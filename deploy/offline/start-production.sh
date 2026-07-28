#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
BUNDLE_ROOT="$SCRIPT_DIR"
ENV_FILE="/etc/openslt/openslt.env"
FORCE_INSTALL=false

usage() {
    cat <<'EOF'
Usage: start.sh [options]

Install or upgrade the current offline bundle, migrate the database, and
start the production services. Re-running the same version only restarts them.

Options:
  --env-file FILE   Production environment path
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

[[ "$(id -u)" == "0" ]] || {
    printf 'Run start.sh as root.\n' >&2
    exit 1
}
[[ -f "$ENV_FILE" ]] || {
    printf 'Production environment not found. Run ./configure.sh first.\n' >&2
    exit 1
}
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

systemctl enable mariadb
systemctl start mariadb

if [[ "$FORCE_INSTALL" == true || "$INSTALLED_VERSION" != "$BUNDLE_VERSION" \
    || ! -x /opt/openslt/.venv/bin/uvicorn \
    || ! -f /etc/systemd/system/openslt-api.service \
    || ! -f /etc/nginx/conf.d/openslt.conf \
    || ! -f /opt/openslt/frontend/dist/index.html ]]; then
    printf '[OpenSLT] Installing bundle version %s...\n' "$BUNDLE_VERSION"
    "$BUNDLE_ROOT/install.sh" --env-file "$ENV_FILE" --no-start
else
    printf '[OpenSLT] Bundle version %s is already installed.\n' "$BUNDLE_VERSION"
    printf '[OpenSLT] Applying any pending database migrations...\n'
    /opt/openslt/.venv/bin/python - "$ENV_FILE" <<'PY'
import os
import subprocess
import sys

from dotenv import dotenv_values

values = dotenv_values(sys.argv[1])
missing = [key for key, value in values.items() if value is None]
if missing:
    raise SystemExit("Invalid environment entries: " + ", ".join(missing))
environment = os.environ.copy()
environment.update(values)
subprocess.run(
    ["/opt/openslt/.venv/bin/alembic", "upgrade", "head"],
    cwd="/opt/openslt",
    env=environment,
    check=True,
)
PY
fi

systemctl daemon-reload
systemctl enable openslt-api nginx
systemctl restart openslt-api
systemctl restart nginx

printf '[OpenSLT] Waiting for production services...\n'
for _ in {1..60}; do
    if curl --fail --silent --max-time 2 http://127.0.0.1:8000/health >/dev/null \
        && curl --fail --silent --max-time 2 http://127.0.0.1/ >/dev/null; then
        install -d -o openslt -g openslt -m 0750 /var/lib/openslt
        install -o root -g root -m 0644 "$BUNDLE_ROOT/VERSION" "$INSTALLED_VERSION_FILE"
        LAN_ADDRESS="$(hostname -I 2>/dev/null | awk '{print $1}')"
        printf '[OpenSLT] Production service is ready: http://%s/\n' "${LAN_ADDRESS:-127.0.0.1}"
        if [[ -f /etc/openslt/initial-admin-password ]]; then
            printf '[OpenSLT] Initial password file: /etc/openslt/initial-admin-password\n'
        fi
        exit 0
    fi
    sleep 1
done

journalctl -u openslt-api -n 100 --no-pager >&2
printf 'OpenSLT did not become healthy within 60 seconds.\n' >&2
exit 1
