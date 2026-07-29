#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
PYTHON="/opt/rh/rh-python38/root/usr/bin/python3.8"
OUTPUT_DIR="$PROJECT_ROOT/release"
VERSION=""
SKIP_TESTS=false
NGINX_REPO_URL=""

usage() {
    cat <<'EOF'
Usage: make-offline-package.sh [options]

Run this once on the internet-connected RHEL 7.9 x86_64 packaging host.

Options:
  --python PATH       Python >=3.8 executable
  --output DIR        Output directory (default: release/)
  --version VERSION   Unique release label (default: date + Git commit)
  --nginx-repo-url URL
                      Alternate RHEL 7 Nginx repository base URL
  --skip-tests        Skip backend tests during packaging
  -h, --help          Show this help

The current frontend/dist must already have been built on a Node.js 20+ host.
EOF
}

while (($#)); do
    case "$1" in
        --python)
            PYTHON="$2"
            shift 2
            ;;
        --output)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --version)
            VERSION="$2"
            shift 2
            ;;
        --nginx-repo-url)
            NGINX_REPO_URL="$2"
            shift 2
            ;;
        --skip-tests)
            SKIP_TESTS=true
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

[[ "$(uname -m)" == "x86_64" ]] || {
    printf 'This script must run on x86_64.\n' >&2
    exit 1
}
grep -Eq '^VERSION_ID="?7\.9"?$' /etc/os-release || {
    printf 'This script must run on RHEL 7.9.\n' >&2
    exit 1
}
[[ -f "$PROJECT_ROOT/frontend/dist/index.html" ]] || {
    printf 'frontend/dist is missing. Build it on a Node.js 20+ host first.\n' >&2
    exit 1
}
[[ -x "$PYTHON" ]] || {
    printf 'Python executable not found: %s\n' "$PYTHON" >&2
    exit 1
}
if find \
    "$PROJECT_ROOT/frontend/src" \
    "$PROJECT_ROOT/frontend/public" \
    "$PROJECT_ROOT/frontend/index.html" \
    "$PROJECT_ROOT/frontend/package.json" \
    "$PROJECT_ROOT/frontend/package-lock.json" \
    "$PROJECT_ROOT/frontend/vite.config.ts" \
    -type f -newer "$PROJECT_ROOT/frontend/dist/index.html" -print -quit | grep -q .; then
    printf 'frontend/dist is stale. Rebuild it on the Node.js host first.\n' >&2
    exit 1
fi

if ! command -v repotrack >/dev/null 2>&1 || ! command -v createrepo >/dev/null 2>&1; then
    [[ "$(id -u)" == "0" ]] || {
        printf 'Run as root once so yum-utils and createrepo can be installed.\n' >&2
        exit 1
    }
    printf '[OpenSLT] Installing online packaging tools...\n'
    yum install -y yum-utils createrepo
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf -- "$WORK_DIR"' EXIT
RPM_DIR="$WORK_DIR/rpms"

printf '[OpenSLT] Collecting the complete RHEL 7 RPM dependency set...\n'
COLLECT_ARGS=(--output "$RPM_DIR")
[[ -n "$NGINX_REPO_URL" ]] && COLLECT_ARGS+=(--nginx-repo-url "$NGINX_REPO_URL")
"$SCRIPT_DIR/collect-rpms-rhel7.sh" "${COLLECT_ARGS[@]}"

BUILD_ARGS=(
    --python "$PYTHON"
    --rpm-dir "$RPM_DIR"
    --output "$OUTPUT_DIR"
)
[[ -n "$VERSION" ]] && BUILD_ARGS+=(--version "$VERSION")
[[ "$SKIP_TESTS" == true ]] && BUILD_ARGS+=(--skip-tests)

printf '[OpenSLT] Building and validating the offline application bundle...\n'
"$SCRIPT_DIR/build-offline-bundle.sh" "${BUILD_ARGS[@]}"

printf '[OpenSLT] Done. Transfer the .tar.gz and .sha256 files from %s.\n' "$OUTPUT_DIR"
