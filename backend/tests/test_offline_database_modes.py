from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OFFLINE_DIR = REPOSITORY_ROOT / "deploy" / "offline"


def _script(name: str) -> str:
    return (OFFLINE_DIR / name).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "script_name",
    ["configure-intranet-host.sh", "install-offline.sh", "start-production.sh"],
)
def test_database_mode_validation_happens_before_host_changes(script_name: str) -> None:
    completed = subprocess.run(
        ["bash", str(OFFLINE_DIR / script_name), "--database-mode", "invalid"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "Invalid database mode" in completed.stderr


def test_existing_mode_is_the_safe_default() -> None:
    configure = _script("configure-intranet-host.sh")
    installer = _script("install-offline.sh")
    starter = _script("start-production.sh")

    assert 'DATABASE_MODE="existing"' in configure
    assert 'DATABASE_MODE="${DATABASE_MODE:-existing}"' in installer
    assert 'DATABASE_MODE="${DATABASE_MODE:-existing}"' in starter
    assert 'environment["AUTO_CREATE_DATABASE"] = "false"' in installer
    assert 'environment["AUTO_CREATE_DATABASE"] = "false"' in starter


def test_existing_mode_does_not_manage_the_database_instance() -> None:
    configure = _script("configure-intranet-host.sh")
    marker = "if [[ \"$DATABASE_MODE\" == \"existing\" ]]; then\n    printf '[OpenSLT] Existing database mode"
    database_branch = configure[configure.index(marker) :]
    existing_branch = database_branch.split("\nelse\n", 1)[0]

    for forbidden in ("my.cnf", "systemctl", "mysql -", "CREATE DATABASE", "GRANT ALL"):
        assert forbidden not in existing_branch

    starter = _script("start-production.sh")
    service_branch = starter[
        starter.index('if [[ "$DATABASE_MODE" != "existing" ]]; then') :
        starter.index('if [[ "$FORCE_INSTALL"')
    ]
    assert "systemctl enable mariadb" in service_branch
    assert "systemctl start mariadb" in service_branch
    assert "else" in service_branch


def test_destructive_database_cleanup_is_initialize_only() -> None:
    configure = _script("configure-intranet-host.sh")
    initialize_start = configure.index('if [[ "$DATABASE_MODE" == "initialize" ]]; then')
    provision_notice = configure.index("Provision mode: MariaDB security cleanup")

    for statement in (
        "DELETE FROM mysql.user WHERE User = '';",
        "DELETE FROM mysql.user WHERE User = 'root' AND Host <> 'localhost';",
        "DROP DATABASE IF EXISTS test;",
        "UPDATE mysql.user SET Password",
    ):
        position = configure.index(statement)
        assert initialize_start < position < provision_notice
        assert configure.count(statement) == 1


def test_existing_mode_filters_database_rpms() -> None:
    installer = _script("install-offline.sh")

    assert "existing)\n        SKIP_DATABASE_RPMS=true" in installer
    assert "mariadb|mariadb-server|mariadb-libs" in installer
    assert "FILTERED_RPM_FILES" in installer


def test_existing_environment_disables_database_creation() -> None:
    example = _script("openslt.env.example")

    assert "AUTO_CREATE_DATABASE=false" in example
