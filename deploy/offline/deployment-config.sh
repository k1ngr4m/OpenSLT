#!/usr/bin/env bash

CANONICAL_ENV_FILE="/etc/openslt/openslt.env"
DEFAULT_BACKEND_PORT=4396
DEFAULT_FRONTEND_PORT=7777

environment_has_placeholder() {
    awk '
        /^[[:space:]]*#/ { next }
        /CHANGE_ME/ { found = 1 }
        END { exit(found ? 0 : 1) }
    ' "$1"
}

environment_value() {
    local key="$1"
    local env_file="$2"
    awk -v key="$key" '
        /^[[:space:]]*#/ { next }
        {
            line = $0
            sub(/^[[:space:]]*/, "", line)
            if (substr(line, 1, length(key)) != key) next
            value = substr(line, length(key) + 1)
            if (value !~ /^[[:space:]]*=/) next
            sub(/^[[:space:]]*=[[:space:]]*/, "", value)
            sub(/[[:space:]]*$/, "", value)
            first = substr(value, 1, 1)
            last = substr(value, length(value), 1)
            if (length(value) >= 2 && ((first == "\"" && last == "\"") || (first == "\047" && last == "\047"))) {
                value = substr(value, 2, length(value) - 2)
            }
            print value
            found = 1
            exit
        }
        END { if (!found) exit 1 }
    ' "$env_file"
}

environment_has_value() {
    local value
    value="$(environment_value "$1" "$2")" || return 1
    [[ -n "$value" ]]
}

load_deployment_ports() {
    local env_file="$1"
    BACKEND_PORT="$(environment_value BACKEND_PORT "$env_file" || printf '%s' "$DEFAULT_BACKEND_PORT")"
    FRONTEND_PORT="$(environment_value FRONTEND_PORT "$env_file" || printf '%s' "$DEFAULT_FRONTEND_PORT")"
    [[ "$BACKEND_PORT" =~ ^[0-9]+$ ]] || {
        printf 'BACKEND_PORT must be an integer: %s\n' "$BACKEND_PORT" >&2
        return 1
    }
    [[ "$FRONTEND_PORT" =~ ^[0-9]+$ ]] || {
        printf 'FRONTEND_PORT must be an integer: %s\n' "$FRONTEND_PORT" >&2
        return 1
    }
    BACKEND_PORT=$((10#$BACKEND_PORT))
    FRONTEND_PORT=$((10#$FRONTEND_PORT))
    ((BACKEND_PORT >= 1024 && BACKEND_PORT <= 65535)) || {
        printf 'BACKEND_PORT must be between 1024 and 65535.\n' >&2
        return 1
    }
    ((FRONTEND_PORT >= 1024 && FRONTEND_PORT <= 65535)) || {
        printf 'FRONTEND_PORT must be between 1024 and 65535.\n' >&2
        return 1
    }
    ((BACKEND_PORT != FRONTEND_PORT)) || {
        printf 'BACKEND_PORT and FRONTEND_PORT must be different.\n' >&2
        return 1
    }
}

validate_deployment_environment() {
    local env_file="$1"
    local database_port
    local has_database_url=false
    local split_count=0
    local key

    [[ -f "$env_file" ]] || {
        printf 'Production environment not found: %s\n' "$env_file" >&2
        return 1
    }
    if environment_has_placeholder "$env_file"; then
        printf 'The environment file still contains CHANGE_ME placeholders: %s\n' \
            "$env_file" >&2
        return 1
    fi
    environment_has_value DATABASE_URL "$env_file" && has_database_url=true
    for key in DATABASE_HOST DATABASE_PORT DATABASE_NAME DATABASE_USER DATABASE_PASSWORD; do
        environment_has_value "$key" "$env_file" && split_count=$((split_count + 1))
    done
    if [[ "$has_database_url" == true && "$split_count" -gt 0 ]]; then
        printf 'DATABASE_URL cannot be combined with split DATABASE_* fields.\n' >&2
        return 1
    fi
    if [[ "$has_database_url" == false && "$split_count" -ne 5 ]]; then
        printf 'Set DATABASE_URL or all of DATABASE_HOST, DATABASE_PORT, DATABASE_NAME, DATABASE_USER, and DATABASE_PASSWORD.\n' >&2
        return 1
    fi
    if [[ "$has_database_url" == false ]]; then
        database_port="$(environment_value DATABASE_PORT "$env_file")"
        [[ "$database_port" =~ ^[0-9]+$ ]] || {
            printf 'DATABASE_PORT must be an integer: %s\n' "$database_port" >&2
            return 1
        }
        database_port=$((10#$database_port))
        ((database_port >= 1 && database_port <= 65535)) || {
            printf 'DATABASE_PORT must be between 1 and 65535.\n' >&2
            return 1
        }
    fi
    load_deployment_ports "$env_file"
}

install_canonical_environment() {
    local source_file="$1"
    getent group openslt >/dev/null 2>&1 || groupadd --system openslt
    install -d -o root -g openslt -m 0750 /etc/openslt
    if [[ "$(readlink -f -- "$source_file")" == "$CANONICAL_ENV_FILE" ]]; then
        chown root:openslt "$CANONICAL_ENV_FILE"
        chmod 0640 "$CANONICAL_ENV_FILE"
    else
        install -o root -g openslt -m 0640 "$source_file" "$CANONICAL_ENV_FILE"
    fi
}

render_runtime_configuration() {
    local application_root="$1"
    sed -e "s/--port 4396/--port $BACKEND_PORT/" \
        "$application_root/deploy/systemd/openslt-api.service" \
        | install -o root -g root -m 0644 /dev/stdin /etc/systemd/system/openslt-api.service
    sed \
        -e "s/server 127.0.0.1:4396/server 127.0.0.1:$BACKEND_PORT/" \
        -e "s/listen 7777/listen $FRONTEND_PORT/" \
        "$application_root/deploy/nginx/openslt.conf" \
        | install -o root -g root -m 0644 /dev/stdin /etc/nginx/conf.d/openslt.conf
}
