from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

from .store import CommonStore


def database_backend_name(database_url: str | None) -> str:
    if not database_url:
        return "sqlite"
    parsed = urlparse(str(database_url))
    scheme = parsed.scheme.lower()
    if scheme in {"", "sqlite", "file"}:
        return "sqlite"
    if scheme in {"postgres", "postgresql"}:
        return "postgres"
    return scheme


def create_common_store(database_url: str | Path | None, *, source_id: str, default_channel_id=None) -> CommonStore:
    backend = database_backend_name(str(database_url) if database_url is not None else None)
    if backend == "sqlite":
        return CommonStore(_sqlite_path(database_url), source_id=source_id, default_channel_id=default_channel_id)
    if backend == "postgres":
        raise NotImplementedError(
            "Postgres storage is declared but not enabled in this phase. "
            "Use sqlite locally, or add the Postgres adapter behind the Store contract."
        )
    raise ValueError(f"Unsupported database backend: {backend}")


def sqlite_database_path(database_url: str | Path | None) -> Path:
    return _sqlite_path(database_url)


def _sqlite_path(database_url: str | Path | None) -> Path:
    if database_url is None:
        return Path("storage/research_mirror.db").resolve()
    if isinstance(database_url, Path):
        return database_url.expanduser().resolve()
    value = str(database_url)
    parsed = urlparse(value)
    if parsed.scheme in {"", "file"}:
        return Path(unquote(parsed.path or value)).expanduser().resolve()
    if parsed.scheme != "sqlite":
        raise ValueError(f"Not a SQLite database URL: {value}")
    if parsed.netloc and parsed.netloc != "localhost":
        raise ValueError(f"Unsupported SQLite URL host: {parsed.netloc}")
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and not value.startswith("sqlite:////"):
        raw_path = raw_path[1:]
    return Path(raw_path).expanduser().resolve()
