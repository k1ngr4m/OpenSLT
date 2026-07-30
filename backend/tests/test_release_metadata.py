from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.main import app
from app.version import APP_VERSION


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "tools"))

from release_metadata import ReleaseMetadataError, load_release_metadata  # noqa: E402


def test_current_release_metadata_is_valid() -> None:
    metadata = load_release_metadata()

    assert metadata["version"] == "0.2.0"
    assert metadata["releases"][0]["version"] == APP_VERSION
    assert app.version == APP_VERSION


def test_release_metadata_cli_prints_current_version() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/release_metadata.py", "--version"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert completed.stdout.strip() == "0.2.0"


def test_release_metadata_rejects_a_mismatched_latest_release(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("0.2.0\n", encoding="utf-8")
    (tmp_path / "RELEASES.json").write_text(
        json.dumps(
            {
                "unreleased": [],
                "releases": [
                    {
                        "version": "0.1.0",
                        "date": None,
                        "title": "Initial",
                        "changes": [{"type": "added", "text": "Initial release"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ReleaseMetadataError, match="newest release"):
        load_release_metadata(tmp_path)
