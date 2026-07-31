#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
PYTHON="/opt/rh/rh-python38/root/usr/bin/python3.8"
RPM_DIR=""
OUTPUT_DIR="$PROJECT_ROOT/release"
VERSION=""
SKIP_TESTS=false
CACHE_DIR=""
BUNDLE_PYTHON=false
PYTHON_RUNTIME_ROOT="/opt/rh/rh-python38"
BUNDLE_NODE=false
NODE_VERSION="20.20.2"
NODE_BASE_URL="https://unofficial-builds.nodejs.org/download/release"
NODE_ARCHIVE=""
NODE_SHASUMS=""

usage() {
    cat <<'EOF'
Usage: build-offline-bundle.sh [options]

Options:
  --python PATH       Python >=3.8 executable on RHEL 7.9
  --rpm-dir DIR       RPM repository created by collect-rpms-rhel7.sh
  --output DIR        Output directory (default: release/)
  --version VERSION   Assert the canonical VERSION value (optional)
  --cache-dir DIR     Reuse Node.js, npm, and pip caches between runs
  --bundle-python     Include /opt/rh/rh-python38 for Python bootstrap
  --bundle-node       Include Node.js, npm, and a verified offline npm cache
  --node-version VER  linux-x64-glibc-217 Node version (default: 20.20.2)
  --node-base-url URL Unofficial Node.js release base URL
  --node-archive FILE Use a predownloaded Node .tar.gz archive
  --node-shasums FILE Use a predownloaded SHASUMS256.txt (required with archive)
  --skip-tests        Skip Python wheel validation and test suites
  -h, --help          Show this help

Without --bundle-node, frontend/dist must already contain a current production
build. With --bundle-node, the bundled runtime builds and validates it.
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
        --cache-dir)
            CACHE_DIR="$2"
            shift 2
            ;;
        --bundle-python)
            BUNDLE_PYTHON=true
            shift
            ;;
        --bundle-node)
            BUNDLE_NODE=true
            shift
            ;;
        --node-version)
            NODE_VERSION="$2"
            shift 2
            ;;
        --node-base-url)
            NODE_BASE_URL="$2"
            shift 2
            ;;
        --node-archive)
            NODE_ARCHIVE="$2"
            shift 2
            ;;
        --node-shasums)
            NODE_SHASUMS="$2"
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
PROJECT_VERSION="$("$PYTHON" "$PROJECT_ROOT/tools/release_metadata.py" --version)" || {
    printf 'OpenSLT release metadata validation failed.\n' >&2
    exit 1
}
if [[ -n "$VERSION" && "$VERSION" != "$PROJECT_VERSION" ]]; then
    printf 'Requested bundle version %s does not match project VERSION %s.\n' \
        "$VERSION" "$PROJECT_VERSION" >&2
    exit 2
fi
VERSION="$PROJECT_VERSION"
if [[ -n "$CACHE_DIR" ]]; then
    mkdir -p "$CACHE_DIR"
    CACHE_DIR="$(cd -- "$CACHE_DIR" && pwd)"
    case "$CACHE_DIR/" in
        "$PROJECT_ROOT"/*)
            printf -- '--cache-dir must be outside the project root: %s\n' "$CACHE_DIR" >&2
            exit 2
            ;;
    esac
    export PIP_CACHE_DIR="$CACHE_DIR/pip"
fi
if [[ "$BUNDLE_PYTHON" == true ]]; then
    [[ -d "$PYTHON_RUNTIME_ROOT" ]] || {
        printf 'The rh-python38 runtime is missing: %s\n' "$PYTHON_RUNTIME_ROOT" >&2
        exit 1
    }
    RESOLVED_PYTHON="$(readlink -f -- "$PYTHON")"
    [[ "$RESOLVED_PYTHON" == "$PYTHON_RUNTIME_ROOT"/* ]] || {
        printf '%s must be inside %s when using --bundle-python.\n' \
            "$PYTHON" "$PYTHON_RUNTIME_ROOT" >&2
        exit 1
    }
fi
if [[ "$BUNDLE_NODE" == true ]]; then
    NODE_VERSION="${NODE_VERSION#v}"
    [[ "$NODE_VERSION" =~ ^20\.[0-9]+\.[0-9]+$ ]] || {
        printf 'Node version must be a complete Node.js 20 release such as 20.20.2.\n' >&2
        exit 2
    }
    if [[ -n "$NODE_ARCHIVE" || -n "$NODE_SHASUMS" ]]; then
        [[ -f "$NODE_ARCHIVE" && -f "$NODE_SHASUMS" ]] || {
            printf -- '--node-archive and --node-shasums must name existing files together.\n' >&2
            exit 2
        }
    fi
else
    if [[ -n "$NODE_ARCHIVE" || -n "$NODE_SHASUMS" || "$NODE_VERSION" != "20.20.2" \
        || "$NODE_BASE_URL" != "https://unofficial-builds.nodejs.org/download/release" ]]; then
        printf 'Node download options require --bundle-node.\n' >&2
        exit 2
    fi
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
        "$PROJECT_ROOT/frontend/release-metadata.config.ts" \
        "$PROJECT_ROOT/VERSION" \
        "$PROJECT_ROOT/RELEASES.json" \
        -type f -newer "$PROJECT_ROOT/frontend/dist/index.html" -print -quit | grep -q .; then
        printf 'frontend/dist is older than one or more frontend source files. Rebuild it first.\n' >&2
        find \
            "$PROJECT_ROOT/frontend/src" \
            "$PROJECT_ROOT/frontend/public" \
            "$PROJECT_ROOT/frontend/index.html" \
            "$PROJECT_ROOT/frontend/package.json" \
            "$PROJECT_ROOT/frontend/package-lock.json" \
            "$PROJECT_ROOT/frontend/vite.config.ts" \
            "$PROJECT_ROOT/frontend/release-metadata.config.ts" \
            "$PROJECT_ROOT/VERSION" \
            "$PROJECT_ROOT/RELEASES.json" \
            -type f -newer "$PROJECT_ROOT/frontend/dist/index.html" -print >&2
        exit 1
    fi
fi

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
    --exclude='./frontend/*.tsbuildinfo' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    -cf - . | tar -C "$STAGING/app" -xf -

if [[ "$BUNDLE_NODE" == true ]]; then
    NODE_ARCHIVE_NAME="node-v${NODE_VERSION}-linux-x64-glibc-217.tar.gz"
    NODE_RELEASE_URL="${NODE_BASE_URL%/}/v${NODE_VERSION}"
    DOWNLOADED_NODE_ARCHIVE="$BUILD_ROOT/$NODE_ARCHIVE_NAME"
    DOWNLOADED_NODE_SHASUMS="$BUILD_ROOT/SHASUMS256.txt"
    CACHED_NODE_ARCHIVE=""
    CACHED_NODE_SHASUMS=""

    if [[ -n "$NODE_ARCHIVE" ]]; then
        cp -p "$NODE_ARCHIVE" "$DOWNLOADED_NODE_ARCHIVE"
        cp -p "$NODE_SHASUMS" "$DOWNLOADED_NODE_SHASUMS"
        NODE_SOURCE="local:$NODE_ARCHIVE_NAME"
    elif [[ -n "$CACHE_DIR" ]]; then
        NODE_CACHE_DIR="$CACHE_DIR/node/v$NODE_VERSION"
        CACHED_NODE_ARCHIVE="$NODE_CACHE_DIR/$NODE_ARCHIVE_NAME"
        CACHED_NODE_SHASUMS="$NODE_CACHE_DIR/SHASUMS256.txt"
        mkdir -p "$NODE_CACHE_DIR"
        if [[ -f "$CACHED_NODE_ARCHIVE" && -f "$CACHED_NODE_SHASUMS" ]]; then
            printf '[OpenSLT] Reusing cached Node.js %s archive.\n' "$NODE_VERSION"
            cp -p "$CACHED_NODE_ARCHIVE" "$DOWNLOADED_NODE_ARCHIVE"
            cp -p "$CACHED_NODE_SHASUMS" "$DOWNLOADED_NODE_SHASUMS"
            NODE_SOURCE="cache:$CACHED_NODE_ARCHIVE"
        else
            command -v curl >/dev/null 2>&1 || {
                printf 'curl is required to download the Node.js runtime.\n' >&2
                exit 1
            }
            printf '[OpenSLT] Downloading Node.js checksums...\n'
            curl --fail --location --show-error --progress-bar --retry 3 --retry-delay 2 \
                --retry-max-time 900 --connect-timeout 30 --max-time 600 \
                --speed-limit 1024 --speed-time 60 \
                --output "$DOWNLOADED_NODE_SHASUMS" "$NODE_RELEASE_URL/SHASUMS256.txt"
            printf '[OpenSLT] Downloading %s (about 48 MB)...\n' "$NODE_ARCHIVE_NAME"
            curl --fail --location --show-error --progress-bar --retry 3 --retry-delay 2 \
                --retry-max-time 900 --connect-timeout 30 --max-time 600 \
                --speed-limit 1024 --speed-time 60 \
                --output "$DOWNLOADED_NODE_ARCHIVE" "$NODE_RELEASE_URL/$NODE_ARCHIVE_NAME"
            NODE_SOURCE="$NODE_RELEASE_URL/$NODE_ARCHIVE_NAME"
        fi
    else
        command -v curl >/dev/null 2>&1 || {
            printf 'curl is required to download the Node.js runtime.\n' >&2
            exit 1
        }
        printf '[OpenSLT] Downloading Node.js checksums...\n'
        curl --fail --location --show-error --progress-bar --retry 3 --retry-delay 2 \
            --retry-max-time 900 --connect-timeout 30 --max-time 600 \
            --speed-limit 1024 --speed-time 60 \
            --output "$DOWNLOADED_NODE_SHASUMS" "$NODE_RELEASE_URL/SHASUMS256.txt"
        printf '[OpenSLT] Downloading %s (about 48 MB)...\n' "$NODE_ARCHIVE_NAME"
        curl --fail --location --show-error --progress-bar --retry 3 --retry-delay 2 \
            --retry-max-time 900 --connect-timeout 30 --max-time 600 \
            --speed-limit 1024 --speed-time 60 \
            --output "$DOWNLOADED_NODE_ARCHIVE" "$NODE_RELEASE_URL/$NODE_ARCHIVE_NAME"
        NODE_SOURCE="$NODE_RELEASE_URL/$NODE_ARCHIVE_NAME"
    fi

    NODE_EXPECTED_SHA="$(awk -v filename="$NODE_ARCHIVE_NAME" \
        '$2 == filename { print $1 }' "$DOWNLOADED_NODE_SHASUMS")"
    [[ "$NODE_EXPECTED_SHA" =~ ^[0-9a-fA-F]{64}$ ]] || {
        printf 'No unique SHA-256 entry for %s was found in SHASUMS256.txt.\n' \
            "$NODE_ARCHIVE_NAME" >&2
        exit 1
    }
    NODE_ACTUAL_SHA="$(sha256sum "$DOWNLOADED_NODE_ARCHIVE" | awk '{print $1}')"
    [[ "${NODE_ACTUAL_SHA,,}" == "${NODE_EXPECTED_SHA,,}" ]] || {
        printf 'Node.js archive SHA-256 mismatch for %s.\n' "$NODE_ARCHIVE_NAME" >&2
        printf 'Expected: %s\nActual:   %s\n' "$NODE_EXPECTED_SHA" "$NODE_ACTUAL_SHA" >&2
        exit 1
    }
    if [[ -n "$CACHED_NODE_ARCHIVE" && "$NODE_SOURCE" != cache:* ]]; then
        cp -p "$DOWNLOADED_NODE_ARCHIVE" "$CACHED_NODE_ARCHIVE"
        cp -p "$DOWNLOADED_NODE_SHASUMS" "$CACHED_NODE_SHASUMS"
    fi

    printf '[OpenSLT] Extracting and validating Node.js %s...\n' "$NODE_VERSION"
    mkdir -p "$STAGING/node-runtime"
    tar -xzf "$DOWNLOADED_NODE_ARCHIVE" \
        --strip-components=1 -C "$STAGING/node-runtime"
    NODE_BIN="$STAGING/node-runtime/bin/node"
    NPM_BIN="$STAGING/node-runtime/bin/npm"
    [[ -x "$NODE_BIN" && -x "$NPM_BIN" ]] || {
        printf 'The Node.js archive does not contain executable node and npm binaries.\n' >&2
        exit 1
    }
    export PATH="$STAGING/node-runtime/bin:$PATH"
    ACTUAL_NODE_VERSION="$($NODE_BIN --version)"
    [[ "$ACTUAL_NODE_VERSION" == "v$NODE_VERSION" ]] || {
        printf 'Extracted Node.js version mismatch: expected v%s, found %s.\n' \
            "$NODE_VERSION" "$ACTUAL_NODE_VERSION" >&2
        exit 1
    }
    NPM_VERSION="$($NPM_BIN --version)"
    NPM_MAJOR="${NPM_VERSION%%.*}"
    [[ "$NPM_MAJOR" =~ ^[0-9]+$ && "$NPM_MAJOR" -ge 10 ]] || {
        printf 'OpenSLT frontend requires npm >=10; bundled Node contains npm %s.\n' \
            "$NPM_VERSION" >&2
        exit 1
    }

    FRONTEND_DIR="$STAGING/app/frontend"
    NPM_CACHE_DIR="$STAGING/npm-cache"
    PACKAGE_LOCK_SHA="$(sha256sum "$FRONTEND_DIR/package-lock.json" | awk '{print $1}')"
    PERSISTENT_NPM_CACHE_DIR=""
    mkdir -p "$NPM_CACHE_DIR"
    if [[ -n "$CACHE_DIR" ]]; then
        PERSISTENT_NPM_CACHE_DIR="$CACHE_DIR/npm/$PACKAGE_LOCK_SHA"
        if [[ -d "$PERSISTENT_NPM_CACHE_DIR/_cacache" ]]; then
            printf '[OpenSLT] Reusing cached npm dependencies for package-lock.json.\n'
            cp -a "$PERSISTENT_NPM_CACHE_DIR/." "$NPM_CACHE_DIR/"
        fi
    fi
    NPM_INSTALLED_FROM_CACHE=false
    if [[ -d "$NPM_CACHE_DIR/_cacache" ]]; then
        printf '[OpenSLT] Validating cached npm dependencies without network...\n'
        if "$NPM_BIN" --prefix "$FRONTEND_DIR" ci --offline \
            --cache "$NPM_CACHE_DIR" --include=dev --no-audit --no-fund; then
            NPM_INSTALLED_FROM_CACHE=true
        else
            printf '[OpenSLT] Cached npm dependencies are incomplete; rebuilding the cache.\n'
            rm -rf -- "$FRONTEND_DIR/node_modules" "$NPM_CACHE_DIR"
            mkdir -p "$NPM_CACHE_DIR"
        fi
    fi
    if [[ "$NPM_INSTALLED_FROM_CACHE" == false ]]; then
        printf '[OpenSLT] Populating the npm cache from package-lock.json...\n'
        "$NPM_BIN" --prefix "$FRONTEND_DIR" ci \
            --cache "$NPM_CACHE_DIR" --include=dev --no-audit --no-fund
        rm -rf -- "$FRONTEND_DIR/node_modules"
        printf '[OpenSLT] Validating a network-free npm installation...\n'
        "$NPM_BIN" --prefix "$FRONTEND_DIR" ci --offline \
            --cache "$NPM_CACHE_DIR" --include=dev --no-audit --no-fund
        if [[ -n "$PERSISTENT_NPM_CACHE_DIR" ]]; then
            rm -rf -- "$PERSISTENT_NPM_CACHE_DIR"
            mkdir -p "$(dirname -- "$PERSISTENT_NPM_CACHE_DIR")"
            cp -a "$NPM_CACHE_DIR" "$PERSISTENT_NPM_CACHE_DIR"
        fi
    fi
    if [[ "$SKIP_TESTS" == false ]]; then
        "$NPM_BIN" --prefix "$FRONTEND_DIR" run test
    fi
    "$NPM_BIN" --prefix "$FRONTEND_DIR" run build
    rm -rf -- "$FRONTEND_DIR/node_modules" "$NPM_CACHE_DIR/_logs"

    cat >"$STAGING/node-runtime/METADATA" <<EOF
node_version=$NODE_VERSION
npm_version=$NPM_VERSION
platform=linux-x64-glibc-217
source=$NODE_SOURCE
archive_filename=$NODE_ARCHIVE_NAME
archive_sha256=$NODE_ACTUAL_SHA
package_lock_sha256=$PACKAGE_LOCK_SHA
warning=Experimental community build; not an official Node.js release binary.
EOF
fi

BUILD_VENV="$BUILD_ROOT/build-venv"
"$PYTHON" -m venv "$BUILD_VENV"
"$BUILD_VENV/bin/python" -m pip install --upgrade 'pip==25.0.1' 'setuptools==75.3.0' 'wheel==0.45.1'
printf '[OpenSLT] Building the Python wheelhouse...\n'
"$BUILD_VENV/bin/python" -m pip wheel --wheel-dir "$STAGING/wheelhouse" "$PROJECT_ROOT[test]"
APP_WHEEL="$(find "$STAGING/wheelhouse" -maxdepth 1 -type f -name 'openslt-*.whl' -print -quit)"
[[ -n "$APP_WHEEL" ]] || {
    printf 'The OpenSLT application wheel was not created.\n' >&2
    exit 1
}
[[ "$(basename "$APP_WHEEL")" == "openslt-$VERSION-"*.whl ]] || {
    printf 'OpenSLT wheel version does not match project VERSION %s: %s\n' \
        "$VERSION" "$(basename "$APP_WHEEL")" >&2
    exit 1
}

if [[ "$SKIP_TESTS" == false ]]; then
    VALIDATE_VENV="$BUILD_ROOT/validate-venv"
    "$PYTHON" -m venv "$VALIDATE_VENV"
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

if [[ "$BUNDLE_PYTHON" == true ]]; then
    printf '[OpenSLT] Adding the installed rh-python38 runtime...\n'
    mkdir -p "$STAGING/python-runtime"
    tar -C /opt/rh \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        -cf - rh-python38 \
        | tar -C "$STAGING/python-runtime" -xf -
    "$PYTHON" -c 'import platform; print(platform.python_version())' \
        >"$STAGING/python-runtime/VERSION"
fi

cp -p "$SCRIPT_DIR/install-offline.sh" "$STAGING/install.sh"
cp -p "$SCRIPT_DIR/configure-intranet-host.sh" "$STAGING/configure.sh"
cp -p "$SCRIPT_DIR/start-production.sh" "$STAGING/start.sh"
cp -p "$SCRIPT_DIR/build-frontend-intranet.sh" "$STAGING/build-frontend.sh"
cp -p "$SCRIPT_DIR/deployment-config.sh" "$STAGING/deployment-config.sh"
cp -p "$SCRIPT_DIR/openslt.env.example" "$STAGING/openslt.env.example"
cp -p "$SCRIPT_DIR/README-OFFLINE.md" "$STAGING/README-OFFLINE.md"
cp -p "$PROJECT_ROOT/RELEASES.json" "$STAGING/RELEASES.json"
chmod 0755 \
    "$STAGING/install.sh" "$STAGING/configure.sh" "$STAGING/start.sh" \
    "$STAGING/build-frontend.sh"

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
