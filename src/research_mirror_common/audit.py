from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any


def build_audit_snapshot(db_path: str | Path, *, source_ids: list[str] | None = None) -> dict[str, Any]:
    path = Path(db_path)
    if not path.exists():
        return {"database": str(path), "exists": False, "sources": {}}
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        sources = source_ids or _source_ids(connection)
        return {
            "database": str(path),
            "exists": True,
            "sources": {source_id: _source_snapshot(connection, source_id) for source_id in sources},
        }


def _source_ids(connection: sqlite3.Connection) -> list[str]:
    result: set[str] = set()
    for table in ("bot_state", "materials", "deliveries", "comments", "summaries", "subscribers"):
        if not _table_exists(connection, table):
            continue
        rows = connection.execute(f"SELECT DISTINCT source_id FROM {table}").fetchall()
        result.update(str(row["source_id"]) for row in rows)
    return sorted(result)


def _source_snapshot(connection: sqlite3.Connection, source_id: str) -> dict[str, Any]:
    state = _state(connection, source_id)
    return {
        "status": state.get("last_check_status") or "unknown",
        "last_check_started_at": state.get("last_check_started_at") or None,
        "last_check_finished_at": state.get("last_check_finished_at") or None,
        "last_successful_scan": state.get("last_successful_scan") or None,
        "last_error": state.get("last_check_error") or None,
        "consecutive_failures": _int(state.get("consecutive_failures")),
        "materials": _count(connection, "materials", source_id),
        "deliveries": _count(connection, "deliveries", source_id),
        "pending_deliveries": _count_where(
            connection,
            "deliveries",
            source_id,
            "status IN ('summary_only', 'summary_pending')",
        ),
        "comments": _count(connection, "comments", source_id),
        "summaries": _count(connection, "summaries", source_id),
        "subscribers": _count_where(connection, "subscribers", source_id, "is_active = 1"),
        "pending_overflow": _count(connection, "pending_overflow", source_id),
    }


def _state(connection: sqlite3.Connection, source_id: str) -> dict[str, str]:
    if not _table_exists(connection, "bot_state"):
        return {}
    rows = connection.execute("SELECT key, value FROM bot_state WHERE source_id = ?", (source_id,)).fetchall()
    return {str(row["key"]): str(row["value"]) for row in rows}


def _count(connection: sqlite3.Connection, table: str, source_id: str) -> int:
    return _count_where(connection, table, source_id, "1 = 1")


def _count_where(connection: sqlite3.Connection, table: str, source_id: str, where: str) -> int:
    if not _table_exists(connection, table):
        return 0
    return int(
        connection.execute(
            f"SELECT count(*) FROM {table} WHERE source_id = ? AND {where}",
            (source_id,),
        ).fetchone()[0]
    )


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone() is not None


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
