#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
readonly BINARY_NAME="ees_ef_vi_trader_binary_api_test"
readonly PACKAGE_DIR="${REPO_ROOT}/build/${BINARY_NAME}/release/${BINARY_NAME}"
readonly BUILDER_IMAGE="openslt/ees-ef-vi-trader-simulator-builder:py38-manylinux2014"

test -x "${PACKAGE_DIR}/${BINARY_NAME}"
file "${PACKAGE_DIR}/${BINARY_NAME}" | grep -Eq 'ELF 64-bit.*x86-64'

output="$({ printf 'new_order\nexit\n'; } | docker run \
  --rm \
  --interactive \
  --network none \
  --platform linux/amd64 \
  --volume "${PACKAGE_DIR}:/opt/ees-simulator:ro" \
  --workdir /opt/ees-simulator \
  --entrypoint "./${BINARY_NAME}" \
  "${BUILDER_IMAGE}" \
  ees_ef_vi_trader_api_test_conf.xml)"

grep -Fq '[SIMULATION ONLY]' <<<"${output}"
grep -Fq 'SIM_EVENT' <<<"${output}"
grep -Fq '"action": "new_order"' <<<"${output}"
echo "Linux x86_64 no-network smoke test passed."
