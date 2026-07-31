#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
PYTHON="/opt/rh/rh-python38/root/usr/bin/python3.8"
OUTPUT_DIR="$PROJECT_ROOT/release"
VERSION=""
SKIP_TESTS=false
CACHE_DIR=""
REFRESH_CACHE=false
NGINX_REPO_URL=""
BUNDLE_PYTHON=false
BUNDLE_NODE=false
NODE_VERSION="20.20.2"
NODE_BASE_URL="https://unofficial-builds.nodejs.org/download/release"
NODE_ARCHIVE=""
NODE_SHASUMS=""

usage() {
    cat <<'EOF'
Usage: make-offline-package.sh [options]

Run this once on the internet-connected RHEL 7.9 x86_64 packaging host.

Options:
  --python PATH       Python >=3.8 executable
  --output DIR        Output directory (default: release/)
  --version VERSION   Assert the canonical VERSION value (optional)
  --cache-dir DIR     Reuse RPM, Node.js, npm, and pip caches between runs
  --refresh-cache     Clear --cache-dir before collecting dependencies
  --nginx-repo-url URL
                      Alternate RHEL 7 Nginx repository base URL
  --bundle-python     Include the installed RHEL 7 rh-python38 runtime
  --bundle-node       Include Node.js, npm, and a verified offline npm cache
  --node-version VER  linux-x64-glibc-217 Node version (default: 20.20.2)
  --node-base-url URL Unofficial Node.js release base URL
  --node-archive FILE Use a predownloaded Node .tar.gz archive
  --node-shasums FILE Use a predownloaded SHASUMS256.txt (required with archive)
  --skip-tests        Skip Python wheel validation and test suites
  -h, --help          Show this help

Without --bundle-node, frontend/dist must already have been built on a Node.js
20+ host. With --bundle-node, the bundled runtime builds and validates it.
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
        --cache-dir)
            CACHE_DIR="$2"
            shift 2
            ;;
        --refresh-cache)
            REFRESH_CACHE=true
            shift
            ;;
        --nginx-repo-url)
            NGINX_REPO_URL="$2"
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
    printf 'This script must run on x86_64.\n' >&2
    exit 1
}
grep -Eq '^VERSION_ID="?7\.9"?$' /etc/os-release || {
    printf 'This script must run on RHEL 7.9.\n' >&2
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
if [[ "$REFRESH_CACHE" == true && -z "$CACHE_DIR" ]]; then
    printf -- '--refresh-cache requires --cache-dir.\n' >&2
    exit 2
fi
if [[ -n "$CACHE_DIR" ]]; then
    if [[ "$REFRESH_CACHE" == true ]]; then
        rm -rf -- "$CACHE_DIR"
    fi
    mkdir -p "$CACHE_DIR"
    CACHE_DIR="$(cd -- "$CACHE_DIR" && pwd)"
    case "$CACHE_DIR/" in
        "$PROJECT_ROOT"/*)
            printf -- '--cache-dir must be outside the project root: %s\n' "$CACHE_DIR" >&2
            exit 2
            ;;
    esac
fi
if [[ "$BUNDLE_NODE" == false ]]; then
    [[ -f "$PROJECT_ROOT/frontend/dist/index.html" ]] || {
        printf 'frontend/dist is missing. Build it on a Node.js 20+ host first.\n' >&2
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
        printf 'frontend/dist is stale. Rebuild it on the Node.js host first.\n' >&2
        exit 1
    fi
    if [[ -n "$NODE_ARCHIVE" || -n "$NODE_SHASUMS" || "$NODE_VERSION" != "20.20.2" \
        || "$NODE_BASE_URL" != "https://unofficial-builds.nodejs.org/download/release" ]]; then
        printf 'Node download options require --bundle-node.\n' >&2
        exit 2
    fi
else
    NORMALIZED_NODE_VERSION="${NODE_VERSION#v}"
    [[ "$NORMALIZED_NODE_VERSION" =~ ^20\.[0-9]+\.[0-9]+$ ]] || {
        printf 'Node version must be a complete Node.js 20 release such as 20.20.2.\n' >&2
        exit 2
    }
    if [[ -n "$NODE_ARCHIVE" || -n "$NODE_SHASUMS" ]]; then
        [[ -f "$NODE_ARCHIVE" && -f "$NODE_SHASUMS" ]] || {
            printf -- '--node-archive and --node-shasums must name existing files together.\n' >&2
            exit 2
        }
    fi
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
if [[ -n "$CACHE_DIR" ]]; then
    RPM_DIR="$CACHE_DIR/rpms"
fi

printf '[OpenSLT] Collecting the complete RHEL 7 RPM dependency set...\n'
COLLECT_ARGS=(--output "$RPM_DIR")
[[ -n "$NGINX_REPO_URL" ]] && COLLECT_ARGS+=(--nginx-repo-url "$NGINX_REPO_URL")
"$SCRIPT_DIR/collect-rpms-rhel7.sh" "${COLLECT_ARGS[@]}"

BUILD_ARGS=(
    --python "$PYTHON"
    --rpm-dir "$RPM_DIR"
    --output "$OUTPUT_DIR"
    --version "$VERSION"
)
[[ "$SKIP_TESTS" == true ]] && BUILD_ARGS+=(--skip-tests)
[[ -n "$CACHE_DIR" ]] && BUILD_ARGS+=(--cache-dir "$CACHE_DIR")
[[ "$BUNDLE_PYTHON" == true ]] && BUILD_ARGS+=(--bundle-python)
if [[ "$BUNDLE_NODE" == true ]]; then
    BUILD_ARGS+=(--bundle-node --node-version "$NODE_VERSION" --node-base-url "$NODE_BASE_URL")
    [[ -n "$NODE_ARCHIVE" ]] && BUILD_ARGS+=(--node-archive "$NODE_ARCHIVE")
    [[ -n "$NODE_SHASUMS" ]] && BUILD_ARGS+=(--node-shasums "$NODE_SHASUMS")
fi

printf '[OpenSLT] Building and validating the offline application bundle...\n'
"$SCRIPT_DIR/build-offline-bundle.sh" "${BUILD_ARGS[@]}"

printf '[OpenSLT] Done. Transfer the .tar.gz and .sha256 files from %s.\n' "$OUTPUT_DIR"
