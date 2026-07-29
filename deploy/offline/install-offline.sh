#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
BUNDLE_ROOT="$SCRIPT_DIR"
ENV_FILE=""
PYTHON="/opt/rh/rh-python38/root/usr/bin/python3.8"
INSTALL_RPMS=false
RPMS_ONLY=false
START_SERVICES=true
DATABASE_MODE=""
DATABASE_MODE_FILE="/etc/openslt/database-mode"
SKIP_DATABASE_RPMS=false

usage() {
    cat <<'EOF'
Usage: install.sh --env-file FILE [options]

Options:
  --env-file FILE     Completed production environment file (required)
  --python PATH       Python >=3.8 executable
  --database-mode MODE
                      existing (default), provision, or initialize
  --install-rpms      Install the bundled RPM repository before OpenSLT
  --rpms-only         Install bundled RPMs, then stop before application setup
  --skip-database-rpms
                      Do not install mariadb, mariadb-server, or mariadb-libs
  --no-start          Install and migrate, but do not start/restart services
  -h, --help          Show this help
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
        --install-rpms)
            INSTALL_RPMS=true
            shift
            ;;
        --rpms-only)
            INSTALL_RPMS=true
            RPMS_ONLY=true
            shift
            ;;
        --skip-database-rpms)
            SKIP_DATABASE_RPMS=true
            shift
            ;;
        --no-start)
            START_SERVICES=false
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
    existing)
        SKIP_DATABASE_RPMS=true
        ;;
    provision|initialize)
        ;;
    *)
        printf 'Invalid database mode: %s (expected existing, provision, or initialize)\n' \
            "$DATABASE_MODE" >&2
        exit 2
        ;;
esac

[[ "$(id -u)" == "0" ]] || {
    printf 'Run the installer as root.\n' >&2
    exit 1
}
[[ "$(uname -m)" == "x86_64" ]] || {
    printf 'OpenSLT offline bundle requires x86_64.\n' >&2
    exit 1
}
grep -Eq '^VERSION_ID="?7\.9"?$' /etc/os-release || {
    printf 'OpenSLT offline bundle requires RHEL 7.9.\n' >&2
    exit 1
}
environment_has_placeholder() {
    awk '
        /^[[:space:]]*#/ { next }
        /CHANGE_ME/ { found = 1 }
        END { exit(found ? 0 : 1) }
    ' "$1"
}
if [[ "$RPMS_ONLY" == false ]]; then
    [[ -n "$ENV_FILE" && -f "$ENV_FILE" ]] || {
        printf -- '--env-file must point to a completed production environment file.\n' >&2
        exit 1
    }
    if environment_has_placeholder "$ENV_FILE"; then
        printf 'The environment file still contains CHANGE_ME placeholders.\n' >&2
        exit 1
    fi
fi
[[ -f "$BUNDLE_ROOT/SHA256SUMS" ]] || {
    printf 'SHA256SUMS is missing from the bundle.\n' >&2
    exit 1
}

printf '[OpenSLT] Verifying bundle checksums...\n'
(
    cd "$BUNDLE_ROOT"
    sha256sum -c SHA256SUMS
)

if [[ "$INSTALL_RPMS" == true ]]; then
    [[ -d "$BUNDLE_ROOT/rpms/packages" ]] || {
        printf 'This bundle does not contain rpms/packages.\n' >&2
        exit 1
    }
    find "$BUNDLE_ROOT/rpms/keys" -maxdepth 1 -type f -exec rpm --import {} \;
    mapfile -t RPM_FILES < <(find "$BUNDLE_ROOT/rpms/packages" -maxdepth 1 -type f -name '*.rpm' -print | LC_ALL=C sort)
    ((${#RPM_FILES[@]})) || {
        printf 'No RPM files were found in the bundle.\n' >&2
        exit 1
    }
    if [[ "$SKIP_DATABASE_RPMS" == true ]]; then
        FILTERED_RPM_FILES=()
        for rpm_file in "${RPM_FILES[@]}"; do
            rpm_name="$(rpm -qp --queryformat '%{NAME}' "$rpm_file")"
            case "$rpm_name" in
                mariadb|mariadb-server|mariadb-libs)
                    printf '[OpenSLT] Skipping database RPM in existing mode: %s\n' "$rpm_name"
                    ;;
                *)
                    FILTERED_RPM_FILES+=("$rpm_file")
                    ;;
            esac
        done
        RPM_FILES=("${FILTERED_RPM_FILES[@]}")
        ((${#RPM_FILES[@]})) || {
            printf 'No non-database RPM files remain after filtering.\n' >&2
            exit 1
        }
    fi
    printf '[OpenSLT] Installing bundled RPMs with all external repositories disabled...\n'
    yum --disablerepo='*' localinstall -y "${RPM_FILES[@]}"
fi

if [[ "$RPMS_ONLY" == true ]]; then
    printf '[OpenSLT] RPM installation completed for database mode %s.\n' "$DATABASE_MODE"
    exit 0
fi

[[ -x "$PYTHON" ]] || {
    printf 'Python executable not found: %s\n' "$PYTHON" >&2
    exit 1
}
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 8) else 1)' || {
    printf 'OpenSLT requires Python >=3.8.\n' >&2
    exit 1
}
command -v nginx >/dev/null 2>&1 || {
    printf 'nginx is not installed. Install bundled RPMs or provision it first.\n' >&2
    exit 1
}
command -v curl >/dev/null 2>&1 || {
    printf 'curl is not installed. Install bundled RPMs or provision it first.\n' >&2
    exit 1
}

getent group openslt >/dev/null 2>&1 || groupadd --system openslt
id openslt >/dev/null 2>&1 || useradd --system --gid openslt --home-dir /var/lib/openslt --shell /sbin/nologin openslt
install -d -o openslt -g openslt -m 0750 /var/lib/openslt /var/lib/openslt/artifacts /var/log/openslt
install -d -o root -g openslt -m 0750 /etc/openslt
install -d -o root -g root -m 0755 /opt/openslt

printf '[OpenSLT] Installing application files...\n'
cp -a "$BUNDLE_ROOT/app/." /opt/openslt/
if [[ "$(readlink -f -- "$ENV_FILE")" == "/etc/openslt/openslt.env" ]]; then
    chown root:openslt /etc/openslt/openslt.env
    chmod 0640 /etc/openslt/openslt.env
else
    install -o root -g openslt -m 0640 "$ENV_FILE" /etc/openslt/openslt.env
fi

rm -rf -- /opt/openslt/.venv
"$PYTHON" -m venv /opt/openslt/.venv
APP_WHEEL="$(find "$BUNDLE_ROOT/wheelhouse" -maxdepth 1 -type f -name 'openslt-*.whl' -print -quit)"
[[ -n "$APP_WHEEL" ]] || {
    printf 'OpenSLT application wheel is missing.\n' >&2
    exit 1
}
/opt/openslt/.venv/bin/python -m pip install \
    --no-index \
    --find-links "$BUNDLE_ROOT/wheelhouse" \
    "$APP_WHEEL"
/opt/openslt/.venv/bin/python -m pip check

chown -R root:root /opt/openslt
chown -R openslt:openslt /var/lib/openslt /var/log/openslt

install -o root -g root -m 0644 /opt/openslt/deploy/systemd/openslt-api.service /etc/systemd/system/openslt-api.service
install -o root -g root -m 0644 /opt/openslt/deploy/nginx/openslt.conf /etc/nginx/conf.d/openslt.conf

if command -v semanage >/dev/null 2>&1; then
    semanage fcontext -a -t httpd_sys_content_t '/opt/openslt/frontend/dist(/.*)?' 2>/dev/null \
        || semanage fcontext -m -t httpd_sys_content_t '/opt/openslt/frontend/dist(/.*)?'
    restorecon -Rv /opt/openslt/frontend/dist
fi
if command -v getsebool >/dev/null 2>&1 && getenforce | grep -qE 'Enforcing|Permissive'; then
    setsebool -P httpd_can_network_connect 1
fi

printf '[OpenSLT] Applying database migrations...\n'
(
    cd /opt/openslt
    /opt/openslt/.venv/bin/python - /etc/openslt/openslt.env "$DATABASE_MODE" <<'PY'
import os
import sys

from dotenv import dotenv_values

values = dotenv_values(sys.argv[1])
missing = [key for key, value in values.items() if value is None]
if missing:
    raise SystemExit("Invalid environment entries: " + ", ".join(missing))
environment = os.environ.copy()
environment.update(values)
environment["AUTO_CREATE_DATABASE"] = "false" if sys.argv[2] == "existing" else "true"
alembic = "/opt/openslt/.venv/bin/alembic"
os.execve(alembic, [alembic, "upgrade", "head"], environment)
PY
)

systemctl daemon-reload
nginx -t

if [[ "$START_SERVICES" == true ]]; then
    systemctl enable openslt-api nginx
    systemctl restart openslt-api
    systemctl restart nginx
    printf '[OpenSLT] Waiting for the health endpoint...\n'
    for _ in {1..60}; do
        if curl --fail --silent --show-error --max-time 2 http://127.0.0.1:4396/health >/dev/null; then
            printf '[OpenSLT] OpenSLT is healthy.\n'
            exit 0
        fi
        sleep 1
    done
    journalctl -u openslt-api -n 100 --no-pager >&2
    printf 'OpenSLT did not become healthy within 60 seconds.\n' >&2
    exit 1
fi

printf '[OpenSLT] Installation completed without starting services.\n'
