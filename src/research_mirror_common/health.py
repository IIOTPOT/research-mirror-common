from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


class HealthStatus(StrEnum):
    OK = "ok"
    WARN = "warn"
    ERROR = "error"


@dataclass(frozen=True)
class HealthMessage:
    status: HealthStatus
    text: str


class HealthStateStore(Protocol):
    def get_state(self, key: str) -> str | None:
        ...

    def set_state(self, key: str, value: str) -> None:
        ...


class HealthTelegram(Protocol):
    def send_message(self, chat_id, text: str, **kwargs):
        ...

    def edit_message_text(self, chat_id, message_id: int, text: str, **kwargs):
        ...


@dataclass(frozen=True)
class CompactHealthSnapshot:
    bot_name: str
    status: str
    poll_at: datetime | None
    scan_at: datetime | None
    next_at: datetime | None
    change_count: int
    error: str | None = None
    name_first: bool = False
    error_limit: int = 32
    empty_time: str = "-"
    ellipsis: str = "…"


class TelegramHealthStatusPublisher:
    def __init__(
        self,
        *,
        telegram: HealthTelegram,
        store: HealthStateStore,
        chat_id,
        message_id_key: str = "health_status_message_id",
        chat_id_key: str = "health_status_chat_id",
        text_key: str | None = None,
        send_kwargs: dict[str, Any] | None = None,
        edit_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.telegram = telegram
        self.store = store
        self.chat_id = chat_id
        self.message_id_key = message_id_key
        self.chat_id_key = chat_id_key
        self.text_key = text_key
        self.send_kwargs = {"disable_web_page_preview": True} if send_kwargs is None else send_kwargs
        self.edit_kwargs = {"disable_web_page_preview": True} if edit_kwargs is None else edit_kwargs

    def publish(self, text: str) -> int | None:
        if self.text_key and self.store.get_state(self.text_key) == text:
            return _optional_int(self.store.get_state(self.message_id_key))

        message_id = _optional_int(self.store.get_state(self.message_id_key))
        stored_chat_id = self.store.get_state(self.chat_id_key)
        if message_id is not None and (stored_chat_id in {None, "", str(self.chat_id)}):
            try:
                self.telegram.edit_message_text(self.chat_id, message_id, text, **self.edit_kwargs)
                self._remember(message_id, text)
                return message_id
            except Exception as exc:
                text_exc = str(exc).casefold()
                if "message is not modified" in text_exc:
                    self._remember(message_id, text)
                    return message_id
                if not _should_send_replacement(text_exc):
                    return None

        try:
            result = self.telegram.send_message(self.chat_id, text, **self.send_kwargs)
        except Exception:
            return None
        new_message_id = _message_id(result)
        if new_message_id is not None:
            self._remember(new_message_id, text)
        return new_message_id

    def _remember(self, message_id: int, text: str) -> None:
        self.store.set_state(self.message_id_key, str(int(message_id)))
        self.store.set_state(self.chat_id_key, str(self.chat_id))
        if self.text_key:
            self.store.set_state(self.text_key, text)


def build_health_message(bot_name: str, snapshot: dict[str, Any]) -> HealthMessage:
    name = str(bot_name or "Bot").strip() or "Bot"
    raw_status = str(snapshot.get("status") or "unknown").lower()
    failures = _int(snapshot.get("consecutive_failures"))
    last_error = str(snapshot.get("last_error") or "").strip()
    reports = snapshot.get("reports")
    sent = snapshot.get("sent")

    if raw_status == "error" or failures >= 3:
        status = HealthStatus.ERROR
        prefix = "ERROR"
    elif failures > 0 or last_error:
        status = HealthStatus.WARN
        prefix = "WARN"
    else:
        status = HealthStatus.OK
        prefix = "OK"

    parts = [f"{prefix} {name}", f"status={raw_status}"]
    if reports is not None:
        parts.append(f"found={reports}")
    if sent is not None:
        parts.append(f"sent={sent}")
    if failures:
        parts.append(f"failures={failures}")
    if last_error:
        parts.append(f"error={last_error[:180]}")
    return HealthMessage(status=status, text=" | ".join(parts))


def format_compact_health_status(snapshot: CompactHealthSnapshot) -> str:
    emoji, label = _compact_status(snapshot.status)
    name = _compact_name(snapshot.bot_name)
    heading = f"{name} {emoji} {label}" if snapshot.name_first else f"{emoji} {name} {label}"
    return "\n".join(
        [
            heading,
            f"poll {_time(snapshot.poll_at, empty=snapshot.empty_time)} | "
            f"scan {_time(snapshot.scan_at, empty=snapshot.empty_time)} +{max(0, int(snapshot.change_count))}",
            f"next {_time(snapshot.next_at, empty=snapshot.empty_time)} | "
            f"err {_short_error(snapshot.error, limit=snapshot.error_limit, ellipsis=snapshot.ellipsis)}",
        ]
    )


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _message_id(result: Any) -> int | None:
    if isinstance(result, dict) and result.get("message_id") is not None:
        return _optional_int(result["message_id"])
    return None


def _should_send_replacement(error_text: str) -> bool:
    return "message to edit not found" in error_text or "message can't be edited" in error_text


def _compact_status(value: str) -> tuple[str, str]:
    normalized = str(value or "OK").strip().upper()
    if normalized == "OK":
        return "🟢", "OK"
    if normalized in {"ERROR", "ERR", "FAIL", "FAILED", "CRIT", "CRITICAL", "DOWN"}:
        return "🔴", "ERROR" if normalized != "FAIL" else "FAIL"
    return "🟡", "WARN"


def _compact_name(value: str) -> str:
    text = " ".join(str(value or "").split())
    return text or "Bot"


def _time(value: datetime | None, *, empty: str) -> str:
    if value is None:
        return empty
    return value.strftime("%H:%M")


def _short_error(value: str | None, *, limit: int, ellipsis: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return "-"
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(ellipsis))].rstrip() + ellipsis
