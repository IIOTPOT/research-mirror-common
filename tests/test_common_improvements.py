from __future__ import annotations

import json
import logging
import sqlite3

import pytest

from research_mirror_common.audit import build_audit_snapshot
from research_mirror_common.config import ConfigField, validate_config
from research_mirror_common.health import (
    CompactHealthSnapshot,
    HealthStatus,
    TelegramHealthStatusPublisher,
    build_health_message,
    format_compact_health_status,
)
from research_mirror_common.interfaces import SourceCapabilities, SourceConnector
from research_mirror_common.logging import JsonFormatter
from research_mirror_common.registry import SourceRegistry, default_source_registry
from research_mirror_common.store import CommonStore


class DummyConnector(SourceConnector):
    source_id = "dummy"
    capabilities = SourceCapabilities(pdf=True, comments=False, subscribers=True)

    def fetch_entries(self, *, since):
        return [{"external_id": "1", "title": "Title", "published_at": since}]

    def fetch_material(self, entry):
        return {"external_id": entry["external_id"], "title": entry["title"]}


class MemoryState:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_state(self, key: str) -> str | None:
        return self.values.get(key)

    def set_state(self, key: str, value: str) -> None:
        self.values[key] = value


class FakeHealthTelegram:
    def __init__(self, *, edit_error: Exception | None = None) -> None:
        self.edit_error = edit_error
        self.sent: list[dict] = []
        self.edited: list[dict] = []

    def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, "kwargs": kwargs})
        return {"message_id": 101}

    def edit_message_text(self, chat_id, message_id, text, **kwargs):
        self.edited.append({"chat_id": chat_id, "message_id": message_id, "text": text, "kwargs": kwargs})
        if self.edit_error:
            raise self.edit_error
        return {"message_id": message_id}


def test_source_registry_registers_connectors_and_default_sources() -> None:
    registry = SourceRegistry()
    registry.register(DummyConnector())

    assert registry.get("dummy").capabilities.pdf is True
    assert registry.describe()["dummy"]["capabilities"]["subscribers"] is True
    assert {"d8", "alfa", "rencap", "alenka", "mozgovik", "euler"} <= set(default_source_registry().source_ids())


def test_store_schema_version_and_indexes_are_applied(tmp_path) -> None:
    store = CommonStore(tmp_path / "research_mirror.db", source_id="alfa")

    version = store.get_schema_version()
    indexes = {
        row["name"]
        for row in store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    }

    assert version >= 1
    assert "idx_deliveries_source_status_channel" in indexes
    assert "idx_materials_source_published_at" in indexes
    assert "idx_comments_source_post" in indexes


def test_health_message_formats_ok_warn_and_error(tmp_path) -> None:
    store = CommonStore(tmp_path / "research_mirror.db", source_id="rencap")
    store.record_check_success(
        store.parse_datetime_value("2026-05-21T10:00:00+00:00"),
        store.parse_datetime_value("2026-05-21T10:00:10+00:00"),
        reports=2,
        sent=1,
    )

    ok = build_health_message("RenCap", store.get_health_snapshot())
    warn = build_health_message("RenCap", {**store.get_health_snapshot(), "consecutive_failures": 1})
    error = build_health_message("RenCap", {"status": "error", "last_error": "boom", "consecutive_failures": 3})

    assert ok.status == HealthStatus.OK
    assert ok.text.startswith("OK RenCap")
    assert warn.status == HealthStatus.WARN
    assert error.status == HealthStatus.ERROR
    assert "boom" in error.text


def test_compact_health_status_and_publisher_replaces_uneditable_message() -> None:
    store = MemoryState()
    store.set_state("health_status_message_id", "77")
    store.set_state("health_status_chat_id", "-100")
    telegram = FakeHealthTelegram(edit_error=RuntimeError("Bad Request: message to edit not found"))
    publisher = TelegramHealthStatusPublisher(telegram=telegram, store=store, chat_id="-100")

    message_id = publisher.publish(
        format_compact_health_status(
            CompactHealthSnapshot(
                bot_name="Bot",
                status="OK",
                poll_at=None,
                scan_at=None,
                next_at=None,
                change_count=0,
            )
        )
    )

    assert message_id == 101
    assert telegram.edited[0]["message_id"] == 77
    assert telegram.sent[0]["text"].startswith("🟢 Bot OK")
    assert store.get_state("health_status_message_id") == "101"


def test_material_payload_roundtrip_and_audit_snapshot(tmp_path) -> None:
    db_path = tmp_path / "research_mirror.db"
    store = CommonStore(db_path, source_id="euler")
    store.record_check_failure(
        store.parse_datetime_value("2026-05-21T10:00:00+00:00"),
        store.parse_datetime_value("2026-05-21T10:00:20+00:00"),
        "temporary error",
    )
    store.record_delivery(
        chat_id=7,
        publication_uid="pub-1",
        status="summary_pending",
        summary_message_id=100,
        material_json=store.build_material_payload(
            external_id="pub-1",
            title="Title",
            url="https://example.test",
            published_at="2026-05-21T09:00:00+00:00",
            summary="Summary",
            pdf_path="/tmp/file.pdf",
            raw={"uid": "pub-1"},
        ),
    )

    delivery = store.get_delivery(chat_id=7, publication_uid="pub-1")
    payload = store.parse_material_payload(delivery["material_json"])
    audit = build_audit_snapshot(db_path, source_ids=["euler"])

    assert payload["version"] == 1
    assert payload["external_id"] == "pub-1"
    assert audit["sources"]["euler"]["pending_deliveries"] == 1
    assert audit["sources"]["euler"]["status"] == "error"


def test_config_validator_reports_missing_invalid_and_masked_values() -> None:
    result = validate_config(
        {
            "BOT_TOKEN": "123456:secret",
            "CHANNEL_ID": "",
            "INTERVAL": "abc",
        },
        [
            ConfigField("BOT_TOKEN", required=True, secret=True),
            ConfigField("CHANNEL_ID", required=True),
            ConfigField("INTERVAL", required=False, cast=int),
        ],
    )

    assert not result.ok
    assert result.values["BOT_TOKEN"] == "***"
    assert "CHANNEL_ID is required" in result.errors
    assert "INTERVAL must be int" in result.errors


def test_json_formatter_outputs_structured_log_record() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        "research",
        logging.WARNING,
        __file__,
        10,
        "scan failed",
        (),
        None,
    )
    record.source_id = "alfa"

    data = json.loads(formatter.format(record))

    assert data["level"] == "WARNING"
    assert data["message"] == "scan failed"
    assert data["source_id"] == "alfa"


def test_dry_run_fixture_pipeline_uses_connector_contract() -> None:
    connector = DummyConnector()
    entries = connector.fetch_entries(since="2026-05-21T00:00:00+00:00")
    materials = [connector.fetch_material(entry) for entry in entries]

    assert materials == [{"external_id": "1", "title": "Title"}]


def test_audit_cli_outputs_json(tmp_path, capsys) -> None:
    from research_mirror_common.migrate import main

    db_path = tmp_path / "research_mirror.db"
    CommonStore(db_path, source_id="alfa").record_check_success(
        CommonStore.parse_datetime_value("2026-05-21T10:00:00+00:00"),
        CommonStore.parse_datetime_value("2026-05-21T10:00:01+00:00"),
        reports=1,
        sent=1,
    )

    assert main(["audit-common", "--target", str(db_path), "--sources", "alfa"]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["sources"]["alfa"]["status"] == "ok"
