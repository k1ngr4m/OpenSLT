#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_ROOT="/opt/openslt"
NODE_ROOT="/opt/openslt-node"
NPM_CACHE="/var/cache/openslt/npm"
SKIP_TESTS=false
RELOAD_NGINX=true

usage() {
    cat <<'EOF'
Usage: build-frontend.sh [options]

Reinstall the locked frontend dependencies from the bundled npm cache, run
tests, build frontend/dist, and reload the production Nginx service.

Options:
  --project-root DIR  Application source directory (default: /opt/openslt)
  --skip-tests        Build without running the frontend test suite
  --no-reload         Build and validate Nginx, but do not reload it
  -h, --help          Show this help

This command is fully offline. It only supports the package-lock.json that was
included when the offline bundle was created.
EOF
}

while (($#)); do
    case "$1" in
        --project-root)
            PROJECT_ROOT="$2"
            shift 2
            ;;
        --skip-tests)
            SKIP_TESTS=true
            shift
            ;;
        --no-reload)
            RELOAD_NGINX=false
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
    printf 'Run build-frontend.sh as root so it can update the production files.\n' >&2
    exit 1
}
FRONTEND_DIR="$PROJECT_ROOT/frontend"
NODE_BIN="$NODE_ROOT/bin/node"
NPM_BIN="$NODE_ROOT/bin/npm"
METADATA_FILE="$NODE_ROOT/METADATA"

[[ -x "$NODE_BIN" && -x "$NPM_BIN" ]] || {
    printf 'Bundled Node.js is not installed at %s.\n' "$NODE_ROOT" >&2
    printf 'Create and deploy an offline package with --bundle-node.\n' >&2
    exit 1
}
[[ -d "$NPM_CACHE/_cacache" ]] || {
    printf 'The offline npm cache is missing or incomplete: %s\n' "$NPM_CACHE" >&2
    exit 1
}
[[ -f "$METADATA_FILE" ]] || {
    printf 'Node.js bundle metadata is missing: %s\n' "$METADATA_FILE" >&2
    exit 1
}
[[ -f "$FRONTEND_DIR/package.json" && -f "$FRONTEND_DIR/package-lock.json" ]] || {
    printf 'Frontend package files are missing under %s.\n' "$FRONTEND_DIR" >&2
    exit 1
}

EXPECTED_LOCK_SHA="$(awk -F= '$1 == "package_lock_sha256" { print $2 }' "$METADATA_FILE")"
ACTUAL_LOCK_SHA="$(sha256sum "$FRONTEND_DIR/package-lock.json" | awk '{print $1}')"
[[ "$EXPECTED_LOCK_SHA" =~ ^[0-9a-fA-F]{64}$ ]] || {
    printf 'Invalid package_lock_sha256 in %s.\n' "$METADATA_FILE" >&2
    exit 1
}
[[ "${ACTUAL_LOCK_SHA,,}" == "${EXPECTED_LOCK_SHA,,}" ]] || {
    printf 'package-lock.json does not match the bundled npm cache.\n' >&2
    printf 'Create a new --bundle-node package after changing frontend dependencies.\n' >&2
    exit 1
}

export PATH="$NODE_ROOT/bin:$PATH"
NODE_VERSION="$($NODE_BIN --version)"
NPM_VERSION="$($NPM_BIN --version)"
[[ "$NODE_VERSION" =~ ^v20\. ]] || {
    printf 'The bundled runtime must be Node.js 20; found %s.\n' "$NODE_VERSION" >&2
    exit 1
}
NPM_MAJOR="${NPM_VERSION%%.*}"
[[ "$NPM_MAJOR" =~ ^[0-9]+$ && "$NPM_MAJOR" -ge 10 ]] || {
    printf 'OpenSLT requires npm >=10; found %s.\n' "$NPM_VERSION" >&2
    exit 1
}

printf '[OpenSLT] Installing locked frontend dependencies from the offline cache...\n'
"$NPM_BIN" --prefix "$FRONTEND_DIR" ci --offline \
    --cache "$NPM_CACHE" --include=dev --no-audit --no-fund
if [[ "$SKIP_TESTS" == false ]]; then
    printf '[OpenSLT] Running frontend tests...\n'
    "$NPM_BIN" --prefix "$FRONTEND_DIR" run test
fi
printf '[OpenSLT] Building the production frontend...\n'
"$NPM_BIN" --prefix "$FRONTEND_DIR" run build
[[ -f "$FRONTEND_DIR/dist/index.html" ]] || {
    printf 'Frontend build did not create dist/index.html.\n' >&2
    exit 1
}

if command -v restorecon >/dev/null 2>&1; then
    restorecon -Rv "$FRONTEND_DIR/dist"
fi
command -v nginx >/dev/null 2>&1 || {
    printf 'nginx is not installed; the build succeeded but cannot be validated.\n' >&2
    exit 1
}
nginx -t
if [[ "$RELOAD_NGINX" == true ]]; then
    systemctl reload nginx
    printf '[OpenSLT] Frontend rebuilt and Nginx reloaded.\n'
else
    printf '[OpenSLT] Frontend rebuilt; Nginx reload was skipped.\n'
fi
