from __future__ import annotations

import subprocess
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OFFLINE_DIR = REPOSITORY_ROOT / "deploy" / "offline"


def _script(name: str) -> str:
    return (OFFLINE_DIR / name).read_text(encoding="utf-8")


def test_node_bundle_options_are_documented_by_both_build_entrypoints() -> None:
    for name in ("make-offline-package.sh", "build-offline-bundle.sh"):
        completed = subprocess.run(
            ["bash", str(OFFLINE_DIR / name), "--help"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
        )

        assert completed.returncode == 0, completed.stderr
        for option in (
            "--bundle-node",
            "--node-version",
            "--node-base-url",
            "--node-archive",
            "--node-shasums",
        ):
            assert option in completed.stdout


def test_offline_bundle_uses_and_enforces_the_canonical_project_version() -> None:
    for name in ("make-offline-package.sh", "build-offline-bundle.sh"):
        script = _script(name)
        assert 'tools/release_metadata.py" --version' in script
        assert '"$VERSION" != "$PROJECT_VERSION"' in script
        assert 'VERSION="$PROJECT_VERSION"' in script

    builder = _script("build-offline-bundle.sh")
    assert "date +%Y%m%d" not in builder
    assert '"openslt-$VERSION-"*.whl' in builder
    assert 'cp -p "$PROJECT_ROOT/RELEASES.json" "$STAGING/RELEASES.json"' in builder
    assert '"$PROJECT_ROOT/VERSION"' in builder
    assert '"$PROJECT_ROOT/RELEASES.json"' in builder


def test_node_archive_is_verified_before_extraction() -> None:
    builder = _script("build-offline-bundle.sh")

    expected_filename = 'NODE_ARCHIVE_NAME="node-v${NODE_VERSION}-linux-x64-glibc-217.tar.gz"'
    exact_manifest_match = "'$2 == filename { print $1 }'"
    checksum_comparison = '[[ "${NODE_ACTUAL_SHA,,}" == "${NODE_EXPECTED_SHA,,}" ]]'
    extraction = 'tar -xzf "$DOWNLOADED_NODE_ARCHIVE"'

    for marker in (expected_filename, exact_manifest_match, checksum_comparison, extraction):
        assert marker in builder
    assert builder.index(expected_filename) < builder.index(exact_manifest_match)
    assert builder.index(exact_manifest_match) < builder.index(checksum_comparison)
    assert builder.index(checksum_comparison) < builder.index(extraction)


def test_npm_cache_is_reinstalled_offline_and_bound_to_lock_file() -> None:
    builder = _script("build-offline-bundle.sh")

    online_install = '"$NPM_BIN" --prefix "$FRONTEND_DIR" ci \\\n'
    offline_install = '"$NPM_BIN" --prefix "$FRONTEND_DIR" ci --offline'
    remove_modules = 'rm -rf -- "$FRONTEND_DIR/node_modules"'
    lock_metadata = "package_lock_sha256=$PACKAGE_LOCK_SHA"
    persistent_cache = 'PERSISTENT_NPM_CACHE_DIR="$CACHE_DIR/npm/$PACKAGE_LOCK_SHA"'
    cached_install = "Validating cached npm dependencies without network"
    populate_cache = "Populating the npm cache from package-lock.json"

    for marker in (
        online_install,
        offline_install,
        remove_modules,
        lock_metadata,
        persistent_cache,
        cached_install,
        populate_cache,
    ):
        assert marker in builder

    online_install_at = builder.index(online_install, builder.index(populate_cache))
    remove_modules_at = builder.index(remove_modules, online_install_at)
    offline_install_at = builder.index(offline_install, remove_modules_at)
    assert online_install_at < remove_modules_at < offline_install_at


def test_bundled_node_is_installed_without_replacing_system_node() -> None:
    installer = _script("install-offline.sh")

    assert 'NODE_INSTALL_ROOT="/opt/openslt-node"' in installer
    assert 'NPM_CACHE_ROOT="/var/cache/openslt/npm"' in installer
    assert "/etc/profile.d/openslt-node.sh" in installer
    assert "/usr/bin/node" not in installer
    assert '"$BUNDLE_ROOT/build-frontend.sh" /opt/openslt/build-frontend.sh' in installer


def test_intranet_frontend_builder_is_strictly_offline() -> None:
    script = _script("build-frontend-intranet.sh")

    assert 'NPM_CACHE="/var/cache/openslt/npm"' in script
    assert 'ci --offline' in script
    assert "package-lock.json does not match the bundled npm cache" in script
    assert "run test" in script
    assert "run build" in script
    assert "systemctl reload nginx" in script
    assert "npm install" not in script


def test_svn_client_is_included_in_the_rhel7_offline_bundle() -> None:
    packages = (OFFLINE_DIR / "rpm-packages-rhel7.txt").read_text(encoding="utf-8").splitlines()
    assert "subversion" in packages
    assert "svn --version --quiet" in _script("README-OFFLINE.md")
