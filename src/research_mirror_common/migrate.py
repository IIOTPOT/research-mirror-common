from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any

from .audit import build_audit_snapshot
from .store import CommonStore, DEFAULT_CHANNEL_ID, deserialize_json, serialize_json, utcnow


HOME = Path(os.getenv("HOME", "~")).expanduser()
DEFAULT_TARGET = Path(os.getenv("RESEARCH_MIRROR_DB", "storage/research_mirror.db")).expanduser()
DEFAULT_LEGACY_DBS = {
    "d8": HOME / "Documents" / "Mirror_D8" / "storage" / "bot.db",
    "alfa": HOME / "Documents" / "AlfaParser" / "storage" / "bot.db",
    "rencap": HOME / "Documents" / "RenCapParser" / "storage" / "bot.db",
    "alenka": HOME / "Documents" / "AlenkaParser" / "storage" / "bot.db",
    "mozgovik": HOME / "Documents" / "MozgovikParser" / "storage" / "bot.db",
    "euler": HOME / "SVCB" / "Playground" / "projects" / "euler_telegram_bot" / "storage" / "euler-telegram-bot" / "bot.db",
}


@dataclass(frozen=True)
class MigrationResult:
    source_id: str
    legacy_path: Path
    backup_path: Path | None
    rows: dict[str, int]
    status: str


class LegacyMigrator:
    def __init__(self, target: str | Path = DEFAULT_TARGET, *, backup_dir: str | Path | None = None) -> None:
        self.target = Path(target)
        self.backup_dir = Path(backup_dir) if backup_dir is not None else self.target.parent / "backups"

    def import_source(self, source_id: str, legacy_path: str | Path, *, backup: bool = True) -> MigrationResult:
        legacy = Path(legacy_path)
        if not legacy.exists():
            result = MigrationResult(source_id, legacy, None, {}, "missing")
            self._record_run(result)
            return result

        backup_path = self._backup(source_id, legacy) if backup else None
        rows: dict[str, int] = {}
        store = CommonStore(self.target, source_id=source_id)
        try:
            with sqlite3.connect(legacy) as source:
                source.row_factory = sqlite3.Row
                rows["bot_state"] = self._import_bot_state(store, source)
                if source_id == "d8":
                    rows.update(self._import_d8(store, source))
                elif source_id in {"alfa", "rencap"}:
                    rows.update(self._import_delivery_only(store, source, source_id=source_id))
                elif source_id == "euler":
                    rows.update(self._import_euler(store, source))
                elif source_id in {"alenka", "mozgovik"}:
                    rows.update(self._import_article_comment_source(store, source, source_id=source_id))
                else:
                    raise ValueError(f"Unsupported source_id: {source_id}")
            result = MigrationResult(source_id, legacy, backup_path, rows, "ok")
        except Exception:
            store.close()
            raise
        store.close()
        self._record_run(result)
        return result

    def import_all(self, legacy_dbs: dict[str, Path] | None = None) -> list[MigrationResult]:
        return [
            self.import_source(source_id, legacy_path)
            for source_id, legacy_path in (legacy_dbs or DEFAULT_LEGACY_DBS).items()
        ]

    def audit(self, legacy_dbs: dict[str, Path] | None = None) -> dict[str, dict[str, int]]:
        legacy_dbs = legacy_dbs or DEFAULT_LEGACY_DBS
        result: dict[str, dict[str, int]] = {}
        with sqlite3.connect(self.target) as target:
            for source_id, legacy_path in legacy_dbs.items():
                legacy_counts = _legacy_counts(source_id, legacy_path)
                common_counts = {
                    "bot_state": _count(target, "bot_state", source_id),
                    "materials": _count(target, "materials", source_id),
                    "deliveries": _count(target, "deliveries", source_id),
                    "comments": _count(target, "comments", source_id),
                    "summaries": _count(target, "summaries", source_id),
                    "subscribers": _count(target, "subscribers", source_id),
                }
                result[source_id] = {f"legacy_{k}": v for k, v in legacy_counts.items()}
                result[source_id].update({f"common_{k}": v for k, v in common_counts.items()})
        return result

    def preflight(self, source_id: str, legacy_path: str | Path) -> dict[str, int | str]:
        legacy = Path(legacy_path)
        if not legacy.exists():
            return {"source_id": source_id, "status": "missing", "legacy_delivered": 0, "common_delivered": 0}
        expected_ids = _legacy_delivered_ids(source_id, legacy)
        with sqlite3.connect(self.target) as target:
            actual = 0
            for external_id, channel_id in expected_ids:
                row = target.execute(
                    """
                    SELECT 1 FROM deliveries
                    WHERE source_id = ? AND external_id = ? AND channel_id = ?
                    LIMIT 1
                    """,
                    (source_id, external_id, channel_id),
                ).fetchone()
                if row:
                    actual += 1
        return {
            "source_id": source_id,
            "status": "ok" if actual == len(expected_ids) else "incomplete",
            "legacy_delivered": len(expected_ids),
            "common_delivered": actual,
        }

    def _backup(self, source_id: str, legacy_path: Path) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = self.backup_dir / f"{source_id}-{utcnow().replace(':', '').replace('+', '')}.db"
        shutil.copy2(legacy_path, backup_path)
        return backup_path

    def _record_run(self, result: MigrationResult) -> None:
        store = CommonStore(self.target, source_id=result.source_id)
        now = utcnow()
        store.connection.execute(
            """
            INSERT INTO migration_runs (
                source_id, legacy_path, backup_path, rows_json, status, started_at, finished_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.source_id,
                str(result.legacy_path),
                str(result.backup_path) if result.backup_path else None,
                json.dumps(result.rows, ensure_ascii=False, sort_keys=True),
                result.status,
                now,
                now,
            ),
        )
        store.connection.commit()
        store.close()

    def _import_bot_state(self, store: CommonStore, source: sqlite3.Connection) -> int:
        if not _table_exists(source, "bot_state"):
            return 0
        rows = source.execute("SELECT key, value FROM bot_state").fetchall()
        for row in rows:
            store.set_state(str(row["key"]), str(row["value"]))
        return len(rows)

    def _import_d8(self, store: CommonStore, source: sqlite3.Connection) -> dict[str, int]:
        if not _table_exists(source, "articles"):
            return {"materials": 0, "deliveries": 0}
        rows = source.execute("SELECT * FROM articles").fetchall()
        deliveries = 0
        for row in rows:
            channel_message_id = _row_int(row, "channel_message_id")
            store.record_article(
                external_id=str(row["external_id"]),
                title=str(row["title"] or ""),
                url=str(row["url"] or ""),
                published_at=row["published_at"],
                content_hash=str(row["content_hash"] or ""),
                status=str(row["status"] or ""),
                channel_message_id=channel_message_id,
                last_error=_row_value(row, "last_error"),
                material_topic=_row_value(row, "material_topic") or "other",
                updated_at=_row_value(row, "updated_at"),
            )
            if channel_message_id is not None:
                deliveries += 1
        return {"materials": len(rows), "deliveries": deliveries}

    def _import_delivery_only(
        self,
        store: CommonStore,
        source: sqlite3.Connection,
        *,
        source_id: str,
    ) -> dict[str, int]:
        if not _table_exists(source, "deliveries"):
            return {"deliveries": 0}
        uid_column = "report_uid" if source_id == "rencap" else "material_uid"
        rows = source.execute("SELECT * FROM deliveries").fetchall()
        for row in rows:
            store.record_delivery(
                chat_id=row["chat_id"],
                external_id=str(row[uid_column]),
                status="sent",
                message_id=_row_int(row, "message_id"),
                document_message_id=_row_int(row, "document_message_id"),
                content_hash=str(row["content_hash"] or ""),
                delivered_at=_row_value(row, "delivered_at") or _row_value(row, "updated_at"),
                updated_at=_row_value(row, "updated_at"),
            )
        return {"deliveries": len(rows)}

    def _import_euler(self, store: CommonStore, source: sqlite3.Connection) -> dict[str, int]:
        deliveries = 0
        subscribers = 0
        if _table_exists(source, "deliveries"):
            rows = source.execute("SELECT * FROM deliveries").fetchall()
            for row in rows:
                store.record_delivery(
                    chat_id=row["chat_id"],
                    publication_uid=str(row["publication_uid"]),
                    status=str(row["status"] or "sent"),
                    summary_message_id=_row_int(row, "summary_message_id"),
                    document_message_id=_row_int(row, "document_message_id"),
                    error=_row_value(row, "error"),
                    material_json=_row_value(row, "material_json"),
                    delivered_at=_row_value(row, "delivered_at"),
                    updated_at=_row_value(row, "updated_at"),
                )
            deliveries = len(rows)
        if _table_exists(source, "subscribers"):
            rows = source.execute("SELECT * FROM subscribers").fetchall()
            for row in rows:
                if int(row["is_active"] or 0):
                    store.subscribe_chat(
                        chat_id=int(row["chat_id"]),
                        chat_type=_row_value(row, "chat_type"),
                        username=_row_value(row, "username"),
                        first_name=_row_value(row, "first_name"),
                    )
                else:
                    store.deactivate_chat(int(row["chat_id"]))
            subscribers = len(rows)
        return {"deliveries": deliveries, "subscribers": subscribers}

    def _import_article_comment_source(
        self,
        store: CommonStore,
        source: sqlite3.Connection,
        *,
        source_id: str,
    ) -> dict[str, int]:
        counts = {"materials": 0, "comments": 0, "summaries": 0, "pending_overflow": 0}
        if _table_exists(source, "articles"):
            rows = source.execute("SELECT * FROM articles").fetchall()
            for row in rows:
                raw: dict[str, Any] = {}
                if _row_value(row, "comments_last_scanned_at"):
                    raw["comments_last_scanned_at"] = _row_value(row, "comments_last_scanned_at")
                store.record_article(
                    external_id=str(row["external_id"]),
                    title=str(row["title"] or ""),
                    url=str(row["url"] or ""),
                    author=_row_value(row, "author"),
                    published_at=_row_value(row, "published_at"),
                    channel_message_id=_row_int(row, "channel_message_id"),
                    discussion_chat_id=_row_value(row, "discussion_chat_id"),
                    message_thread_id=_row_int(row, "message_thread_id"),
                    discussion_message_id=_row_int(row, "discussion_message_id"),
                    content_hash=str(row["content_hash"] or ""),
                    status="sent" if _row_int(row, "channel_message_id") else None,
                    raw_json=raw or None,
                    updated_at=_row_value(row, "updated_at"),
                )
            counts["materials"] = len(rows)
        if _table_exists(source, "comments"):
            rows = source.execute("SELECT * FROM comments").fetchall()
            for row in rows:
                store.record_comment(
                    external_id=str(row["external_id"]),
                    post_external_id=str(row["post_external_id"]),
                    parent_external_id=_row_value(row, "parent_external_id"),
                    author=str(row["author"] or ""),
                    discussion_message_id=_row_int(row, "discussion_message_id"),
                    highlight_message_id=_row_int(row, "highlight_message_id"),
                    content_hash=str(row["content_hash"] or ""),
                )
            counts["comments"] = len(rows)
        if _table_exists(source, "article_summaries"):
            rows = source.execute("SELECT * FROM article_summaries").fetchall()
            for row in rows:
                store.record_article_summary(
                    external_id=str(row["external_id"]),
                    text_hash=_row_value(row, "text_hash"),
                    summary=str(_row_value(row, "summary") or ""),
                    provider=_row_value(row, "provider"),
                    source_message_id=_row_int(row, "source_message_id"),
                )
            counts["summaries"] = len(rows)
        if source_id == "mozgovik" and _table_exists(source, "pending_overflow"):
            rows = source.execute("SELECT * FROM pending_overflow").fetchall()
            for row in rows:
                image_urls = deserialize_json(row["image_urls_json"]) or []
                store.record_pending_overflow(str(row["external_id"]), _row_int(row, "channel_message_id"), list(image_urls))
            counts["pending_overflow"] = len(rows)
        return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research-mirror-admin")
    sub = parser.add_subparsers(dest="command", required=True)
    migrate_all = sub.add_parser("migrate-all")
    migrate_all.add_argument("--target", default=str(DEFAULT_TARGET))
    migrate_one = sub.add_parser("migrate-source")
    migrate_one.add_argument("--source", required=True)
    migrate_one.add_argument("--legacy-db", required=True)
    migrate_one.add_argument("--target", default=str(DEFAULT_TARGET))
    audit = sub.add_parser("audit")
    audit.add_argument("--target", default=str(DEFAULT_TARGET))
    audit_common = sub.add_parser("audit-common")
    audit_common.add_argument("--target", default=str(DEFAULT_TARGET))
    audit_common.add_argument("--sources", default="")
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--source", required=True)
    preflight.add_argument("--legacy-db", required=True)
    preflight.add_argument("--target", default=str(DEFAULT_TARGET))
    args = parser.parse_args(argv)

    migrator = LegacyMigrator(args.target)
    if args.command == "migrate-all":
        print(json.dumps([_result_dict(result) for result in migrator.import_all()], ensure_ascii=False, indent=2))
        return 0
    if args.command == "migrate-source":
        print(json.dumps(_result_dict(migrator.import_source(args.source, args.legacy_db)), ensure_ascii=False, indent=2))
        return 0
    if args.command == "audit":
        print(json.dumps(migrator.audit(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "audit-common":
        source_ids = [item.strip() for item in str(args.sources or "").split(",") if item.strip()] or None
        print(json.dumps(build_audit_snapshot(args.target, source_ids=source_ids), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "preflight":
        print(json.dumps(migrator.preflight(args.source, args.legacy_db), ensure_ascii=False, indent=2))
        return 0
    parser.error("unknown command")
    return 2


def _result_dict(result: MigrationResult) -> dict[str, Any]:
    return {
        "source_id": result.source_id,
        "legacy_path": str(result.legacy_path),
        "backup_path": str(result.backup_path) if result.backup_path else None,
        "rows": result.rows,
        "status": result.status,
    }


def _legacy_counts(source_id: str, legacy_path: Path) -> dict[str, int]:
    if not legacy_path.exists():
        return {}
    with sqlite3.connect(legacy_path) as connection:
        if source_id in {"d8", "alenka", "mozgovik"}:
            materials_table = "articles"
        else:
            materials_table = ""
        return {
            "bot_state": _legacy_count(connection, "bot_state"),
            "materials": _legacy_count(connection, materials_table) if materials_table else 0,
            "deliveries": _legacy_count(connection, "deliveries"),
            "comments": _legacy_count(connection, "comments"),
            "summaries": _legacy_count(connection, "article_summaries"),
            "subscribers": _legacy_count(connection, "subscribers"),
        }


def _legacy_delivered_ids(source_id: str, legacy_path: Path) -> list[tuple[str, str]]:
    with sqlite3.connect(legacy_path) as connection:
        connection.row_factory = sqlite3.Row
        if source_id == "d8" and _table_exists(connection, "articles"):
            return [
                (str(row["external_id"]), DEFAULT_CHANNEL_ID)
                for row in connection.execute("SELECT external_id FROM articles WHERE channel_message_id IS NOT NULL")
            ]
        if source_id == "alfa" and _table_exists(connection, "deliveries"):
            return [
                (str(row["material_uid"]), str(row["chat_id"]))
                for row in connection.execute("SELECT material_uid, chat_id FROM deliveries")
            ]
        if source_id == "rencap" and _table_exists(connection, "deliveries"):
            return [
                (str(row["report_uid"]), str(row["chat_id"]))
                for row in connection.execute("SELECT report_uid, chat_id FROM deliveries")
            ]
        if source_id == "euler" and _table_exists(connection, "deliveries"):
            return [
                (str(row["publication_uid"]), str(row["chat_id"]))
                for row in connection.execute("SELECT publication_uid, chat_id FROM deliveries")
            ]
        if source_id in {"alenka", "mozgovik"} and _table_exists(connection, "articles"):
            return [
                (str(row["external_id"]), DEFAULT_CHANNEL_ID)
                for row in connection.execute("SELECT external_id FROM articles WHERE channel_message_id IS NOT NULL")
            ]
    return []


def _count(connection: sqlite3.Connection, table: str, source_id: str) -> int:
    if not _table_exists(connection, table):
        return 0
    return int(connection.execute(f"SELECT count(*) FROM {table} WHERE source_id = ?", (source_id,)).fetchone()[0])


def _legacy_count(connection: sqlite3.Connection, table: str) -> int:
    if not table or not _table_exists(connection, table):
        return 0
    return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    return row is not None


def _row_value(row: sqlite3.Row, key: str) -> Any:
    return row[key] if key in row.keys() else None


def _row_int(row: sqlite3.Row, key: str) -> int | None:
    value = _row_value(row, key)
    if value is None or value == "":
        return None
    return int(value)
