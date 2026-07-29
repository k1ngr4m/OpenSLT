#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
BUNDLE_ROOT="$SCRIPT_DIR"
PYTHON="/opt/rh/rh-python38/root/usr/bin/python3.8"
MYSQL_DEFAULTS_FILE=""
DATABASE_NAME="openslt"
DATABASE_USER="openslt"
ENV_FILE="/etc/openslt/openslt.env"
OPEN_FIREWALL=true
PYTHON_RUNTIME_DIR="$BUNDLE_ROOT/python-runtime/rh-python38"
RPMS_INSTALLED=false
DATABASE_MODE="existing"
DATABASE_MODE_FILE="/etc/openslt/database-mode"
MYSQL_DEFAULTS_FILE_SET=false
DATABASE_NAME_SET=false
DATABASE_USER_SET=false

usage() {
    cat <<'EOF'
Usage: configure.sh [options]

Run this once as root on the offline RHEL 7.9 application host.

Options:
  --python PATH                Python >=3.8 executable
  --database-mode MODE        existing (default), provision, or initialize
  --mysql-defaults-file FILE  Existing MySQL client option file for root/admin
  --database-name NAME        Application database (default: openslt)
  --database-user NAME        Application user (default: openslt)
  --env-file FILE             Production environment path
  --no-firewall               Do not add firewalld's HTTP service
  -h, --help                  Show this help

If /etc/openslt/openslt.env already exists, its passwords and keys are kept.
EOF
}

while (($#)); do
    case "$1" in
        --python)
            PYTHON="$2"
            shift 2
            ;;
        --database-mode)
            DATABASE_MODE="$2"
            shift 2
            ;;
        --mysql-defaults-file)
            MYSQL_DEFAULTS_FILE="$2"
            MYSQL_DEFAULTS_FILE_SET=true
            shift 2
            ;;
        --database-name)
            DATABASE_NAME="$2"
            DATABASE_NAME_SET=true
            shift 2
            ;;
        --database-user)
            DATABASE_USER="$2"
            DATABASE_USER_SET=true
            shift 2
            ;;
        --env-file)
            ENV_FILE="$2"
            shift 2
            ;;
        --no-firewall)
            OPEN_FIREWALL=false
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown argument: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

case "$DATABASE_MODE" in
    existing|provision|initialize)
        ;;
    *)
        printf 'Invalid database mode: %s (expected existing, provision, or initialize)\n' \
            "$DATABASE_MODE" >&2
        exit 2
        ;;
esac

[[ "$(id -u)" == "0" ]] || {
    printf 'Run configure.sh as root.\n' >&2
    exit 1
}
[[ "$DATABASE_NAME" =~ ^[A-Za-z0-9_]+$ ]] || {
    printf 'Database name may contain only letters, digits, and underscore.\n' >&2
    exit 1
}
[[ "$DATABASE_USER" =~ ^[A-Za-z0-9_]+$ ]] || {
    printf 'Database user may contain only letters, digits, and underscore.\n' >&2
    exit 1
}
if [[ -n "$MYSQL_DEFAULTS_FILE" && ! -f "$MYSQL_DEFAULTS_FILE" ]]; then
    printf 'MySQL defaults file not found: %s\n' "$MYSQL_DEFAULTS_FILE" >&2
    exit 1
fi
environment_has_placeholder() {
    awk '
        /^[[:space:]]*#/ { next }
        /CHANGE_ME/ { found = 1 }
        END { exit(found ? 0 : 1) }
    ' "$1"
}
if [[ "$DATABASE_MODE" == "existing" ]]; then
    if [[ "$MYSQL_DEFAULTS_FILE_SET" == true \
        || "$DATABASE_NAME_SET" == true \
        || "$DATABASE_USER_SET" == true ]]; then
        printf 'Database administration options cannot be used in existing mode.\n' >&2
        exit 2
    fi
    [[ -f "$ENV_FILE" ]] || {
        printf 'Existing mode requires a completed environment file: %s\n' "$ENV_FILE" >&2
        exit 1
    }
    if environment_has_placeholder "$ENV_FILE"; then
        printf 'The existing environment file still contains CHANGE_ME placeholders: %s\n' \
            "$ENV_FILE" >&2
        exit 1
    fi
    for required_key in DATABASE_URL JWT_SECRET CREDENTIAL_ENCRYPTION_KEY INITIAL_ADMIN_PASSWORD; do
        grep -Eq "^[[:space:]]*${required_key}=.+" "$ENV_FILE" || {
            printf 'Required environment entry is missing: %s\n' "$required_key" >&2
            exit 1
        }
    done
fi

install_system_rpms() {
    "$BUNDLE_ROOT/install.sh" --rpms-only --database-mode "$DATABASE_MODE"
}
python_is_supported() {
    [[ -x "$PYTHON" ]] \
        && "$PYTHON" -c \
            'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 8) else 1)' \
            >/dev/null 2>&1
}

if ! python_is_supported && [[ -d "$PYTHON_RUNTIME_DIR" ]]; then
    [[ "$PYTHON" == /opt/rh/rh-python38/* ]] || {
        printf 'The bundled Python runtime can only bootstrap /opt/rh/rh-python38. Requested: %s\n' \
            "$PYTHON" >&2
        exit 1
    }
    printf '[OpenSLT] Installing bundled operating-system packages before Python bootstrap...\n'
    install_system_rpms
    RPMS_INSTALLED=true
    printf '[OpenSLT] Installing the bundled rh-python38 runtime...\n'
    install -d -o root -g root -m 0755 /opt/rh
    cp -a "$PYTHON_RUNTIME_DIR" /opt/rh/
fi

[[ -x "$PYTHON" ]] || {
    printf 'Required Python executable not found: %s\n' "$PYTHON" >&2
    printf 'Create the bundle with --bundle-python or install Python >=3.8 first.\n' >&2
    exit 1
}
PYTHON_VERSION="$("$PYTHON" -c 'import platform; print(platform.python_version())')" || {
    printf 'Unable to run the Python executable: %s\n' "$PYTHON" >&2
    exit 1
}
"$PYTHON" -c \
    'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 8) else 1)' || {
    printf 'OpenSLT requires preinstalled Python >=3.8; found %s at %s\n' \
        "$PYTHON_VERSION" "$PYTHON" >&2
    exit 1
}
printf '[OpenSLT] Python accepted: %s (%s)\n' "$PYTHON_VERSION" "$PYTHON"

if [[ "$RPMS_INSTALLED" == false ]]; then
    printf '[OpenSLT] Installing the bundled operating-system packages...\n'
    install_system_rpms
fi

if [[ "$DATABASE_MODE" == "existing" ]]; then
    printf '[OpenSLT] Existing database mode: instance configuration and account management skipped.\n'
    printf '[OpenSLT] Existing environment preserved: %s\n' "$ENV_FILE"
else
    install -d -o root -g root -m 0755 /etc/my.cnf.d
    install -o root -g root -m 0644 /dev/stdin /etc/my.cnf.d/openslt.cnf <<'EOF'
[mysqld]
default-storage-engine=InnoDB
character-set-server=utf8mb4
collation-server=utf8mb4_unicode_ci
innodb-file-per-table=1
EOF

    systemctl enable mariadb
    systemctl restart mariadb

    MYSQL=(mysql)
    if [[ -n "$MYSQL_DEFAULTS_FILE" ]]; then
        MYSQL=(mysql "--defaults-extra-file=$MYSQL_DEFAULTS_FILE")
    fi
    if ! "${MYSQL[@]}" -uroot -NBe "SELECT VERSION()" >/dev/null 2>&1; then
        printf 'Cannot authenticate as MariaDB root. Provide --mysql-defaults-file.\n' >&2
        exit 1
    fi

    SERVER_VERSION="$("${MYSQL[@]}" -uroot -NBe "SELECT VERSION()")"
    "$PYTHON" - "$SERVER_VERSION" <<'PY'
import re
import sys

raw = sys.argv[1]
match = re.search(r"(\d+)\.(\d+)\.(\d+)", raw)
if "mariadb" not in raw.casefold() or not match:
    raise SystemExit("Expected MariaDB, found: " + raw)
version = tuple(int(part) for part in match.groups())
if version == (5, 5, 5):
    versions = re.findall(r"(\d+)\.(\d+)\.(\d+)", raw)
    if len(versions) > 1:
        version = tuple(int(part) for part in versions[1])
if version < (5, 5, 68):
    raise SystemExit("OpenSLT requires MariaDB >= 5.5.68, found: " + raw)
print("[OpenSLT] MariaDB server accepted: " + raw)
PY

    install -d -o root -g root -m 0700 "$(dirname -- "$ENV_FILE")"

    if [[ "$DATABASE_MODE" == "initialize" ]]; then
        ROOT_PASSWORD=""
        ROOT_PASSWORD_QUERY="SELECT COUNT(*) FROM mysql.user WHERE User = 'root' "
        ROOT_PASSWORD_QUERY+="AND Host = 'localhost' AND Password <> ''"
        ROOT_PASSWORD_ROWS="$("${MYSQL[@]}" -uroot -NBe "$ROOT_PASSWORD_QUERY")"
        if [[ "$ROOT_PASSWORD_ROWS" == "0" ]]; then
            if [[ -z "$MYSQL_DEFAULTS_FILE" && ! -f /root/.my.cnf ]]; then
                ROOT_PASSWORD="$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(36))')"
            else
                printf 'MariaDB root has an empty password; secure it before continuing.\n' >&2
                exit 1
            fi
        fi

        "${MYSQL[@]}" -uroot <<SQL
DELETE FROM mysql.user WHERE User = '';
DELETE FROM mysql.user WHERE User = 'root' AND Host <> 'localhost';
DROP DATABASE IF EXISTS test;
DELETE FROM mysql.db WHERE Db = 'test' OR Db LIKE 'test\\_%';
$(if [[ -n "$ROOT_PASSWORD" ]]; then printf "UPDATE mysql.user SET Password = PASSWORD('%s') WHERE User = 'root';" "$ROOT_PASSWORD"; fi)
FLUSH PRIVILEGES;
SQL

        if [[ -n "$ROOT_PASSWORD" ]]; then
            install -o root -g root -m 0600 /dev/stdin /root/.my.cnf <<EOF
[client]
user=root
password=$ROOT_PASSWORD
EOF
            printf '[OpenSLT] MariaDB root credentials saved to /root/.my.cnf\n'
        fi
    else
        printf '[OpenSLT] Provision mode: MariaDB security cleanup and root changes skipped.\n'
    fi

    if [[ -f "$ENV_FILE" ]]; then
        printf '[OpenSLT] Existing environment preserved: %s\n' "$ENV_FILE"
    else
        mapfile -t GENERATED < <("$PYTHON" - <<'PY'
import base64
import os
import secrets

print(secrets.token_urlsafe(36))
print(secrets.token_urlsafe(64))
print(base64.urlsafe_b64encode(os.urandom(32)).decode())
print(secrets.token_urlsafe(24))
PY
        )
        DATABASE_PASSWORD="${GENERATED[0]}"
        JWT_SECRET="${GENERATED[1]}"
        CREDENTIAL_KEY="${GENERATED[2]}"
        INITIAL_ADMIN_PASSWORD="${GENERATED[3]}"

        "${MYSQL[@]}" -uroot <<SQL
CREATE DATABASE IF NOT EXISTS \`$DATABASE_NAME\`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL PRIVILEGES ON \`$DATABASE_NAME\`.*
  TO '$DATABASE_USER'@'127.0.0.1' IDENTIFIED BY '$DATABASE_PASSWORD';
FLUSH PRIVILEGES;
SQL

        install -o root -g root -m 0600 /dev/stdin "$ENV_FILE" <<EOF
ENVIRONMENT=production
TZ=Asia/Shanghai
DATABASE_URL="mysql+pymysql://$DATABASE_USER:$DATABASE_PASSWORD@127.0.0.1:3306/$DATABASE_NAME?charset=utf8mb4"
AUTO_CREATE_DATABASE=true
JWT_SECRET="$JWT_SECRET"
CREDENTIAL_ENCRYPTION_KEY="$CREDENTIAL_KEY"
ARTIFACT_ROOT=/var/lib/openslt/artifacts
LOG_DIR=/var/log/openslt
LOG_LEVEL=INFO
APP_LOG_RETENTION_DAYS=90
AUDIT_LOG_RETENTION_DAYS=365
PORTABLE_MODE=false
ENABLE_INTERNAL_SCHEDULER=true
HOST=127.0.0.1
PORT=4396
OPEN_BROWSER=false
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD="$INITIAL_ADMIN_PASSWORD"
EOF
        install -o root -g root -m 0600 /dev/stdin /etc/openslt/initial-admin-password <<EOF
$INITIAL_ADMIN_PASSWORD
EOF
        printf '[OpenSLT] Initial admin password saved to /etc/openslt/initial-admin-password\n'
    fi
fi

install -d -o root -g root -m 0755 "$(dirname -- "$DATABASE_MODE_FILE")"
printf '%s\n' "$DATABASE_MODE" \
    | install -o root -g root -m 0644 /dev/stdin "$DATABASE_MODE_FILE"

if [[ "$OPEN_FIREWALL" == true ]] \
    && command -v firewall-cmd >/dev/null 2>&1 \
    && systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-port=7777/tcp
    firewall-cmd --reload
fi

printf '[OpenSLT] Environment configuration completed. Next run: ./start.sh\n'
