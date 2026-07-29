from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OFFLINE_DIR = REPOSITORY_ROOT / "deploy" / "offline"
DEPLOYMENT_CONFIG = OFFLINE_DIR / "deployment-config.sh"


def _script(name: str) -> str:
    return (OFFLINE_DIR / name).read_text(encoding="utf-8")


def _validate_environment(tmp_path: Path, content: str) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / "openslt.env"
    env_file.write_text(content, encoding="utf-8")
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; validate_deployment_environment "$2" || exit $?; printf "%s %s" "$BACKEND_PORT" "$FRONTEND_PORT"',
            "bash",
            str(DEPLOYMENT_CONFIG),
            str(env_file),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )


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
    assert 'ENV_FILE="/etc/openslt/openslt.env"' in configure
    assert 'ENV_FILE="/etc/openslt/openslt.env"' in installer
    assert 'ENV_FILE="/etc/openslt/openslt.env"' in starter
    assert 'environment["AUTO_CREATE_DATABASE"] = "false"' not in installer
    assert 'environment["AUTO_CREATE_DATABASE"] = "false"' not in starter


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


def test_environment_example_contains_only_deployment_inputs() -> None:
    example = _script("openslt.env.example")

    for key in (
        "DATABASE_HOST=127.0.0.1",
        "DATABASE_PORT=3306",
        "DATABASE_NAME=openslt",
        "DATABASE_USER=openslt",
        "DATABASE_PASSWORD=CHANGE_ME",
        "BACKEND_PORT=4396",
        "FRONTEND_PORT=7777",
        "INITIAL_ADMIN_USERNAME=admin",
        "INITIAL_ADMIN_PASSWORD=shengli123",
    ):
        assert key in example
    for removed in (
        "DATABASE_URL",
        "AUTO_CREATE_DATABASE",
        "JWT_SECRET",
        "CREDENTIAL_ENCRYPTION_KEY",
        "PORTABLE_MODE",
        "OPEN_BROWSER",
    ):
        assert removed not in example


def test_environment_placeholder_check_ignores_comments() -> None:
    helper = _script("deployment-config.sh")

    assert "/^[[:space:]]*#/ { next }" in helper
    assert 'if environment_has_placeholder "$env_file"; then' in helper
    assert "grep -q 'CHANGE_ME'" not in helper


def test_split_environment_and_custom_ports_are_accepted(tmp_path: Path) -> None:
    completed = _validate_environment(
        tmp_path,
        """# Replace CHANGE_ME only in active values.
DATABASE_HOST=10.0.0.8
DATABASE_PORT=3307
DATABASE_NAME=openslt
DATABASE_USER=app
DATABASE_PASSWORD="p@ss:#word"
BACKEND_PORT=4400
FRONTEND_PORT=7788
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=shengli123
""",
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "4400 7788"


def test_legacy_database_url_uses_default_ports(tmp_path: Path) -> None:
    completed = _validate_environment(
        tmp_path,
        "DATABASE_URL=mysql+pymysql://openslt:secret@127.0.0.1:3306/openslt\n",
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "4396 7777"


@pytest.mark.parametrize(
    "content, message",
    [
        (
            "DATABASE_URL=mysql+pymysql://app:secret@localhost/openslt\n"
            "DATABASE_HOST=127.0.0.1\n",
            "cannot be combined",
        ),
        (
            "DATABASE_HOST=127.0.0.1\nDATABASE_PORT=3306\nDATABASE_NAME=openslt\n"
            "DATABASE_USER=app\nDATABASE_PASSWORD=secret\n"
            "BACKEND_PORT=80\nFRONTEND_PORT=7777\n",
            "BACKEND_PORT must be between",
        ),
        (
            "DATABASE_HOST=127.0.0.1\nDATABASE_PORT=70000\nDATABASE_NAME=openslt\n"
            "DATABASE_USER=app\nDATABASE_PASSWORD=secret\n",
            "DATABASE_PORT must be between",
        ),
        (
            "DATABASE_HOST=127.0.0.1\nDATABASE_PORT=3306\nDATABASE_NAME=openslt\n"
            "DATABASE_USER=app\nDATABASE_PASSWORD=secret\n"
            "BACKEND_PORT=7777\nFRONTEND_PORT=7777\n",
            "must be different",
        ),
        (
            "DATABASE_HOST=127.0.0.1\nDATABASE_PORT=3306\nDATABASE_NAME=openslt\n"
            "DATABASE_USER=app\nDATABASE_PASSWORD=CHANGE_ME\n",
            "CHANGE_ME placeholders",
        ),
    ],
)
def test_invalid_deployment_environment_is_rejected(
    tmp_path: Path,
    content: str,
    message: str,
) -> None:
    completed = _validate_environment(tmp_path, content)

    assert completed.returncode != 0
    assert message in completed.stderr


def test_bundle_contains_shared_deployment_configuration() -> None:
    builder = _script("build-offline-bundle.sh")
    installer = _script("install-offline.sh")
    starter = _script("start-production.sh")

    assert 'cp -p "$SCRIPT_DIR/deployment-config.sh" "$STAGING/deployment-config.sh"' in builder
    assert "source \"$BUNDLE_ROOT/deployment-config.sh\"" in installer
    assert "source \"$BUNDLE_ROOT/deployment-config.sh\"" in starter


def test_runtime_ports_are_rendered_and_checked_dynamically() -> None:
    helper = _script("deployment-config.sh")
    configure = _script("configure-intranet-host.sh")
    installer = _script("install-offline.sh")
    starter = _script("start-production.sh")

    assert 's/--port 4396/--port $BACKEND_PORT/' in helper
    assert 's/listen 7777/listen $FRONTEND_PORT/' in helper
    assert 'add-port="$FRONTEND_PORT/tcp"' in configure
    assert '"http://127.0.0.1:$BACKEND_PORT/health"' in installer
    assert '"http://127.0.0.1:$FRONTEND_PORT/"' in starter


def test_runtime_secrets_are_persisted_outside_environment() -> None:
    installer = _script("install-offline.sh")
    starter = _script("start-production.sh")

    assert "/var/lib/openslt/secrets" in installer
    assert "chmod 0600" in installer
    assert "dotenv_values(sys.argv[1], interpolate=False)" in installer
    assert "dotenv_values(sys.argv[1], interpolate=False)" in starter
    assert "/etc/openslt/initial-admin-password" not in starter


def test_canonical_environment_permissions_are_restricted() -> None:
    helper = _script("deployment-config.sh")

    assert 'CANONICAL_ENV_FILE="/etc/openslt/openslt.env"' in helper
    assert "install -o root -g openslt -m 0640" in helper
    assert 'chmod 0640 "$CANONICAL_ENV_FILE"' in helper
