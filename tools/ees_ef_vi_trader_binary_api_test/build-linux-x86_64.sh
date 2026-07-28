#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
readonly TOOL_RELATIVE_PATH="${SCRIPT_DIR#"${REPO_ROOT}/"}"
readonly BUILDER_IMAGE="openslt/ees-ef-vi-trader-simulator-builder:py38-manylinux2014"

docker info >/dev/null
docker build \
  --platform linux/amd64 \
  --file "${SCRIPT_DIR}/Dockerfile.build" \
  --tag "${BUILDER_IMAGE}" \
  "${SCRIPT_DIR}"
docker run \
  --rm \
  --platform linux/amd64 \
  --user "$(id -u):$(id -g)" \
  --env HOME=/tmp/ees-simulator-builder \
  --env PYINSTALLER_CONFIG_DIR=/tmp/ees-simulator-pyinstaller \
  --volume "${REPO_ROOT}:/src" \
  --workdir /src \
  "${BUILDER_IMAGE}" \
  "${TOOL_RELATIVE_PATH}/build-in-container.sh"

"${SCRIPT_DIR}/smoke-test-linux.sh"
