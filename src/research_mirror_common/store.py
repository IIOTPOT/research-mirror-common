from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any


ChatId = int | str
DEFAULT_CHANNEL_ID = "__default__"
DEFAULT_TEXT_HASH = "__default__"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Subscriber:
    chat_id: int
    chat_type: str | None
    username: str | None
    first_name: str | None
    is_active: bool


class CommonStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        source_id: str,
        default_channel_id: ChatId | None = DEFAULT_CHANNEL_ID,
    ) -> None:
        self.db_path = Path(db_path)
        self.source_id = _required_text(source_id, "source_id")
        self.default_channel_id = _text_channel_id(default_channel_id)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=30000")
        self._init_schema()

    def close(self) -> None:
        self.connection.close()

    def _init_schema(self) -> None:
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=30000;

            CREATE TABLE IF NOT EXISTS bot_state (
                source_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source_id, key)
            );

            CREATE TABLE IF NOT EXISTS materials (
                source_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                author TEXT,
                published_at TEXT,
                full_text TEXT,
                summary_text TEXT,
                material_topic TEXT,
                content_hash TEXT,
                raw_json TEXT,
                pdf_path TEXT,
                status TEXT,
                channel_message_id INTEGER,
                discussion_chat_id TEXT,
                message_thread_id INTEGER,
                discussion_message_id INTEGER,
                last_error TEXT,
                created_at TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source_id, external_id)
            );

            CREATE TABLE IF NOT EXISTS deliveries (
                source_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'sent',
                channel_message_id INTEGER,
                discussion_chat_id TEXT,
                discussion_message_id INTEGER,
                message_thread_id INTEGER,
                document_message_id INTEGER,
                content_hash TEXT,
                error TEXT,
                material_json TEXT,
                delivered_at TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source_id, channel_id, external_id)
            );

            CREATE TABLE IF NOT EXISTS comments (
                source_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                post_external_id TEXT NOT NULL,
                parent_external_id TEXT,
                author TEXT NOT NULL DEFAULT '',
                discussion_message_id INTEGER,
                highlight_message_id INTEGER,
                content_hash TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source_id, external_id)
            );

            CREATE TABLE IF NOT EXISTS summaries (
                source_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                text_hash TEXT NOT NULL DEFAULT '__default__',
                provider TEXT,
                model TEXT,
                prompt_version TEXT,
                formatted_text TEXT NOT NULL,
                source_message_id INTEGER,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source_id, external_id, text_hash)
            );

            CREATE TABLE IF NOT EXISTS subscribers (
                source_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                chat_type TEXT,
                username TEXT,
                first_name TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source_id, chat_id)
            );

            CREATE TABLE IF NOT EXISTS pending_overflow (
                source_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                channel_message_id INTEGER,
                image_urls_json TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source_id, external_id)
            );

            CREATE TABLE IF NOT EXISTS migration_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                legacy_path TEXT NOT NULL,
                backup_path TEXT,
                rows_json TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );

            CREATE VIEW IF NOT EXISTS articles AS
            SELECT
                external_id,
                title,
                url,
                published_at,
                content_hash,
                COALESCE(status, 'sent') AS status,
                channel_message_id,
                last_error,
                COALESCE(material_topic, 'other') AS material_topic,
                COALESCE(created_at, updated_at) AS created_at,
                updated_at
            FROM materials;
            """
        )
        self._apply_schema_migrations()
        self.connection.commit()

    def _apply_schema_migrations(self) -> None:
        self.connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_deliveries_source_status_channel
                ON deliveries (source_id, status, channel_id);
            CREATE INDEX IF NOT EXISTS idx_materials_source_published_at
                ON materials (source_id, published_at);
            CREATE INDEX IF NOT EXISTS idx_comments_source_post
                ON comments (source_id, post_external_id);
            """
        )
        self.connection.execute(
            """
            INSERT INTO schema_migrations (version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (SCHEMA_VERSION, utcnow()),
        )

    def get_schema_version(self) -> int:
        row = self.connection.execute("SELECT max(version) AS version FROM schema_migrations").fetchone()
        return optional_int(row["version"] if row else None) or 0

    def get_state(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM bot_state WHERE source_id = ? AND key = ?",
            (self.source_id, key),
        ).fetchone()
        return str(row["value"]) if row else None

    def set_state(self, key: str, value: str) -> None:
        now = utcnow()
        self.connection.execute(
            """
            INSERT INTO bot_state (source_id, key, value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_id, key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (self.source_id, key, str(value), now),
        )
        self.connection.commit()

    def get_state_value(self, key: str) -> str | None:
        return self.get_state(key)

    def set_state_value(self, key: str, value: str) -> None:
        self.set_state(key, value)

    def get_update_offset(self) -> int | None:
        value = self.get_state("telegram_update_offset")
        return int(value) if value not in {None, ""} else None

    def set_update_offset(self, offset: int) -> None:
        self.set_state("telegram_update_offset", str(offset))

    def get_last_successful_scan(self) -> datetime | None:
        return parse_datetime(self.get_state("last_successful_scan"))

    def set_last_successful_scan(self, value: datetime) -> None:
        self.set_state("last_successful_scan", value.astimezone(UTC).isoformat())

    def record_check_started(self, started_at: datetime) -> None:
        self.set_state("last_check_started_at", started_at.astimezone(UTC).isoformat())
        self.set_state("last_check_finished_at", "")
        self.set_state("last_check_status", "running")
        self.set_state("last_check_error", "")

    def record_check_success(self, started_at: datetime, finished_at: datetime, *, reports: int, sent: int) -> None:
        self.set_state("last_check_started_at", started_at.astimezone(UTC).isoformat())
        self.set_state("last_check_finished_at", finished_at.astimezone(UTC).isoformat())
        self.set_state("last_check_status", "ok")
        self.set_state("last_check_error", "")
        self.set_state("last_reports_found", str(max(0, reports)))
        self.set_state("last_reports_sent", str(max(0, sent)))
        self.set_state("consecutive_failures", "0")

    def record_check_failure(self, started_at: datetime, finished_at: datetime, error: BaseException | str) -> None:
        failures = _safe_int(self.get_state("consecutive_failures"), default=0) + 1
        self.set_state("last_check_started_at", started_at.astimezone(UTC).isoformat())
        self.set_state("last_check_finished_at", finished_at.astimezone(UTC).isoformat())
        self.set_state("last_check_status", "error")
        self.set_state("last_check_error", _format_error(error))
        self.set_state("consecutive_failures", str(failures))

    def get_health_snapshot(self) -> dict[str, Any]:
        return {
            "status": self.get_state("last_check_status") or "unknown",
            "last_check_started_at": parse_datetime(self.get_state("last_check_started_at")),
            "last_check_finished_at": parse_datetime(self.get_state("last_check_finished_at")),
            "last_successful_scan": self.get_last_successful_scan(),
            "last_error": self.get_state("last_check_error") or None,
            "reports": optional_int(self.get_state("last_reports_found")),
            "sent": optional_int(self.get_state("last_reports_sent")),
            "consecutive_failures": _safe_int(self.get_state("consecutive_failures"), default=0),
        }

    @staticmethod
    def parse_datetime_value(value: object) -> datetime:
        parsed = parse_datetime(value)
        if parsed is None:
            raise ValueError(f"Invalid datetime value: {value!r}")
        return parsed

    def record_delivery(
        self,
        *,
        chat_id: ChatId | None = None,
        material_uid: str | None = None,
        report_uid: str | None = None,
        publication_uid: str | None = None,
        external_id: str | None = None,
        status: str = "sent",
        message_id: int | None = None,
        channel_message_id: int | None = None,
        summary_message_id: int | None = None,
        document_message_id: int | None = None,
        discussion_chat_id: ChatId | None = None,
        discussion_message_id: int | None = None,
        message_thread_id: int | None = None,
        content_hash: str | None = None,
        error: str | None = None,
        material_json: dict | str | None = None,
        delivered_at: str | None = None,
        updated_at: str | None = None,
    ) -> None:
        uid = _delivery_uid(
            external_id=external_id,
            material_uid=material_uid,
            report_uid=report_uid,
            publication_uid=publication_uid,
        )
        channel = self._channel_id(chat_id)
        now = updated_at or utcnow()
        delivered = delivered_at or now
        stored_message_id = summary_message_id if summary_message_id is not None else channel_message_id
        stored_message_id = stored_message_id if stored_message_id is not None else message_id
        self.connection.execute(
            """
            INSERT INTO deliveries (
                source_id, channel_id, external_id, status, channel_message_id, discussion_chat_id,
                discussion_message_id, message_thread_id, document_message_id, content_hash, error,
                material_json, delivered_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, channel_id, external_id) DO UPDATE SET
                status = excluded.status,
                channel_message_id = COALESCE(excluded.channel_message_id, deliveries.channel_message_id),
                discussion_chat_id = COALESCE(excluded.discussion_chat_id, deliveries.discussion_chat_id),
                discussion_message_id = COALESCE(excluded.discussion_message_id, deliveries.discussion_message_id),
                message_thread_id = COALESCE(excluded.message_thread_id, deliveries.message_thread_id),
                document_message_id = COALESCE(excluded.document_message_id, deliveries.document_message_id),
                content_hash = COALESCE(excluded.content_hash, deliveries.content_hash),
                error = excluded.error,
                material_json = COALESCE(excluded.material_json, deliveries.material_json),
                updated_at = excluded.updated_at
            """,
            (
                self.source_id,
                channel,
                uid,
                status or "sent",
                stored_message_id,
                _optional_text(discussion_chat_id),
                discussion_message_id,
                message_thread_id,
                document_message_id,
                content_hash,
                error,
                serialize_json(material_json),
                delivered,
                now,
            ),
        )
        self.connection.commit()

    def build_material_payload(
        self,
        *,
        external_id: str,
        title: str,
        url: str,
        published_at: datetime | str | None = None,
        summary: str | None = None,
        pdf_path: str | Path | None = None,
        raw: dict | list | str | None = None,
        version: int = 1,
    ) -> dict[str, Any]:
        return build_material_payload(
            source_id=self.source_id,
            external_id=external_id,
            title=title,
            url=url,
            published_at=published_at,
            summary=summary,
            pdf_path=pdf_path,
            raw=raw,
            version=version,
        )

    def parse_material_payload(self, value: dict | str | None) -> dict[str, Any]:
        return parse_material_payload(value, source_id=self.source_id)

    def get_delivery(
        self,
        *,
        chat_id: ChatId | None = None,
        material_uid: str | None = None,
        report_uid: str | None = None,
        publication_uid: str | None = None,
        external_id: str | None = None,
    ) -> dict | None:
        uid = _delivery_uid(
            external_id=external_id,
            material_uid=material_uid,
            report_uid=report_uid,
            publication_uid=publication_uid,
        )
        row = self.connection.execute(
            """
            SELECT *
            FROM deliveries
            WHERE source_id = ? AND channel_id = ? AND external_id = ?
            """,
            (self.source_id, self._channel_id(chat_id), uid),
        ).fetchone()
        return self._delivery_dict(row) if row else None

    def was_delivered(
        self,
        *,
        chat_id: ChatId | None = None,
        material_uid: str | None = None,
        report_uid: str | None = None,
        publication_uid: str | None = None,
        external_id: str | None = None,
    ) -> bool:
        delivery = self.get_delivery(
            chat_id=chat_id,
            material_uid=material_uid,
            report_uid=report_uid,
            publication_uid=publication_uid,
            external_id=external_id,
        )
        if not delivery:
            return False
        if publication_uid is not None:
            return str(delivery.get("status") or "") in {"sent", "summary_only", "summary_pending"}
        return True

    def get_delivery_summary_message_id(self, *, chat_id: ChatId, publication_uid: str) -> int | None:
        delivery = self.get_delivery(chat_id=chat_id, publication_uid=publication_uid)
        if not delivery or delivery.get("status") not in {"sent", "summary_only", "summary_pending"}:
            return None
        return optional_int(delivery.get("summary_message_id"))

    def list_retryable_deliveries(self, *, chat_ids: list[ChatId] | None = None) -> list[dict]:
        return self._list_deliveries_by_status(
            statuses=["summary_only"],
            chat_ids=chat_ids,
            require_summary=True,
            require_material_json=True,
            exclude_errors={"PDF is unavailable", "PDF exceeds Telegram document limit"},
        )

    def list_pending_summary_deliveries(self, *, chat_ids: list[ChatId] | None = None) -> list[dict]:
        return self._list_deliveries_by_status(
            statuses=["summary_pending"],
            chat_ids=chat_ids,
            require_summary=True,
            require_material_json=True,
        )

    def list_retaggable_deliveries(self, *, chat_id: ChatId, limit: int | None = None) -> list[dict]:
        rows = self._list_deliveries_by_status(
            statuses=["sent", "summary_only", "summary_pending"],
            chat_ids=[chat_id],
            require_summary=True,
            require_material_json=True,
        )
        return rows[: max(0, int(limit))] if limit is not None else rows

    def is_article_delivered(self, external_id: str, content_hash: str) -> bool:
        row = self.connection.execute(
            """
            SELECT 1 FROM materials
            WHERE source_id = ? AND external_id = ? AND content_hash = ? AND status = 'delivered'
            """,
            (self.source_id, str(external_id), str(content_hash)),
        ).fetchone()
        return row is not None

    def record_article(
        self,
        *,
        external_id: str,
        title: str,
        url: str,
        author: str | None = None,
        published_at: datetime | str | None = None,
        channel_message_id: int | None = None,
        discussion_chat_id: ChatId | None = None,
        message_thread_id: int | None = None,
        discussion_message_id: int | None = None,
        content_hash: str,
        status: str | None = None,
        last_error: str | None = None,
        material_topic: str | None = None,
        full_text: str | None = None,
        summary_text: str | None = None,
        raw_json: dict | str | None = None,
        pdf_path: str | Path | None = None,
        updated_at: str | None = None,
    ) -> None:
        now = updated_at or utcnow()
        created = now
        published = format_datetime(published_at)
        self.connection.execute(
            """
            INSERT INTO materials (
                source_id, external_id, title, url, author, published_at, full_text, summary_text,
                material_topic, content_hash, raw_json, pdf_path, status, channel_message_id,
                discussion_chat_id, message_thread_id, discussion_message_id, last_error, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, external_id) DO UPDATE SET
                title = excluded.title,
                url = excluded.url,
                author = COALESCE(excluded.author, materials.author),
                published_at = excluded.published_at,
                full_text = COALESCE(excluded.full_text, materials.full_text),
                summary_text = COALESCE(excluded.summary_text, materials.summary_text),
                material_topic = COALESCE(excluded.material_topic, materials.material_topic),
                content_hash = excluded.content_hash,
                raw_json = COALESCE(excluded.raw_json, materials.raw_json),
                pdf_path = COALESCE(excluded.pdf_path, materials.pdf_path),
                status = COALESCE(excluded.status, materials.status),
                channel_message_id = COALESCE(excluded.channel_message_id, materials.channel_message_id),
                discussion_chat_id = COALESCE(excluded.discussion_chat_id, materials.discussion_chat_id),
                message_thread_id = COALESCE(excluded.message_thread_id, materials.message_thread_id),
                discussion_message_id = COALESCE(excluded.discussion_message_id, materials.discussion_message_id),
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            (
                self.source_id,
                str(external_id),
                str(title or ""),
                str(url or ""),
                author,
                published,
                full_text,
                summary_text,
                material_topic,
                content_hash,
                serialize_json(raw_json),
                str(pdf_path) if pdf_path is not None else None,
                status,
                channel_message_id,
                _optional_text(discussion_chat_id),
                message_thread_id,
                discussion_message_id,
                last_error,
                created,
                now,
            ),
        )
        if channel_message_id is not None and status in {None, "delivered", "sent"}:
            self.record_delivery(
                external_id=str(external_id),
                chat_id=self.default_channel_id,
                status="sent",
                message_id=channel_message_id,
                discussion_chat_id=discussion_chat_id,
                discussion_message_id=discussion_message_id,
                message_thread_id=message_thread_id,
                content_hash=content_hash,
            )
        else:
            self.connection.commit()

    def get_article_message(self, external_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM materials WHERE source_id = ? AND external_id = ?",
            (self.source_id, str(external_id)),
        ).fetchone()
        return self._material_dict(row) if row else None

    def list_articles(self) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM materials
            WHERE source_id = ?
            ORDER BY COALESCE(published_at, ''), COALESCE(channel_message_id, 0), external_id
            """,
            (self.source_id,),
        ).fetchall()
        return [self._material_dict(row) for row in rows]

    def record_article_summary(
        self,
        *,
        external_id: str,
        summary: str,
        text_hash: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        source_message_id: int | None = None,
    ) -> None:
        now = utcnow()
        self.connection.execute(
            """
            INSERT INTO summaries (
                source_id, external_id, text_hash, provider, model, prompt_version,
                formatted_text, source_message_id, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, external_id, text_hash) DO UPDATE SET
                provider = excluded.provider,
                model = excluded.model,
                prompt_version = excluded.prompt_version,
                formatted_text = excluded.formatted_text,
                source_message_id = excluded.source_message_id,
                updated_at = excluded.updated_at
            """,
            (
                self.source_id,
                str(external_id),
                text_hash or DEFAULT_TEXT_HASH,
                provider,
                model,
                prompt_version,
                summary,
                source_message_id,
                now,
            ),
        )
        self.connection.commit()

    def get_article_summary(self, external_id: str, text_hash: str | None = None) -> dict | None:
        row = self.connection.execute(
            """
            SELECT * FROM summaries
            WHERE source_id = ? AND external_id = ? AND text_hash = ?
            """,
            (self.source_id, str(external_id), text_hash or DEFAULT_TEXT_HASH),
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["summary"] = item["formatted_text"]
        return item

    def record_comment(
        self,
        *,
        external_id: str,
        post_external_id: str,
        author: str,
        discussion_message_id: int | None,
        content_hash: str,
        parent_external_id: str | None = None,
        highlight_message_id: int | None = None,
    ) -> None:
        now = utcnow()
        self.connection.execute(
            """
            INSERT INTO comments (
                source_id, external_id, post_external_id, parent_external_id, author,
                discussion_message_id, highlight_message_id, content_hash, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, external_id) DO UPDATE SET
                post_external_id = excluded.post_external_id,
                parent_external_id = excluded.parent_external_id,
                author = excluded.author,
                discussion_message_id = COALESCE(excluded.discussion_message_id, comments.discussion_message_id),
                highlight_message_id = COALESCE(excluded.highlight_message_id, comments.highlight_message_id),
                content_hash = excluded.content_hash,
                updated_at = excluded.updated_at
            """,
            (
                self.source_id,
                str(external_id),
                str(post_external_id),
                parent_external_id,
                str(author or ""),
                discussion_message_id,
                highlight_message_id,
                content_hash,
                now,
            ),
        )
        self.connection.commit()

    def get_comment(self, external_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM comments WHERE source_id = ? AND external_id = ?",
            (self.source_id, str(external_id)),
        ).fetchone()
        return dict(row) if row else None

    def list_comment_ids(self) -> list[str]:
        rows = self.connection.execute(
            "SELECT external_id FROM comments WHERE source_id = ? ORDER BY external_id",
            (self.source_id,),
        ).fetchall()
        return [str(row["external_id"]) for row in rows]

    def get_previous_comment_by_author(self, *, post_external_id: str, author: str, before_external_id: str) -> dict | None:
        before_numeric = optional_int(before_external_id)
        if before_numeric is not None:
            row = self.connection.execute(
                """
                SELECT * FROM comments
                WHERE source_id = ?
                  AND post_external_id = ?
                  AND author = ?
                  AND discussion_message_id IS NOT NULL
                  AND external_id <> ''
                  AND external_id NOT GLOB '*[^0-9]*'
                  AND CAST(external_id AS INTEGER) < ?
                ORDER BY CAST(external_id AS INTEGER) DESC
                LIMIT 1
                """,
                (self.source_id, post_external_id, author, before_numeric),
            ).fetchone()
            if row:
                return dict(row)
        row = self.connection.execute(
            """
            SELECT * FROM comments
            WHERE source_id = ?
              AND post_external_id = ?
              AND author = ?
              AND external_id <> ?
              AND discussion_message_id IS NOT NULL
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (self.source_id, post_external_id, author, before_external_id),
        ).fetchone()
        return dict(row) if row else None

    def record_pending_overflow(self, external_id: str, channel_message_id: int | None, image_urls: list[str]) -> None:
        now = utcnow()
        self.connection.execute(
            """
            INSERT INTO pending_overflow (
                source_id, external_id, channel_message_id, image_urls_json, attempts, last_error, updated_at
            )
            VALUES (?, ?, ?, ?, 0, NULL, ?)
            ON CONFLICT(source_id, external_id) DO UPDATE SET
                channel_message_id = excluded.channel_message_id,
                image_urls_json = excluded.image_urls_json,
                updated_at = excluded.updated_at
            """,
            (self.source_id, str(external_id), channel_message_id, json.dumps(image_urls, ensure_ascii=False), now),
        )
        self.connection.commit()

    def list_pending_overflow(self) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT external_id, channel_message_id, image_urls_json, attempts, last_error, updated_at
            FROM pending_overflow
            WHERE source_id = ?
            ORDER BY updated_at
            """,
            (self.source_id,),
        ).fetchall()
        result: list[dict] = []
        for row in rows:
            item = dict(row)
            try:
                item["image_urls"] = list(json.loads(item.pop("image_urls_json")))
            except Exception:
                item["image_urls"] = []
            result.append(item)
        return result

    def mark_overflow_sent(self, external_id: str) -> None:
        self.connection.execute(
            "DELETE FROM pending_overflow WHERE source_id = ? AND external_id = ?",
            (self.source_id, str(external_id)),
        )
        self.connection.commit()

    def mark_overflow_failed(self, external_id: str, error: str) -> None:
        self.connection.execute(
            """
            UPDATE pending_overflow
            SET attempts = attempts + 1, last_error = ?, updated_at = ?
            WHERE source_id = ? AND external_id = ?
            """,
            (error, utcnow(), self.source_id, str(external_id)),
        )
        self.connection.commit()

    def mark_comments_scanned(self, external_id: str, scanned_at: str) -> None:
        row = self.connection.execute(
            "SELECT raw_json FROM materials WHERE source_id = ? AND external_id = ?",
            (self.source_id, str(external_id)),
        ).fetchone()
        raw = deserialize_json(row["raw_json"]) if row else None
        if not isinstance(raw, dict):
            raw = {}
        raw["comments_last_scanned_at"] = scanned_at
        self.connection.execute(
            """
            UPDATE materials
            SET raw_json = ?,
                updated_at = ?
            WHERE source_id = ? AND external_id = ?
            """,
            (serialize_json(raw), utcnow(), self.source_id, str(external_id)),
        )
        self.connection.commit()

    def list_articles_for_comment_refresh(
        self,
        *,
        now: datetime,
        refresh_window_hours: int,
        early_hours: int,
        mid_hours: int,
        early_interval_seconds: int,
        mid_interval_seconds: int,
        late_interval_seconds: int,
        limit: int,
    ) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM materials
            WHERE source_id = ? AND channel_message_id IS NOT NULL
            ORDER BY published_at DESC
            """,
            (self.source_id,),
        ).fetchall()
        due: list[dict] = []
        for row in rows:
            item = self._material_dict(row)
            published_at = parse_datetime(item.get("published_at"))
            if published_at is None:
                continue
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=now.tzinfo)
            age_seconds = (now - published_at.astimezone(now.tzinfo)).total_seconds()
            if age_seconds < 0 or age_seconds > refresh_window_hours * 3600:
                continue
            interval = late_interval_seconds
            if age_seconds <= early_hours * 3600:
                interval = early_interval_seconds
            elif age_seconds <= mid_hours * 3600:
                interval = mid_interval_seconds
            last_scanned = _comments_last_scanned(item)
            if last_scanned is not None:
                if last_scanned.tzinfo is None:
                    last_scanned = last_scanned.replace(tzinfo=now.tzinfo)
                if (now - last_scanned.astimezone(now.tzinfo)).total_seconds() < interval:
                    continue
            due.append(item)
            if len(due) >= limit:
                break
        return due

    def subscribe_chat(self, *, chat_id: int, chat_type: str | None, username: str | None, first_name: str | None) -> None:
        now = utcnow()
        self.connection.execute(
            """
            INSERT INTO subscribers (source_id, chat_id, chat_type, username, first_name, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(source_id, chat_id) DO UPDATE SET
                chat_type = excluded.chat_type,
                username = excluded.username,
                first_name = excluded.first_name,
                is_active = 1,
                updated_at = excluded.updated_at
            """,
            (self.source_id, str(chat_id), chat_type, username, first_name, now, now),
        )
        self.connection.commit()

    def deactivate_chat(self, chat_id: int) -> None:
        now = utcnow()
        self.connection.execute(
            """
            INSERT INTO subscribers (source_id, chat_id, chat_type, username, first_name, is_active, created_at, updated_at)
            VALUES (?, ?, NULL, NULL, NULL, 0, ?, ?)
            ON CONFLICT(source_id, chat_id) DO UPDATE SET is_active = 0, updated_at = excluded.updated_at
            """,
            (self.source_id, str(chat_id), now, now),
        )
        self.connection.commit()

    def is_chat_active(self, chat_id: int) -> bool:
        row = self.connection.execute(
            "SELECT is_active FROM subscribers WHERE source_id = ? AND chat_id = ?",
            (self.source_id, str(chat_id)),
        ).fetchone()
        return bool(row and int(row["is_active"]) == 1)

    def list_active_subscribers(self) -> list[Subscriber]:
        rows = self.connection.execute(
            """
            SELECT chat_id, chat_type, username, first_name, is_active
            FROM subscribers
            WHERE source_id = ? AND is_active = 1
            ORDER BY CAST(chat_id AS INTEGER) ASC
            """,
            (self.source_id,),
        ).fetchall()
        return [
            Subscriber(
                chat_id=int(row["chat_id"]),
                chat_type=row["chat_type"],
                username=row["username"],
                first_name=row["first_name"],
                is_active=bool(row["is_active"]),
            )
            for row in rows
        ]

    def _channel_id(self, chat_id: ChatId | None) -> str:
        return _text_channel_id(chat_id if chat_id is not None else self.default_channel_id)

    def _delivery_dict(self, row: sqlite3.Row) -> dict:
        item = dict(row)
        item["chat_id"] = item["channel_id"]
        item["material_uid"] = item["external_id"]
        item["report_uid"] = item["external_id"]
        item["publication_uid"] = item["external_id"]
        item["message_id"] = item["channel_message_id"]
        item["summary_message_id"] = item["channel_message_id"]
        return item

    def _material_dict(self, row: sqlite3.Row) -> dict:
        item = dict(row)
        raw = deserialize_json(item.get("raw_json")) or {}
        item["comments_last_scanned_at"] = raw.get("comments_last_scanned_at")
        return item

    def _list_deliveries_by_status(
        self,
        *,
        statuses: list[str],
        chat_ids: list[ChatId] | None = None,
        require_summary: bool = False,
        require_material_json: bool = False,
        exclude_errors: set[str] | None = None,
    ) -> list[dict]:
        params: list[Any] = [self.source_id, *statuses]
        where = [f"source_id = ?", f"status IN ({', '.join('?' for _ in statuses)})"]
        if chat_ids:
            channels = [_text_channel_id(chat_id) for chat_id in chat_ids]
            where.append(f"channel_id IN ({', '.join('?' for _ in channels)})")
            params.extend(channels)
        if require_summary:
            where.append("channel_message_id IS NOT NULL")
        if require_material_json:
            where.append("material_json IS NOT NULL")
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM deliveries
            WHERE {' AND '.join(where)}
            ORDER BY delivered_at ASC
            """,
            params,
        ).fetchall()
        result = [self._delivery_dict(row) for row in rows]
        if exclude_errors:
            result = [row for row in result if row.get("error") not in exclude_errors]
        return result


def utcnow() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def format_datetime(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def serialize_json(value: dict | list | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def deserialize_json(value: object) -> Any:
    if not value:
        return None
    try:
        return json.loads(str(value))
    except Exception:
        return None


def build_material_payload(
    *,
    source_id: str,
    external_id: str,
    title: str,
    url: str | None,
    published_at: datetime | str | None = None,
    summary: str | None = None,
    pdf_path: str | Path | None = None,
    raw: dict | list | str | None = None,
    version: int = 1,
) -> dict[str, Any]:
    return {
        "version": int(version),
        "source_id": str(source_id),
        "external_id": str(external_id),
        "title": str(title or ""),
        "url": str(url or ""),
        "published_at": format_datetime(published_at),
        "summary": summary,
        "pdf_path": str(pdf_path) if pdf_path is not None else None,
        "raw": raw,
    }


def parse_material_payload(value: dict | str | None, *, source_id: str | None = None) -> dict[str, Any]:
    data = deserialize_json(value) if isinstance(value, str) else value
    if not isinstance(data, dict):
        return {}
    if "version" not in data:
        data = {"version": 0, "source_id": source_id, "raw": data}
    return data


def _delivery_uid(
    *,
    external_id: str | None,
    material_uid: str | None,
    report_uid: str | None,
    publication_uid: str | None,
) -> str:
    return _required_text(external_id or material_uid or report_uid or publication_uid, "delivery external_id")


def _required_text(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _text_channel_id(value: object) -> str:
    text = str(value if value is not None else DEFAULT_CHANNEL_ID).strip()
    return text or DEFAULT_CHANNEL_ID


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_int(value: str | None, *, default: int) -> int:
    parsed = optional_int(value)
    return default if parsed is None else parsed


def _format_error(error: BaseException | str) -> str:
    if isinstance(error, BaseException):
        text = f"{error.__class__.__name__}: {error}"
    else:
        text = str(error)
    return text.replace("\n", " ")[:500]


def _comments_last_scanned(item: dict) -> datetime | None:
    return parse_datetime(item.get("comments_last_scanned_at"))
