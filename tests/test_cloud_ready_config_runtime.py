from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from research_mirror_common.artifacts import LocalArtifactStorage
from research_mirror_common.config import load_research_config
from research_mirror_common.researchctl import main as researchctl_main
from research_mirror_common.storage import create_common_store, database_backend_name


def test_config_loader_resolves_relative_paths_and_env_overrides(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "research_mirror.toml"
    config_path.write_text(
        """
[runtime]
mode = "local"

[database]
url = "sqlite:///storage/local.db"

[storage]
artifacts_root = "artifacts"

[health]
chat_id = "-100local"

[sources.alfa]
name = "Alfa"
module = "alfa_telegram_bot"
workdir = "../AlfaParser"
schedule = "loop"
command_once = ["python3", "-m", "alfa_telegram_bot", "check-once"]
command_loop = ["python3", "-m", "alfa_telegram_bot", "run"]
required_env = ["ALFA_BOT_TOKEN"]
stale_after_seconds = 7200
failure_threshold = 2
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/cloud.db")
    monkeypatch.setenv("ARTIFACT_STORAGE_ROOT", "/tmp/artifacts")
    monkeypatch.setenv("RESEARCH_RUNTIME", "docker")
    monkeypatch.setenv("HEALTH_CHAT_ID", "-100cloud")

    config = load_research_config(config_path)

    assert config.runtime.mode == "docker"
    assert config.database.url == "sqlite:////tmp/cloud.db"
    assert config.storage.artifacts_root == Path("/tmp/artifacts")
    assert config.health.chat_id == "-100cloud"
    assert config.sources["alfa"].workdir == (tmp_path / "../AlfaParser").resolve()
    assert config.sources["alfa"].required_env == ["ALFA_BOT_TOKEN"]
    assert config.sources["alfa"].failure_threshold == 2


def test_artifact_storage_writes_under_source_namespace(tmp_path) -> None:
    storage = LocalArtifactStorage(tmp_path)

    stored = storage.put_bytes("alfa", "pdf/report.pdf", b"payload")

    assert stored == tmp_path / "alfa" / "pdf" / "report.pdf"
    assert stored.read_bytes() == b"payload"
    assert storage.exists("alfa", "pdf/report.pdf")


def test_create_common_store_keeps_sqlite_behavior(tmp_path) -> None:
    db_path = tmp_path / "research.db"
    store = create_common_store(f"sqlite:///{db_path}", source_id="alfa")

    store.record_delivery(chat_id="-100", external_id="m1", message_id=42)

    assert database_backend_name(f"sqlite:///{db_path}") == "sqlite"
    assert store.was_delivered(chat_id="-100", external_id="m1")


def test_researchctl_status_and_doctor_use_config(tmp_path, capsys, monkeypatch) -> None:
    db_path = tmp_path / "research.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE bot_state (source_id TEXT, key TEXT, value TEXT, updated_at TEXT, PRIMARY KEY (source_id, key))"
        )
        connection.execute("INSERT INTO bot_state VALUES ('alfa', 'last_scan_status', 'ok', 'now')")

    config_path = tmp_path / "research_mirror.toml"
    config_path.write_text(
        f"""
[runtime]
mode = "docker"

[database]
url = "sqlite:///{db_path}"

[storage]
artifacts_root = "artifacts"

[health]
chat_id = "-100"

[sources.alfa]
name = "Alfa"
module = "alfa_telegram_bot"
workdir = "."
schedule = "loop"
command_once = ["python3", "-m", "alfa_telegram_bot", "check-once"]
command_loop = ["python3", "-m", "alfa_telegram_bot", "run"]
required_env = ["ALFA_BOT_TOKEN"]
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ALFA_BOT_TOKEN", "token")

    assert researchctl_main(["--config", str(config_path), "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["runtime"] == "docker"
    assert status["database"]["backend"] == "sqlite"
    assert status["sources"]["alfa"]["configured"] is True

    assert researchctl_main(["--config", str(config_path), "doctor", "--json"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["ok"] is True
    assert doctor["checks"]["database"]["ok"] is True
    assert doctor["checks"]["sources"]["alfa"]["ok"] is True


def test_doctor_ignores_disabled_source_and_reads_env_file(tmp_path, capsys, monkeypatch) -> None:
    db_path = tmp_path / "research.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE bot_state (source_id TEXT, key TEXT, value TEXT, updated_at TEXT, PRIMARY KEY (source_id, key))"
        )

    # d8-style source: required env lives in workdir/.env (not .env.local).
    d8_dir = tmp_path / "d8"
    d8_dir.mkdir()
    (d8_dir / ".env").write_text("D8_BOT_CHANNEL_ID=-100123\n", encoding="utf-8")

    config_path = tmp_path / "research_mirror.toml"
    config_path.write_text(
        f"""
[runtime]
mode = "docker"

[database]
url = "sqlite:///{db_path}"

[storage]
artifacts_root = "artifacts"

[sources.d8]
name = "D8"
module = "d8_telegram_bot"
workdir = "{d8_dir}"
schedule = "calendar"
command_once = ["python3", "-m", "d8_telegram_bot.cli", "check-once"]
command_loop = ["python3", "-m", "d8_telegram_bot.cli", "run"]
required_env = ["D8_BOT_CHANNEL_ID"]

[sources.mrsk]
name = "MRSK"
module = "minenergo_monitor"
workdir = "{tmp_path / 'does-not-exist'}"
enabled = false
schedule = "interval"
command_once = []
command_loop = []
required_env = []
""",
        encoding="utf-8",
    )

    assert researchctl_main(["--config", str(config_path), "doctor", "--json"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    # d8 satisfied via .env; mrsk disabled and excluded from aggregate.
    assert doctor["ok"] is True
    assert doctor["checks"]["sources"]["d8"]["ok"] is True
    assert doctor["checks"]["sources"]["d8"]["missing_env"] == []
    assert doctor["checks"]["sources"]["mrsk"]["enabled"] is False
    assert doctor["checks"]["sources"]["mrsk"]["skipped"] is True


def test_doctor_source_filter_checks_single_source(tmp_path, capsys) -> None:
    db_path = tmp_path / "research.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE bot_state (source_id TEXT, key TEXT, value TEXT, updated_at TEXT, PRIMARY KEY (source_id, key))"
        )
    alfa_dir = tmp_path / "alfa"
    alfa_dir.mkdir()
    (alfa_dir / ".env.local").write_text("ALFA_BOT_CHANNEL_ID=-100\n", encoding="utf-8")

    config_path = tmp_path / "research_mirror.toml"
    config_path.write_text(
        f"""
[runtime]
mode = "docker"

[database]
url = "sqlite:///{db_path}"

[storage]
artifacts_root = "artifacts"

[sources.alfa]
name = "Alfa"
module = "alfa_telegram_bot"
workdir = "{alfa_dir}"
schedule = "loop"
command_once = ["python3", "-m", "alfa_telegram_bot", "check-once"]
command_loop = ["python3", "-m", "alfa_telegram_bot", "run"]
required_env = ["ALFA_BOT_CHANNEL_ID"]

[sources.rencap]
name = "RenCap"
module = "rencap_telegram_bot"
workdir = "{tmp_path / 'missing-rencap'}"
schedule = "interval"
command_once = []
command_loop = []
required_env = ["RENCAP_BOT_TOKEN"]
""",
        encoding="utf-8",
    )

    # rencap is broken, but filtering to alfa keeps the report green.
    assert researchctl_main(["--config", str(config_path), "doctor", "--source", "alfa", "--json"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["ok"] is True
    assert set(doctor["checks"]["sources"]) == {"alfa"}


def test_researchctl_source_run_builds_existing_source_command(tmp_path, capsys) -> None:
    config_path = tmp_path / "research_mirror.toml"
    config_path.write_text(
        """
[runtime]
mode = "docker"

[database]
url = "sqlite:///storage/research.db"

[storage]
artifacts_root = "artifacts"

[health]
chat_id = "-100"

[sources.rencap]
name = "RenCap"
module = "rencap_telegram_bot"
workdir = "."
schedule = "interval"
command_once = ["python3", "-m", "rencap_telegram_bot", "check-once", "--include-private"]
command_loop = ["python3", "-m", "rencap_telegram_bot", "run", "--include-private", "--no-progress"]
""",
        encoding="utf-8",
    )

    assert researchctl_main(["--config", str(config_path), "source", "run", "rencap", "--once", "--dry-run", "--print-command"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["cwd"] == str(tmp_path)
    assert output["command"] == [
        "python3",
        "-m",
        "rencap_telegram_bot",
        "check-once",
        "--include-private",
        "--dry-run",
    ]
