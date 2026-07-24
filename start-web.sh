#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_ROOT="$PROJECT_ROOT/frontend"
VENV_ROOT="$PROJECT_ROOT/.venv"
PYTHON="$VENV_ROOT/bin/python"
SESSION_NAME="openslt"
API_HOST="127.0.0.1"
API_PORT="8000"
WEB_BIND_HOST="0.0.0.0"
WEB_PORT="5173"
API_URL="http://${API_HOST}:${API_PORT}"
WEB_LOCAL_URL="http://127.0.0.1:${WEB_PORT}"
STARTUP_TIMEOUT_SECONDS=45

if [[ -t 1 ]]; then
    COLOR_CYAN=$'\033[36m'
    COLOR_GREEN=$'\033[32m'
    COLOR_RED=$'\033[31m'
    COLOR_RESET=$'\033[0m'
else
    COLOR_CYAN=""
    COLOR_GREEN=""
    COLOR_RED=""
    COLOR_RESET=""
fi

info() {
    printf '%s[OpenSLT]%s %s\n' "$COLOR_CYAN" "$COLOR_RESET" "$*"
}

success() {
    printf '%s[OpenSLT]%s %s\n' "$COLOR_GREEN" "$COLOR_RESET" "$*"
}

error() {
    printf '%s[OpenSLT] ERROR:%s %s\n' "$COLOR_RED" "$COLOR_RESET" "$*" >&2
}

die() {
    error "$*"
    exit 1
}

usage() {
    cat <<'EOF'
Usage: ./start-web.sh [command] [target]

Commands:
  start                 Prepare dependencies and start OpenSLT (default)
  stop                  Stop the OpenSLT tmux session
  restart               Stop and start OpenSLT again
  status                Show tmux window and health status
  attach                Attach to the OpenSLT tmux session
  logs [backend|frontend]
                        Show recent output from one or both services
  help                  Show this help
EOF
}

require_command() {
    local command_name="$1"
    local install_hint="${2:-Install it and run this command again.}"
    command -v "$command_name" >/dev/null 2>&1 || die \
        "Required command '$command_name' was not found. $install_hint"
}

python_is_supported() {
    local candidate="$1"
    "$candidate" -c \
        'import sys; raise SystemExit(0 if (3, 8, 2) <= sys.version_info[:3] < (3, 9) else 1)' \
        >/dev/null 2>&1
}

find_bootstrap_python() {
    local candidate
    for candidate in python3.8 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 && python_is_supported "$candidate"; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

require_node_runtime() {
    local node_version npm_version node_major npm_major
    require_command node "Install Node.js 20 or newer."
    require_command npm "Install npm 10 or newer."

    node_version="$(node --version)"
    npm_version="$(npm --version)"
    node_major="${node_version#v}"
    node_major="${node_major%%.*}"
    npm_major="${npm_version%%.*}"
    [[ "$node_major" =~ ^[0-9]+$ ]] || die "Could not parse Node.js version: $node_version"
    [[ "$npm_major" =~ ^[0-9]+$ ]] || die "Could not parse npm version: $npm_version"
    (( node_major >= 20 )) || die "Node.js 20+ is required; found $node_version."
    (( npm_major >= 10 )) || die "npm 10+ is required; found $npm_version."
}

file_sha256() {
    "$PYTHON" - "$1" <<'PY'
import hashlib
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
digest = hashlib.sha256()
with path.open("rb") as source:
    for block in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(block)
print(digest.hexdigest())
PY
}

session_exists() {
    tmux has-session -t "=$SESSION_NAME" 2>/dev/null
}

window_exists() {
    tmux list-windows -t "=$SESSION_NAME" -F '#{window_name}' 2>/dev/null \
        | grep -Fxq -- "$1"
}

window_is_alive() {
    local state
    window_exists "$1" || return 1
    state="$(tmux display-message -p -t "$SESSION_NAME:$1.0" '#{pane_dead}' 2>/dev/null)"
    [[ "$state" == "0" ]]
}

api_is_healthy() {
    local body
    body="$(curl --fail --silent --show-error --max-time 2 "$API_URL/health" 2>/dev/null)" \
        || return 1
    [[ "$body" == *'"status":"ok"'* && "$body" == *'"service":"openslt-api"'* ]]
}

web_is_healthy() {
    curl --fail --silent --show-error --max-time 2 "$WEB_LOCAL_URL" >/dev/null 2>&1
}

port_is_in_use() {
    local host="$1"
    local port="$2"
    "$PYTHON" - "$host" "$port" <<'PY'
import socket
import sys

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
    probe.settimeout(0.3)
    raise SystemExit(0 if probe.connect_ex((sys.argv[1], int(sys.argv[2]))) == 0 else 1)
PY
}

recent_logs() {
    local target="$1"
    printf '\n===== %s =====\n' "$target"
    tmux capture-pane -p -t "$SESSION_NAME:$target.0" -S -200 2>/dev/null \
        || printf 'No output is available for %s.\n' "$target"
}

show_startup_failure() {
    local service="$1"
    error "$service did not become ready within ${STARTUP_TIMEOUT_SECONDS} seconds."
    recent_logs "$service" >&2
    error "The tmux session was left running for inspection. Use './start-web.sh restart' to retry."
}

wait_until_ready() {
    local service="$1"
    local health_function="$2"
    local attempts=$((STARTUP_TIMEOUT_SECONDS * 4))
    local attempt

    for ((attempt = 1; attempt <= attempts; attempt++)); do
        if "$health_function"; then
            return 0
        fi
        if ! window_is_alive "$service"; then
            error "$service exited during startup."
            recent_logs "$service" >&2
            return 1
        fi
        sleep 0.25
    done
    show_startup_failure "$service"
    return 1
}

detect_lan_address() {
    local address=""
    if command -v hostname >/dev/null 2>&1; then
        address="$(hostname -I 2>/dev/null | awk '{
            for (i = 1; i <= NF; i++) {
                if ($i ~ /^[0-9]+\./ && $i !~ /^127\./) {
                    print $i
                    exit
                }
            }
        }')"
    fi
    printf '%s' "${address:-127.0.0.1}"
}

print_ready() {
    local lan_address
    lan_address="$(detect_lan_address)"
    printf '\n'
    success "OpenSLT web is ready: http://${lan_address}:${WEB_PORT}"
    printf 'API documentation (server local): %s/docs\n' "$API_URL"
    printf 'tmux session: %s\n' "$SESSION_NAME"
    printf '\nManagement commands:\n'
    printf '  ./start-web.sh status\n'
    printf '  ./start-web.sh attach\n'
    printf '  ./start-web.sh logs\n'
    printf '  ./start-web.sh restart\n'
    printf '  ./start-web.sh stop\n\n'
}

prepare_environment() {
    local bootstrap_python pyproject_hash python_stamp installed_python_hash
    local package_hash node_stamp installed_package_hash

    require_command tmux "Install tmux with your Linux package manager."
    require_command curl "Install curl with your Linux package manager."
    require_node_runtime

    cd "$PROJECT_ROOT"
    if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
        [[ -f "$PROJECT_ROOT/.env.example" ]] || die ".env.example is missing."
        info "Creating .env from .env.example..."
        (umask 077 && cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env")
    fi

    if [[ ! -x "$PYTHON" ]]; then
        if [[ -e "$VENV_ROOT" ]]; then
            die "$VENV_ROOT exists but does not contain an executable Python. Remove it and retry."
        fi
        bootstrap_python="$(find_bootstrap_python)" || die \
            "Python 3.8.2 (3.8.x) was not found. Install it and retry."
        info "Creating the Python virtual environment..."
        "$bootstrap_python" -m venv "$VENV_ROOT"
        "$PYTHON" -m pip install --upgrade pip
    elif ! python_is_supported "$PYTHON"; then
        die "The existing .venv does not use Python 3.8.2 (3.8.x). Remove .venv and retry."
    fi

    pyproject_hash="$(file_sha256 "$PROJECT_ROOT/pyproject.toml")"
    python_stamp="$VENV_ROOT/.openslt-pyproject.sha256"
    installed_python_hash=""
    [[ -f "$python_stamp" ]] && installed_python_hash="$(<"$python_stamp")"
    if [[ "$installed_python_hash" != "$pyproject_hash" ]] \
        || ! (cd "$PROJECT_ROOT" && "$PYTHON" -c 'import alembic, fastapi, uvicorn, app' \
            >/dev/null 2>&1); then
        info "Installing backend dependencies..."
        "$PYTHON" -m pip install --editable "$PROJECT_ROOT"
        printf '%s\n' "$pyproject_hash" >"$python_stamp"
    fi

    [[ -f "$FRONTEND_ROOT/package-lock.json" ]] || die "frontend/package-lock.json is missing."
    package_hash="$(file_sha256 "$FRONTEND_ROOT/package-lock.json")"
    node_stamp="$FRONTEND_ROOT/node_modules/.openslt-package-lock.sha256"
    installed_package_hash=""
    [[ -f "$node_stamp" ]] && installed_package_hash="$(<"$node_stamp")"
    if [[ "$installed_package_hash" != "$package_hash" ]] \
        || [[ ! -x "$FRONTEND_ROOT/node_modules/.bin/vite" ]]; then
        info "Installing frontend dependencies..."
        (cd "$FRONTEND_ROOT" && npm ci --no-audit --no-fund)
        printf '%s\n' "$package_hash" >"$node_stamp"
    fi

    info "Applying database migrations..."
    (cd "$PROJECT_ROOT" && "$PYTHON" -m alembic upgrade head)
}

start_services() {
    local backend_command frontend_command

    require_command tmux "Install tmux with your Linux package manager."
    require_command curl "Install curl with your Linux package manager."
    if session_exists; then
        if window_is_alive backend && window_is_alive frontend \
            && api_is_healthy && web_is_healthy; then
            info "The OpenSLT tmux session is already running."
            print_ready
            return 0
        fi
        die "The '$SESSION_NAME' tmux session exists but is not healthy. Run './start-web.sh status', './start-web.sh logs', or './start-web.sh restart'."
    fi

    prepare_environment
    port_is_in_use "$API_HOST" "$API_PORT" \
        && die "Port $API_PORT is already in use by a process outside the '$SESSION_NAME' session."
    port_is_in_use "127.0.0.1" "$WEB_PORT" \
        && die "Port $WEB_PORT is already in use by a process outside the '$SESSION_NAME' session."

    printf -v backend_command \
        'exec %q -m uvicorn app.main:app --app-dir backend --host %q --port %q' \
        "$PYTHON" "$API_HOST" "$API_PORT"
    printf -v frontend_command \
        'exec npm run dev -- --host %q --port %q --strictPort' \
        "$WEB_BIND_HOST" "$WEB_PORT"

    info "Starting the backend in tmux session '$SESSION_NAME'..."
    tmux new-session -d -s "$SESSION_NAME" -n backend -c "$PROJECT_ROOT"
    if ! tmux new-window -d -t "=$SESSION_NAME" -n frontend -c "$FRONTEND_ROOT"; then
        tmux kill-session -t "=$SESSION_NAME" 2>/dev/null || true
        die "Failed to create the frontend tmux window."
    fi
    tmux set-option -w -t "$SESSION_NAME:backend" remain-on-exit on >/dev/null
    tmux set-option -w -t "$SESSION_NAME:frontend" remain-on-exit on >/dev/null
    if ! tmux respawn-pane -k -t "$SESSION_NAME:backend.0" "$backend_command" \
        || ! tmux respawn-pane -k -t "$SESSION_NAME:frontend.0" "$frontend_command"; then
        tmux kill-session -t "=$SESSION_NAME" 2>/dev/null || true
        die "Failed to start one of the tmux service windows."
    fi
    tmux select-window -t "$SESSION_NAME:backend"

    info "Waiting for the API..."
    wait_until_ready backend api_is_healthy || return 1
    info "Waiting for the web client..."
    wait_until_ready frontend web_is_healthy || return 1
    print_ready
}

stop_services() {
    require_command tmux "Install tmux with your Linux package manager."
    if ! session_exists; then
        info "The OpenSLT tmux session is not running."
        return 0
    fi
    info "Stopping tmux session '$SESSION_NAME'..."
    tmux kill-session -t "=$SESSION_NAME"
    success "OpenSLT has stopped."
}

status_services() {
    local unhealthy=0 window pane_dead exit_status
    require_command tmux "Install tmux with your Linux package manager."
    require_command curl "Install curl with your Linux package manager."
    if ! session_exists; then
        info "tmux session: stopped"
        return 1
    fi

    info "tmux session: running"
    for window in backend frontend; do
        if ! window_exists "$window"; then
            printf '  %-8s missing\n' "$window"
            unhealthy=1
            continue
        fi
        pane_dead="$(tmux display-message -p -t "$SESSION_NAME:$window.0" '#{pane_dead}')"
        exit_status="$(tmux display-message -p -t "$SESSION_NAME:$window.0" '#{pane_dead_status}')"
        if [[ "$pane_dead" == "0" ]]; then
            printf '  %-8s running\n' "$window"
        else
            printf '  %-8s exited (status %s)\n' "$window" "${exit_status:-unknown}"
            unhealthy=1
        fi
    done

    if api_is_healthy; then
        printf '  API      healthy (%s/health)\n' "$API_URL"
    else
        printf '  API      unavailable\n'
        unhealthy=1
    fi
    if web_is_healthy; then
        printf '  Web      healthy (%s)\n' "$WEB_LOCAL_URL"
    else
        printf '  Web      unavailable\n'
        unhealthy=1
    fi
    return "$unhealthy"
}

attach_session() {
    require_command tmux "Install tmux with your Linux package manager."
    session_exists || die "The OpenSLT tmux session is not running."
    if [[ -n "${TMUX:-}" ]]; then
        exec tmux switch-client -t "=$SESSION_NAME"
    fi
    exec tmux attach-session -t "=$SESSION_NAME"
}

show_logs() {
    local target="${1:-}"
    require_command tmux "Install tmux with your Linux package manager."
    session_exists || die "The OpenSLT tmux session is not running."
    case "$target" in
        "")
            recent_logs backend
            recent_logs frontend
            ;;
        backend|frontend)
            window_exists "$target" || die "The '$target' tmux window does not exist."
            recent_logs "$target"
            ;;
        *)
            die "Unknown log target '$target'. Use backend or frontend."
            ;;
    esac
}

main() {
    local command_name="${1:-start}"
    case "$command_name" in
        start)
            [[ $# -le 1 ]] || die "The start command does not accept extra arguments."
            start_services
            ;;
        stop)
            [[ $# -le 1 ]] || die "The stop command does not accept extra arguments."
            stop_services
            ;;
        restart)
            [[ $# -le 1 ]] || die "The restart command does not accept extra arguments."
            stop_services
            start_services
            ;;
        status)
            [[ $# -le 1 ]] || die "The status command does not accept extra arguments."
            status_services
            ;;
        attach)
            [[ $# -le 1 ]] || die "The attach command does not accept extra arguments."
            attach_session
            ;;
        logs)
            [[ $# -le 2 ]] || die "Usage: ./start-web.sh logs [backend|frontend]"
            show_logs "${2:-}"
            ;;
        help|-h|--help)
            usage
            ;;
        *)
            usage >&2
            die "Unknown command '$command_name'."
            ;;
    esac
}

main "$@"
