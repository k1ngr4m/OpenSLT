"""Resolve the OpenSLT application version from the canonical release metadata."""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path


VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def _read_source_version() -> str:
    version_file = Path(__file__).resolve().parents[2] / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def get_app_version() -> str:
    source_version = _read_source_version()
    if not source_version:
        try:
            source_version = distribution_version("openslt")
        except PackageNotFoundError as exc:
            raise RuntimeError("OpenSLT version metadata is unavailable") from exc
    if not VERSION_PATTERN.fullmatch(source_version):
        raise RuntimeError(
            f"Invalid OpenSLT version {source_version!r}; expected MAJOR.MINOR.PATCH"
        )
    return source_version


APP_VERSION = get_app_version()
