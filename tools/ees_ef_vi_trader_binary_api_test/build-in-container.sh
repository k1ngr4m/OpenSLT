#!/usr/bin/env bash
set -euo pipefail

readonly BINARY_NAME="ees_ef_vi_trader_binary_api_test"
readonly TOOL_DIR="/src/tools/${BINARY_NAME}"
readonly WORK_ROOT="/src/build/${BINARY_NAME}"
readonly DIST_ROOT="${WORK_ROOT}/dist"
readonly PACKAGE_ROOT="${WORK_ROOT}/release"
readonly PACKAGE_DIR="${PACKAGE_ROOT}/${BINARY_NAME}"
readonly PYTHON_BIN="/opt/python/cp38-cp38/bin/python"

rm -rf "${WORK_ROOT}"
mkdir -p "${WORK_ROOT}/pyinstaller" "${WORK_ROOT}/spec" "${DIST_ROOT}" "${PACKAGE_ROOT}"

"${PYTHON_BIN}" -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --name "${BINARY_NAME}" \
  --distpath "${DIST_ROOT}" \
  --workpath "${WORK_ROOT}/pyinstaller" \
  --specpath "${WORK_ROOT}/spec" \
  "${TOOL_DIR}/${BINARY_NAME}.py"

cp -a "${DIST_ROOT}/${BINARY_NAME}" "${PACKAGE_DIR}"
cp "${TOOL_DIR}/ees_ef_vi_trader_api_test_conf.xml" "${PACKAGE_DIR}/"
cp "${TOOL_DIR}/README.md" "${PACKAGE_DIR}/README-SIMULATOR.md"
chmod 0755 "${PACKAGE_DIR}/${BINARY_NAME}"

(
  cd "${PACKAGE_DIR}"
  find . -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum > SHA256SUMS
)

file "${PACKAGE_DIR}/${BINARY_NAME}" | grep -Eq 'ELF 64-bit.*x86-64'
tar -C "${PACKAGE_ROOT}" -czf "${WORK_ROOT}/${BINARY_NAME}-linux-x86_64.tar.gz" "${BINARY_NAME}"
sha256sum "${WORK_ROOT}/${BINARY_NAME}-linux-x86_64.tar.gz" \
  > "${WORK_ROOT}/${BINARY_NAME}-linux-x86_64.tar.gz.sha256"

echo "Package: ${WORK_ROOT}/${BINARY_NAME}-linux-x86_64.tar.gz"
echo "Checksum: ${WORK_ROOT}/${BINARY_NAME}-linux-x86_64.tar.gz.sha256"
