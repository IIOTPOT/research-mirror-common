from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib
from typing import Callable, Mapping, Any


@dataclass(frozen=True)
class ConfigField:
    name: str
    required: bool = False
    secret: bool = False
    cast: Callable[[str], Any] | None = None


@dataclass(frozen=True)
class ConfigValidationResult:
    ok: bool
    values: dict[str, Any]
    errors: list[str]


@dataclass(frozen=True)
class RuntimeConfig:
    mode: str = "local"


@dataclass(frozen=True)
class DatabaseConfig:
    url: str


@dataclass(frozen=True)
class ArtifactConfig:
    artifacts_root: Path


@dataclass(frozen=True)
class HealthConfig:
    chat_id: str | None = None


@dataclass(frozen=True)
class SourceRuntimeConfig:
    source_id: str
    name: str
    module: str
    workdir: Path
    schedule: str
    command_once: list[str]
    command_loop: list[str]
    required_env: list[str]
    enabled: bool = True
    stale_after_seconds: int = 3600
    failure_threshold: int = 3
    launchd_label: str | None = None
    start_interval_seconds: int | None = None
    calendar_hour: int | None = None
    calendar_minute: int | None = None
    environment: dict[str, str] | None = None


@dataclass(frozen=True)
class ResearchConfig:
    path: Path
    runtime: RuntimeConfig
    database: DatabaseConfig
    storage: ArtifactConfig
    health: HealthConfig
    sources: dict[str, SourceRuntimeConfig]


DEFAULT_CONFIG_NAME = "research_mirror.toml"


def validate_config(env: Mapping[str, str | None], fields: list[ConfigField]) -> ConfigValidationResult:
    values: dict[str, Any] = {}
    errors: list[str] = []
    for field in fields:
        raw = (env.get(field.name) or "").strip()
        if field.required and not raw:
            errors.append(f"{field.name} is required")
        if not raw:
            values[field.name] = None
            continue
        if field.cast is not None:
            try:
                cast_value = field.cast(raw)
            except Exception:
                cast_name = getattr(field.cast, "__name__", "valid value")
                errors.append(f"{field.name} must be {cast_name}")
                values[field.name] = "***" if field.secret else raw
                continue
            values[field.name] = "***" if field.secret else cast_value
        else:
            values[field.name] = "***" if field.secret else raw
    return ConfigValidationResult(ok=not errors, values=values, errors=errors)


def load_research_config(path: str | Path | None = None, *, env: Mapping[str, str | None] | None = None) -> ResearchConfig:
    env = os.environ if env is None else env
    config_path = _config_path(path, env=env)
    raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    base_dir = config_path.parent

    runtime_raw = _mapping(raw.get("runtime"))
    database_raw = _mapping(raw.get("database"))
    storage_raw = _mapping(raw.get("storage"))
    health_raw = _mapping(raw.get("health"))

    database_url = str(env.get("DATABASE_URL") or database_raw.get("url") or f"sqlite:///{base_dir / 'storage' / 'research_mirror.db'}")
    artifacts_root = _resolve_path(
        str(env.get("ARTIFACT_STORAGE_ROOT") or storage_raw.get("artifacts_root") or "storage/artifacts"),
        base_dir,
    )
    runtime_mode = str(env.get("RESEARCH_RUNTIME") or runtime_raw.get("mode") or "local").strip() or "local"
    health_chat_id = env.get("HEALTH_CHAT_ID") or health_raw.get("chat_id")

    sources: dict[str, SourceRuntimeConfig] = {}
    for source_id, source_raw in _mapping(raw.get("sources")).items():
        source = _mapping(source_raw)
        sources[str(source_id)] = SourceRuntimeConfig(
            source_id=str(source_id),
            name=str(source.get("name") or source_id),
            module=str(source.get("module") or ""),
            workdir=_resolve_path(str(source.get("workdir") or "."), base_dir),
            schedule=str(source.get("schedule") or "manual"),
            command_once=_string_list(source.get("command_once")),
            command_loop=_string_list(source.get("command_loop")),
            required_env=_string_list(source.get("required_env")),
            enabled=_bool(source.get("enabled"), True),
            stale_after_seconds=_int(source.get("stale_after_seconds"), 3600),
            failure_threshold=_int(source.get("failure_threshold"), 3),
            launchd_label=str(source.get("launchd_label")) if source.get("launchd_label") else None,
            start_interval_seconds=_optional_int(source.get("start_interval_seconds")),
            calendar_hour=_optional_int(source.get("calendar_hour")),
            calendar_minute=_optional_int(source.get("calendar_minute")),
            environment={str(key): str(value) for key, value in _mapping(source.get("environment")).items()},
        )

    return ResearchConfig(
        path=config_path,
        runtime=RuntimeConfig(mode=runtime_mode),
        database=DatabaseConfig(url=database_url),
        storage=ArtifactConfig(artifacts_root=artifacts_root),
        health=HealthConfig(chat_id=str(health_chat_id) if health_chat_id else None),
        sources=sources,
    )


def find_default_config_path(*, env: Mapping[str, str | None] | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("RESEARCH_MIRROR_CONFIG"):
        return Path(str(env["RESEARCH_MIRROR_CONFIG"])).expanduser()
    cwd_candidate = Path.cwd() / DEFAULT_CONFIG_NAME
    if cwd_candidate.exists():
        return cwd_candidate
    return Path(__file__).resolve().parents[2] / DEFAULT_CONFIG_NAME


def _config_path(path: str | Path | None, *, env: Mapping[str, str | None]) -> Path:
    candidate = Path(path).expanduser() if path is not None else find_default_config_path(env=env)
    if not candidate.exists():
        raise FileNotFoundError(f"Research mirror config not found: {candidate}")
    return candidate.resolve()


def _resolve_path(value: str, base_dir: Path) -> Path:
    expanded = Path(os.path.expandvars(value)).expanduser()
    return expanded if expanded.is_absolute() else (base_dir / expanded).resolve()


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item)]
    if value in {None, ""}:
        return []
    return [str(value)]


def _int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _optional_int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    return _int(value, 0)
