#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PACKAGE_FILE="$SCRIPT_DIR/rpm-packages-rhel7.txt"
OUTPUT_DIR="$SCRIPT_DIR/rpms"

usage() {
    cat <<'EOF'
Usage: collect-rpms-rhel7.sh [--output DIR] [--package-file FILE]

Run this on the internet-connected RHEL 7.9 x86_64 build host after enabling
the RHEL/SCL, Nginx, and MySQL Community 8 repositories. The result is a
dependency-complete RPM directory which build-offline-bundle.sh can include.
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

mkdir -p "$OUTPUT_DIR/packages" "$OUTPUT_DIR/keys"

while IFS= read -r package; do
    [[ -z "$package" || "$package" == \#* ]] && continue
    printf '[OpenSLT] Collecting %s and its dependencies...\n' "$package"
    repotrack -a x86_64 -p "$OUTPUT_DIR/packages" "$package"
done <"$PACKAGE_FILE"

find /etc/pki/rpm-gpg -maxdepth 1 -type f -exec cp -p {} "$OUTPUT_DIR/keys/" \;
createrepo --update "$OUTPUT_DIR/packages"
cp -p "$PACKAGE_FILE" "$OUTPUT_DIR/requested-packages.txt"

printf '[OpenSLT] RPM repository created at %s\n' "$OUTPUT_DIR"
