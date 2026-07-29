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

usage() {
    cat <<'EOF'
Usage: configure.sh [options]

Run this once as root on the offline RHEL 7.9 application host.

Options:
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
        --mysql-defaults-file)
            MYSQL_DEFAULTS_FILE="$2"
            shift 2
            ;;
        --database-name)
            DATABASE_NAME="$2"
            shift 2
            ;;
        --database-user)
            DATABASE_USER="$2"
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
[[ -x "$PYTHON" ]] || {
    printf 'Required preinstalled Python executable not found: %s\n' "$PYTHON" >&2
    exit 1
}
PYTHON_VERSION="$("$PYTHON" -c 'import platform; print(platform.python_version())')" || {
    printf 'Unable to run the preinstalled Python executable: %s\n' "$PYTHON" >&2
    exit 1
}
"$PYTHON" -c \
    'import sys; raise SystemExit(0 if (3, 8, 2) <= sys.version_info[:3] < (3, 9) else 1)' || {
    printf 'OpenSLT requires preinstalled Python >=3.8.2,<3.9; found %s at %s\n' \
        "$PYTHON_VERSION" "$PYTHON" >&2
    exit 1
}
printf '[OpenSLT] Preinstalled Python accepted: %s (%s)\n' "$PYTHON_VERSION" "$PYTHON"

printf '[OpenSLT] Installing the bundled operating-system packages...\n'
"$BUNDLE_ROOT/install.sh" --rpms-only

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
PORT=8000
OPEN_BROWSER=false
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD="$INITIAL_ADMIN_PASSWORD"
EOF
    install -o root -g root -m 0600 /dev/stdin /etc/openslt/initial-admin-password <<EOF
$INITIAL_ADMIN_PASSWORD
EOF
    printf '[OpenSLT] Initial admin password saved to /etc/openslt/initial-admin-password\n'
fi

if [[ "$OPEN_FIREWALL" == true ]] \
    && command -v firewall-cmd >/dev/null 2>&1 \
    && systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-service=http
    firewall-cmd --reload
fi

printf '[OpenSLT] Environment configuration completed. Next run: ./start.sh\n'
