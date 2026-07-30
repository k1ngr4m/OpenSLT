#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
BUNDLE_ROOT="$SCRIPT_DIR"
ENV_FILE="/etc/openslt/openslt.env"
PYTHON="/opt/rh/rh-python38/root/usr/bin/python3.8"
FORCE_INSTALL=false
DATABASE_MODE=""
DATABASE_MODE_FILE="/etc/openslt/database-mode"

[[ -f "$BUNDLE_ROOT/deployment-config.sh" ]] || {
    printf 'Bundle configuration helper is missing.\n' >&2
    exit 1
}
# shellcheck source=deployment-config.sh
source "$BUNDLE_ROOT/deployment-config.sh"

usage() {
    cat <<'EOF'
Usage: start.sh [options]

Install or upgrade the current offline bundle, migrate the database, and
start the production services. Re-running the same version only restarts them.

Options:
  --env-file FILE   Production environment path (default: /etc/openslt/openslt.env)
  --python PATH     Preinstalled Python >=3.8 executable
  --database-mode MODE
                    existing (default), provision, or initialize
  --reinstall       Reinstall even when this bundle version is already active
  -h, --help        Show this help
EOF
}

while (($#)); do
    case "$1" in
        --env-file)
            ENV_FILE="$2"
            shift 2
            ;;
        --python)
            PYTHON="$2"
            shift 2
            ;;
        --database-mode)
            DATABASE_MODE="$2"
            shift 2
            ;;
        --reinstall)
            FORCE_INSTALL=true
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

if [[ -z "$DATABASE_MODE" && -f "$DATABASE_MODE_FILE" ]]; then
    DATABASE_MODE="$(<"$DATABASE_MODE_FILE")"
fi
DATABASE_MODE="${DATABASE_MODE:-existing}"
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
    printf 'Run start.sh as root.\n' >&2
    exit 1
}
[[ -f "$ENV_FILE" ]] || {
    printf 'Production environment not found. Run ./configure.sh first.\n' >&2
    exit 1
}
validate_deployment_environment "$ENV_FILE"
install_canonical_environment "$ENV_FILE"
ENV_FILE="$CANONICAL_ENV_FILE"
[[ -f "$BUNDLE_ROOT/VERSION" ]] || {
    printf 'Bundle VERSION file is missing.\n' >&2
    exit 1
}
(
    cd "$BUNDLE_ROOT"
    sha256sum --quiet -c SHA256SUMS
)

BUNDLE_VERSION="$(<"$BUNDLE_ROOT/VERSION")"
INSTALLED_VERSION_FILE="/var/lib/openslt/installed-bundle-version"
INSTALLED_VERSION=""
[[ -f "$INSTALLED_VERSION_FILE" ]] && INSTALLED_VERSION="$(<"$INSTALLED_VERSION_FILE")"

if [[ "$DATABASE_MODE" != "existing" ]]; then
    systemctl enable mariadb
    systemctl start mariadb
else
    printf '[OpenSLT] Existing database mode: local MariaDB service management skipped.\n'
fi

NODE_REINSTALL_REQUIRED=false
if [[ -d "$BUNDLE_ROOT/node-runtime" ]] \
    && { [[ ! -x /opt/openslt-node/bin/node ]] \
        || [[ ! -f /opt/openslt-node/METADATA ]] \
        || [[ ! -d /var/cache/openslt/npm/_cacache ]] \
        || [[ ! -x /opt/openslt/build-frontend.sh ]]; }; then
    NODE_REINSTALL_REQUIRED=true
fi
if [[ "$FORCE_INSTALL" == true || "$INSTALLED_VERSION" != "$BUNDLE_VERSION" \
    || ! -x /opt/openslt/.venv/bin/uvicorn \
    || ! -f /etc/systemd/system/openslt-api.service \
    || ! -f /etc/nginx/conf.d/openslt.conf \
    || ! -f /opt/openslt/frontend/dist/index.html \
    || "$NODE_REINSTALL_REQUIRED" == true ]]; then
    printf '[OpenSLT] Installing bundle version %s...\n' "$BUNDLE_VERSION"
    "$BUNDLE_ROOT/install.sh" \
        --env-file "$ENV_FILE" \
        --python "$PYTHON" \
        --database-mode "$DATABASE_MODE" \
        --no-start
else
    printf '[OpenSLT] Bundle version %s is already installed.\n' "$BUNDLE_VERSION"
    printf '[OpenSLT] Applying any pending database migrations...\n'
    /opt/openslt/.venv/bin/python - "$ENV_FILE" <<'PY'
import os
import subprocess
import sys

from dotenv import dotenv_values

values = dotenv_values(sys.argv[1], interpolate=False)
missing = [key for key, value in values.items() if value is None]
if missing:
    raise SystemExit("Invalid environment entries: " + ", ".join(missing))
environment = os.environ.copy()
for key in (
    "DATABASE_URL",
    "DATABASE_HOST",
    "DATABASE_PORT",
    "DATABASE_NAME",
    "DATABASE_USER",
    "DATABASE_PASSWORD",
    "AUTO_CREATE_DATABASE",
    "JWT_SECRET",
    "CREDENTIAL_ENCRYPTION_KEY",
    "BACKEND_PORT",
    "FRONTEND_PORT",
    "INITIAL_ADMIN_USERNAME",
    "INITIAL_ADMIN_PASSWORD",
):
    environment.pop(key, None)
environment.update(values)
environment.update(
    {
        "ENVIRONMENT": "production",
        "TZ": "Asia/Shanghai",
        "ARTIFACT_ROOT": "/var/lib/openslt/artifacts",
        "LOG_DIR": "/var/log/openslt",
        "LOG_LEVEL": "INFO",
        "APP_LOG_RETENTION_DAYS": "90",
        "AUDIT_LOG_RETENTION_DAYS": "365",
        "OBSERVABILITY_BODY_LIMIT_BYTES": "65536",
        "OBSERVABILITY_SQL_LIMIT_BYTES": "32768",
        "OBSERVABILITY_SQL_PARAMS_LIMIT_BYTES": "8192",
        "OBSERVABILITY_QUEUE_SIZE": "10000",
        "OBSERVABILITY_HOT_RETENTION_DAYS": "30",
        "OBSERVABILITY_ARCHIVE_RETENTION_DAYS": "90",
        "ENABLE_INTERNAL_SCHEDULER": "true",
    }
)
subprocess.run(
    ["/opt/openslt/.venv/bin/alembic", "upgrade", "head"],
    cwd="/opt/openslt",
    env=environment,
    check=True,
)
PY
fi

install -d -o openslt -g openslt -m 0700 /var/lib/openslt/secrets
chown -R openslt:openslt /var/lib/openslt /var/log/openslt
find /var/lib/openslt/secrets -maxdepth 1 -type f -exec chmod 0600 {} \;
render_runtime_configuration /opt/openslt

systemctl daemon-reload
nginx -t
systemctl enable openslt-api nginx
systemctl restart openslt-api
systemctl restart nginx

printf '[OpenSLT] Waiting for production services...\n'
for _ in {1..60}; do
    if curl --fail --silent --max-time 2 \
        "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null \
        && curl --fail --silent --max-time 2 \
            "http://127.0.0.1:$FRONTEND_PORT/" >/dev/null; then
        install -d -o openslt -g openslt -m 0750 /var/lib/openslt
        install -o root -g root -m 0644 "$BUNDLE_ROOT/VERSION" "$INSTALLED_VERSION_FILE"
        LAN_ADDRESS="$(hostname -I 2>/dev/null | awk '{print $1}')"
        printf '[OpenSLT] Production service is ready: http://%s:%s/\n' \
            "${LAN_ADDRESS:-127.0.0.1}" "$FRONTEND_PORT"
        exit 0
    fi
    sleep 1
done

journalctl -u openslt-api -n 100 --no-pager >&2
printf 'OpenSLT did not become healthy within 60 seconds.\n' >&2
exit 1
