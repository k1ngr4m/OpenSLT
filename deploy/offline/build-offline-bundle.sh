#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
PYTHON="/opt/rh/rh-python38/root/usr/bin/python3.8"
RPM_DIR=""
OUTPUT_DIR="$PROJECT_ROOT/release"
VERSION=""
SKIP_TESTS=false

usage() {
    cat <<'EOF'
Usage: build-offline-bundle.sh [options]

Options:
  --python PATH       Python >=3.8 executable on RHEL 7.9
  --rpm-dir DIR       RPM repository created by collect-rpms-rhel7.sh
  --output DIR        Output directory (default: release/)
  --version VERSION   Bundle version label
  --skip-tests        Skip the offline wheel reinstall and backend tests
  -h, --help          Show this help

frontend/dist must already contain a current production build. Build it on a
supported Node.js 20 host before running this script.
EOF
}

while (($#)); do
    case "$1" in
        --python)
            PYTHON="$2"
            shift 2
            ;;
        --rpm-dir)
            RPM_DIR="$2"
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
    printf 'Bundle creation must run on x86_64.\n' >&2
    exit 1
}
grep -Eq '^VERSION_ID="?7\.9"?$' /etc/os-release || {
    printf 'Bundle creation must run on RHEL 7.9.\n' >&2
    exit 1
}
[[ -x "$PYTHON" ]] || {
    printf 'Python executable not found: %s\n' "$PYTHON" >&2
    exit 1
}
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 8) else 1)' || {
    printf 'OpenSLT requires Python >=3.8.\n' >&2
    exit 1
}
[[ -f "$PROJECT_ROOT/frontend/dist/index.html" ]] || {
    printf 'frontend/dist is missing. Build the frontend before creating the bundle.\n' >&2
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
    printf 'frontend/dist is older than one or more frontend source files. Rebuild it first.\n' >&2
    find \
        "$PROJECT_ROOT/frontend/src" \
        "$PROJECT_ROOT/frontend/public" \
        "$PROJECT_ROOT/frontend/index.html" \
        "$PROJECT_ROOT/frontend/package.json" \
        "$PROJECT_ROOT/frontend/package-lock.json" \
        "$PROJECT_ROOT/frontend/vite.config.ts" \
        -type f -newer "$PROJECT_ROOT/frontend/dist/index.html" -print >&2
    exit 1
fi

if [[ -z "$VERSION" ]]; then
    if command -v git >/dev/null 2>&1 && git -C "$PROJECT_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        VERSION="$(date +%Y%m%d)-$(git -C "$PROJECT_ROOT" rev-parse --short HEAD)"
    else
        VERSION="$(date +%Y%m%d-%H%M%S)"
    fi
fi
[[ "$VERSION" =~ ^[A-Za-z0-9._-]+$ ]] || {
    printf 'Version may contain only letters, digits, dot, underscore, and hyphen.\n' >&2
    exit 1
}
if [[ -n "$RPM_DIR" && ! -d "$RPM_DIR/packages" ]]; then
    printf 'RPM directory must contain packages/: %s\n' "$RPM_DIR" >&2
    exit 1
fi

BUILD_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$BUILD_ROOT"' EXIT
BUNDLE_NAME="openslt-offline-rhel7-x86_64-$VERSION"
STAGING="$BUILD_ROOT/$BUNDLE_NAME"
mkdir -p "$STAGING/app" "$STAGING/wheelhouse" "$OUTPUT_DIR"

printf '[OpenSLT] Copying the application working tree...\n'
tar -C "$PROJECT_ROOT" \
    --exclude='./.git' \
    --exclude='./.env' \
    --exclude='./.venv' \
    --exclude='./.venv-portable' \
    --exclude='./.pytest_cache' \
    --exclude='./build' \
    --exclude='./release' \
    --exclude='./backend/data' \
    --exclude='./backend/logs' \
    --exclude='./frontend/node_modules' \
    --exclude='./frontend/.vite' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    -cf - . | tar -C "$STAGING/app" -xf -

BUILD_VENV="$BUILD_ROOT/build-venv"
"$PYTHON" -m venv "$BUILD_VENV"
"$BUILD_VENV/bin/python" -m pip install --upgrade 'pip==25.0.1' 'setuptools==75.3.0' 'wheel==0.45.1'
printf '[OpenSLT] Building the Python wheelhouse...\n'
"$BUILD_VENV/bin/python" -m pip wheel --wheel-dir "$STAGING/wheelhouse" "$PROJECT_ROOT[test]"

if [[ "$SKIP_TESTS" == false ]]; then
    VALIDATE_VENV="$BUILD_ROOT/validate-venv"
    "$PYTHON" -m venv "$VALIDATE_VENV"
    APP_WHEEL="$(find "$STAGING/wheelhouse" -maxdepth 1 -type f -name 'openslt-*.whl' -print -quit)"
    [[ -n "$APP_WHEEL" ]] || {
        printf 'The OpenSLT application wheel was not created.\n' >&2
        exit 1
    }
    printf '[OpenSLT] Validating a network-free wheel installation...\n'
    "$VALIDATE_VENV/bin/python" -m pip install --no-index --find-links "$STAGING/wheelhouse" "${APP_WHEEL}[test]"
    "$VALIDATE_VENV/bin/python" -m pip check
    (
        cd "$PROJECT_ROOT"
        "$VALIDATE_VENV/bin/python" -m pytest
    )
    "$VALIDATE_VENV/bin/python" -m pip freeze >"$STAGING/python-packages.txt"
fi

if [[ -n "$RPM_DIR" ]]; then
    printf '[OpenSLT] Adding the offline RPM repository...\n'
    cp -a "$RPM_DIR" "$STAGING/rpms"
fi

cp -p "$SCRIPT_DIR/install-offline.sh" "$STAGING/install.sh"
cp -p "$SCRIPT_DIR/configure-intranet-host.sh" "$STAGING/configure.sh"
cp -p "$SCRIPT_DIR/start-production.sh" "$STAGING/start.sh"
cp -p "$SCRIPT_DIR/openslt.env.example" "$STAGING/openslt.env.example"
cp -p "$SCRIPT_DIR/README-OFFLINE.md" "$STAGING/README-OFFLINE.md"
chmod 0755 "$STAGING/install.sh" "$STAGING/configure.sh" "$STAGING/start.sh"

printf '%s\n' "$VERSION" >"$STAGING/VERSION"
(
    cd "$STAGING"
    find . -type f ! -name SHA256SUMS -print0 \
        | LC_ALL=C sort -z \
        | xargs -0 sha256sum >SHA256SUMS
)

ARCHIVE="$OUTPUT_DIR/$BUNDLE_NAME.tar.gz"
tar -C "$BUILD_ROOT" -czf "$ARCHIVE" "$BUNDLE_NAME"
(
    cd "$OUTPUT_DIR"
    sha256sum "$BUNDLE_NAME.tar.gz" >"$BUNDLE_NAME.tar.gz.sha256"
)

printf '[OpenSLT] Offline bundle created: %s\n' "$ARCHIVE"
printf '[OpenSLT] Archive checksum: %s.sha256\n' "$ARCHIVE"
