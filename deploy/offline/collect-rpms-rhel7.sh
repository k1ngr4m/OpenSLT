#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PACKAGE_FILE="$SCRIPT_DIR/rpm-packages-rhel7.txt"
OUTPUT_DIR="$SCRIPT_DIR/rpms"
NGINX_REPO_URL="${OPENSLT_NGINX_REPO_URL:-https://nginx.org/packages/rhel/7/\$basearch/}"
NGINX_KEY_URL="${OPENSLT_NGINX_KEY_URL:-https://nginx.org/keys/nginx_signing.key}"
TEMP_REPO_FILE=""

usage() {
    cat <<'EOF'
Usage: collect-rpms-rhel7.sh [options]

Run this on the internet-connected RHEL 7.9 x86_64 build host after enabling
the RHEL and Software Collections repositories. If nginx is unavailable, the
script temporarily enables the official nginx.org RHEL 7 repository.

Options:
  --output DIR            RPM repository output directory
  --package-file FILE     Package list to collect
  --nginx-repo-url URL    Alternate RHEL 7 Nginx repository base URL
  -h, --help              Show this help
EOF
}

while (($#)); do
    case "$1" in
        --output)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --package-file)
            PACKAGE_FILE="$2"
            shift 2
            ;;
        --nginx-repo-url)
            NGINX_REPO_URL="$2"
            shift 2
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
    printf 'This collector must run on x86_64.\n' >&2
    exit 1
}
grep -Eq '^VERSION_ID="?7\.9"?$' /etc/os-release || {
    printf 'This collector must run on RHEL 7.9.\n' >&2
    exit 1
}
command -v repotrack >/dev/null 2>&1 || {
    printf 'repotrack is missing; install yum-utils on the online host.\n' >&2
    exit 1
}
command -v createrepo >/dev/null 2>&1 || {
    printf 'createrepo is missing; install it on the online host.\n' >&2
    exit 1
}
[[ -f "$PACKAGE_FILE" ]] || {
    printf 'Package list not found: %s\n' "$PACKAGE_FILE" >&2
    exit 1
}

cleanup() {
    if [[ -n "$TEMP_REPO_FILE" ]]; then
        rm -f -- "$TEMP_REPO_FILE"
    fi
}
trap cleanup EXIT

package_is_requested() {
    local requested="$1"
    awk -v requested="$requested" '
        /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
        $1 == requested { found = 1 }
        END { exit(found ? 0 : 1) }
    ' "$PACKAGE_FILE"
}

package_is_available() {
    local requested="$1"
    repoquery --quiet --qf '%{name}' "$requested" 2>/dev/null \
        | grep -Fx -- "$requested" >/dev/null
}

if package_is_requested nginx && ! package_is_available nginx; then
    [[ "$(id -u)" == "0" ]] || {
        printf 'nginx is unavailable in the enabled repositories. Run as root so the temporary Nginx repository can be configured.\n' >&2
        exit 1
    }
    command -v curl >/dev/null 2>&1 || {
        printf 'curl is required to retrieve the Nginx repository signing key.\n' >&2
        exit 1
    }

    TEMP_REPO_FILE="/etc/yum.repos.d/openslt-nginx-packaging-$$.repo"
    printf '[OpenSLT] nginx is absent from the enabled repositories; temporarily enabling %s\n' "$NGINX_REPO_URL"
    {
        printf '[openslt-nginx-packaging-%s]\n' "$$"
        printf 'name=OpenSLT temporary Nginx packaging repository\n'
        printf 'baseurl=%s\n' "$NGINX_REPO_URL"
        printf 'enabled=1\n'
        printf 'gpgcheck=1\n'
        printf 'gpgkey=%s\n' "$NGINX_KEY_URL"
    } >"$TEMP_REPO_FILE"

    if ! package_is_available nginx; then
        printf 'nginx is still unavailable from %s\n' "$NGINX_REPO_URL" >&2
        printf 'Check access with: curl -I %s\n' "$NGINX_REPO_URL" >&2
        printf 'For an internal mirror, rerun with --nginx-repo-url URL.\n' >&2
        exit 1
    fi
fi

missing_packages=()
while IFS= read -r package; do
    [[ -z "$package" || "$package" == \#* ]] && continue
    if ! package_is_available "$package"; then
        missing_packages+=("$package")
    fi
done <"$PACKAGE_FILE"
if ((${#missing_packages[@]})); then
    printf 'The following packages are unavailable from the enabled repositories:\n' >&2
    printf '  %s\n' "${missing_packages[@]}" >&2
    printf 'Enable the RHEL 7 base, Software Collections, and MariaDB repositories, then retry.\n' >&2
    exit 1
fi

mkdir -p "$OUTPUT_DIR/packages" "$OUTPUT_DIR/keys"

if package_is_requested nginx && [[ -n "$TEMP_REPO_FILE" ]]; then
    curl --fail --location --silent --show-error \
        "$NGINX_KEY_URL" \
        --output "$OUTPUT_DIR/keys/nginx_signing.key"
    grep -q 'BEGIN PGP PUBLIC KEY BLOCK' "$OUTPUT_DIR/keys/nginx_signing.key" || {
        printf 'The downloaded Nginx signing key is not a PGP public key: %s\n' "$NGINX_KEY_URL" >&2
        exit 1
    }
fi

while IFS= read -r package; do
    [[ -z "$package" || "$package" == \#* ]] && continue
    printf '[OpenSLT] Collecting %s and its dependencies...\n' "$package"
    repotrack -a x86_64 -p "$OUTPUT_DIR/packages" "$package"
done <"$PACKAGE_FILE"

# repotrack on RHEL 7 can include multilib dependencies despite -a x86_64.
# The target is explicitly x86_64, so do not install an unnecessary 32-bit stack.
find "$OUTPUT_DIR/packages" -maxdepth 1 -type f -name '*.i686.rpm' -delete
find /etc/pki/rpm-gpg -maxdepth 1 -type f -exec cp -p {} "$OUTPUT_DIR/keys/" \;
createrepo --update "$OUTPUT_DIR/packages"
cp -p "$PACKAGE_FILE" "$OUTPUT_DIR/requested-packages.txt"

printf '[OpenSLT] RPM repository created at %s\n' "$OUTPUT_DIR"
