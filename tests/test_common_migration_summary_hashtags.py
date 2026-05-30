from __future__ import annotations

import json
import sqlite3

from research_mirror_common.hashtags import HashtagBuilder
from research_mirror_common.migrate import LegacyMigrator
from research_mirror_common.summary import SummaryClient, build_summary_prompt, parse_json_object, summary_system_prompt


def test_parse_json_object_accepts_plain_and_wrapped_json() -> None:
    assert parse_json_object('{"headline":"H","bullets":["A"]}') == {"headline": "H", "bullets": ["A"]}
    assert parse_json_object('prefix {"headline":"H","bullets":["A"]} suffix') == {
        "headline": "H",
        "bullets": ["A"],
    }


def test_summary_client_formats_json_response_and_records_prompt_version(monkeypatch, tmp_path) -> None:
    calls: list[dict] = []

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "json": json})

        class Response:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"choices": [{"message": {"content": '{"headline":"H","bullets":["A","B"]}'}}]}

        return Response()

    monkeypatch.setattr("research_mirror_common.summary.httpx.post", fake_post)
    client = SummaryClient(
        provider="auto",
        api_key="key",
        base_url="https://api.example.test/openai/v1",
        models="model-a,model-b",
        prompt_profile="alfa",
        prompt_version="v-test",
    )

    result = client.build_summary(title="Title", text="Long text with facts")

    assert result.text == "H\n• A\n• B"
    assert result.provider == "llm"
    assert result.model == "model-a"
    assert result.prompt_version == "v-test"
    assert calls[0]["json"]["response_format"] == {"type": "json_object"}


def test_summary_client_retries_without_response_format(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "json": json})

        class Response:
            def __init__(self, status_code: int) -> None:
                self.status_code = status_code

            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"choices": [{"message": {"content": '{"headline":"H","bullets":["A"]}'}}]}

        return Response(400 if "response_format" in json else 200)

    monkeypatch.setattr("research_mirror_common.summary.httpx.post", fake_post)

    result = SummaryClient(provider="auto", api_key="key", base_url="http://localhost:3001/v1", models="auto").build_summary(
        title="Title",
        text="Long text with facts",
    )

    assert result.text == "H\n• A"
    assert calls[0]["json"]["response_format"] == {"type": "json_object"}
    assert "response_format" not in calls[1]["json"]


def test_summary_client_extractive_fallback_when_provider_is_extractive() -> None:
    result = SummaryClient(provider="extractive").build_summary(
        title="Title",
        text="Первое предложение с цифрой 10%. Второе предложение про EBITDA. Третье предложение.",
    )

    assert result.provider == "extractive"
    assert "Первое предложение" in result.text


def test_build_summary_prompt_uses_source_specific_rules() -> None:
    mozgovik_prompt = build_summary_prompt(title="T", text="Body", prompt_profile="mozgovik")
    euler_prompt = build_summary_prompt(title="T", text="Body", prompt_profile="euler")

    assert "Smart-Lab/Mozgovik" in mozgovik_prompt
    assert "ISIN" in mozgovik_prompt
    assert "risk factors" in euler_prompt
    assert "Telegram-саммари" in summary_system_prompt()


def test_build_summary_prompt_has_standard_investor_rules_for_all_profiles() -> None:
    for profile in ("generic", "d8", "alfa", "rencap", "alenka", "mozgovik", "euler"):
        prompt = build_summary_prompt(title="T", text="Body", prompt_profile=profile)

        assert "для опытного инвестора" in prompt
        assert "вывод автора, если он прямо сформулирован" in prompt
        assert "целевые цены и дивдоходности, если они указаны" in prompt
        assert "сложные формулировки" in prompt
        assert "что изменилось для компании, сектора или рынка" in prompt


def test_hashtag_builder_orders_thematic_tags_before_tickers_and_dedupes() -> None:
    tags = ["#SBER", "#макро", "#SBER", "#облигации", "#идея", "#валюта"]

    assert HashtagBuilder().order(tags) == ["#макро", "#облигации", "#валюта", "#SBER", "#идея"]


def test_legacy_migrator_imports_alfa_delivery_idempotently(tmp_path) -> None:
    legacy = tmp_path / "alfa.db"
    target = tmp_path / "research_mirror.db"
    with sqlite3.connect(legacy) as connection:
        connection.executescript(
            """
            CREATE TABLE bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE deliveries (
                chat_id TEXT NOT NULL,
                material_uid TEXT NOT NULL,
                message_id INTEGER,
                document_message_id INTEGER,
                content_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, material_uid)
            );
            INSERT INTO bot_state VALUES ('last_successful_scan', '2026-05-20T00:00:00+00:00', '2026-05-20T00:00:01+00:00');
            INSERT INTO deliveries VALUES ('-100alfa', 'm1', 101, 102, 'hash', '2026-05-20T00:00:02+00:00');
            """
        )

    migrator = LegacyMigrator(target)
    first = migrator.import_source("alfa", legacy)
    second = migrator.import_source("alfa", legacy)

    assert first.rows["deliveries"] == 1
    assert second.rows["deliveries"] == 1
    with sqlite3.connect(target) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM deliveries WHERE source_id = 'alfa'").fetchall()
        state = connection.execute("SELECT value FROM bot_state WHERE source_id = 'alfa' AND key = 'last_successful_scan'").fetchone()
    assert len(rows) == 1
    assert rows[0]["external_id"] == "m1"
    assert rows[0]["channel_message_id"] == 101
    assert state["value"] == "2026-05-20T00:00:00+00:00"


def test_legacy_migrator_imports_euler_subscribers_and_material_json(tmp_path) -> None:
    legacy = tmp_path / "euler.db"
    target = tmp_path / "research_mirror.db"
    material_json = json.dumps({"uid": "pub-1"}, ensure_ascii=False)
    with sqlite3.connect(legacy) as connection:
        connection.executescript(
            """
            CREATE TABLE subscribers (
                chat_id INTEGER PRIMARY KEY,
                chat_type TEXT,
                username TEXT,
                first_name TEXT,
                is_active INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE deliveries (
                chat_id INTEGER NOT NULL,
                publication_uid TEXT NOT NULL,
                status TEXT NOT NULL,
                summary_message_id INTEGER,
                document_message_id INTEGER,
                error TEXT,
                material_json TEXT,
                delivered_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, publication_uid)
            );
            INSERT INTO subscribers VALUES (7, 'private', 'user', 'First', 1, 'created', 'updated');
            """
        )
        connection.execute(
            """
            INSERT INTO deliveries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (7, "pub-1", "summary_pending", 201, None, None, material_json, "delivered", "updated"),
        )

    result = LegacyMigrator(target).import_source("euler", legacy)

    assert result.rows["subscribers"] == 1
    with sqlite3.connect(target) as connection:
        connection.row_factory = sqlite3.Row
        delivery = connection.execute("SELECT * FROM deliveries WHERE source_id = 'euler'").fetchone()
        subscriber = connection.execute("SELECT * FROM subscribers WHERE source_id = 'euler'").fetchone()
    assert delivery["status"] == "summary_pending"
    assert delivery["material_json"] == material_json
    assert subscriber["chat_id"] == "7"
