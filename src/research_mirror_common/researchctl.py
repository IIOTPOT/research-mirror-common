from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Sequence

from .audit import build_audit_snapshot
from .config import ResearchConfig, SourceRuntimeConfig, load_research_config
from .storage import database_backend_name, sqlite_database_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="researchctl")
    parser.add_argument("--config", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--source", dest="source_id")

    publish = sub.add_parser("publish-status")
    publish.add_argument("--dry-run", action="store_true")

    backup = sub.add_parser("backup")
    backup_sub = backup.add_subparsers(dest="backup_command", required=True)
    backup_create = backup_sub.add_parser("create")
    backup_create.add_argument("--output-dir", type=Path)
    backup_restore = backup_sub.add_parser("restore")
    backup_restore.add_argument("backup_path", type=Path)

    source = sub.add_parser("source")
    source_sub = source.add_subparsers(dest="source_command", required=True)
    source_run = source_sub.add_parser("run")
    source_run.add_argument("source_id")
    mode = source_run.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--loop", action="store_true")
    source_run.add_argument("--dry-run", action="store_true")
    source_run.add_argument("--print-command", action="store_true")

    migrate = sub.add_parser("migrate")
    migrate.add_argument("args", nargs=argparse.REMAINDER)

    args = parser.parse_args(list(argv) if argv is not None else None)
    config = load_research_config(args.config)

    if args.command == "status":
        payload = build_status(config)
        return _emit(payload, as_json=args.json)
    if args.command == "doctor":
        if args.source_id and args.source_id not in config.sources:
            parser.error(f"unknown source: {args.source_id}")
        payload = build_doctor_report(config, source_id=args.source_id)
        rc = 0 if payload["ok"] else 1
        _emit(payload, as_json=args.json)
        return rc
    if args.command == "source" and args.source_command == "run":
        return run_source(config, args.source_id, once=args.once, dry_run=args.dry_run, print_command=args.print_command)
    if args.command == "publish-status":
        return _publish_status(config, dry_run=args.dry_run)
    if args.command == "backup":
        if args.backup_command == "create":
            payload = create_backup(config, output_dir=args.output_dir)
        else:
            payload = restore_backup(config, args.backup_path)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == "migrate":
        from .migrate import main as migrate_main

        return migrate_main(list(args.args))
    parser.error("unsupported command")
    return 2


def build_status(config: ResearchConfig) -> dict[str, Any]:
    backend = database_backend_name(config.database.url)
    database: dict[str, Any] = {"url": config.database.url, "backend": backend}
    if backend == "sqlite":
        db_path = sqlite_database_path(config.database.url)
        database["path"] = str(db_path)
        database["exists"] = db_path.exists()
        audit = build_audit_snapshot(db_path) if db_path.exists() else {"sources": {}}
    else:
        audit = {"sources": {}}
    return {
        "runtime": config.runtime.mode,
        "config": str(config.path),
        "database": database,
        "artifacts_root": str(config.storage.artifacts_root),
        "sources": {
            source_id: {
                "configured": True,
                "name": source.name,
                "module": source.module,
                "schedule": source.schedule,
                "workdir": str(source.workdir),
                "state": audit.get("sources", {}).get(source_id, {}),
            }
            for source_id, source in sorted(config.sources.items())
        },
    }


def build_doctor_report(
    config: ResearchConfig,
    *,
    env: dict[str, str] | None = None,
    source_id: str | None = None,
) -> dict[str, Any]:
    import os

    env = dict(os.environ if env is None else env)
    status = build_status(config)
    database_check = _database_check(config)
    artifact_check = {"ok": True, "path": str(config.storage.artifacts_root)}
    selected = (
        {source_id: config.sources[source_id]}
        if source_id is not None
        else dict(sorted(config.sources.items()))
    )
    source_checks = {sid: _source_check(source, env=env) for sid, source in selected.items()}
    # Disabled sources (e.g. not deployed in this stack) are reported but excluded
    # from the aggregate ok so doctor stays a reliable green/red signal.
    blocking = [item["ok"] for item in source_checks.values() if item.get("enabled", True)]
    checks = {
        "database": database_check,
        "artifacts": artifact_check,
        "sources": source_checks,
    }
    return {
        "ok": database_check["ok"] and artifact_check["ok"] and all(blocking),
        "runtime": status["runtime"],
        "checks": checks,
    }


def run_source(
    config: ResearchConfig,
    source_id: str,
    *,
    once: bool,
    dry_run: bool = False,
    print_command: bool = False,
) -> int:
    source = config.sources[source_id]
    command = list(source.command_once if once else source.command_loop)
    if dry_run and "--dry-run" not in command:
        command.append("--dry-run")
    if print_command:
        print(json.dumps({"cwd": str(source.workdir), "command": command}, ensure_ascii=False, indent=2))
        return 0
    return subprocess.run(command, cwd=source.workdir, check=False).returncode


def create_backup(config: ResearchConfig, *, output_dir: Path | None = None) -> dict[str, str]:
    if database_backend_name(config.database.url) != "sqlite":
        raise RuntimeError("backup create currently supports sqlite databases")
    db_path = sqlite_database_path(config.database.url)
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    backup_dir = (output_dir or db_path.parent / "backups").expanduser().resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"{db_path.stem}-{stamp}{db_path.suffix}"
    shutil.copy2(db_path, destination)
    return {"database": str(db_path), "backup": str(destination)}


def restore_backup(config: ResearchConfig, backup_path: Path) -> dict[str, str]:
    if database_backend_name(config.database.url) != "sqlite":
        raise RuntimeError("backup restore currently supports sqlite databases")
    db_path = sqlite_database_path(config.database.url)
    backup = backup_path.expanduser().resolve()
    if not backup.exists():
        raise FileNotFoundError(backup)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, db_path)
    return {"database": str(db_path), "restored_from": str(backup)}


def _database_check(config: ResearchConfig) -> dict[str, Any]:
    backend = database_backend_name(config.database.url)
    if backend != "sqlite":
        return {"ok": True, "backend": backend, "message": "external database configured"}
    db_path = sqlite_database_path(config.database.url)
    return {"ok": db_path.exists(), "backend": "sqlite", "path": str(db_path)}


def _source_check(source: SourceRuntimeConfig, *, env: dict[str, str]) -> dict[str, Any]:
    if not source.enabled:
        return {
            "ok": True,
            "enabled": False,
            "skipped": True,
            "schedule": source.schedule,
        }
    env_file_values: set[str] = set()
    if source.workdir.exists():
        for name in (".env.local", ".env"):
            env_file_values |= _read_env_file_names(source.workdir / name)
    missing_env = [name for name in source.required_env if not env.get(name) and name not in env_file_values]
    return {
        "ok": source.workdir.exists() and not missing_env,
        "enabled": True,
        "workdir_exists": source.workdir.exists(),
        "missing_env": missing_env,
        "schedule": source.schedule,
    }


def _read_env_file_names(path: Path) -> set[str]:
    if not path.exists():
        return set()
    names: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        lines = path.read_text().splitlines()
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        name = text.split("=", 1)[0].strip()
        if name and name.replace("_", "").isalnum():
            names.add(name)
    return names


def _publish_status(config: ResearchConfig, *, dry_run: bool) -> int:
    from scripts.statushealthai import main as status_main

    argv = ["--summary-dir", str(config.storage.artifacts_root / "status-health")]
    if config.health.chat_id:
        argv.extend(["--chat-id", config.health.chat_id])
    if dry_run:
        argv.append("--dry-run")
    return status_main(argv)


def _emit(payload: dict[str, Any], *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(_format_human(payload))
    return 0


def _format_human(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
