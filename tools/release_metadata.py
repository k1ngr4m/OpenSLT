#!/usr/bin/env python3
"""Validate and expose OpenSLT release metadata."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
CHANGE_TYPES = {"added", "changed", "fixed", "removed", "security"}
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataError(ValueError):
    pass


def _version_tuple(value: str) -> Tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(value)
    if not match:
        raise ReleaseMetadataError(
            f"Invalid version {value!r}; expected MAJOR.MINOR.PATCH without a v prefix"
        )
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _require_nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseMetadataError(f"{field} must be a non-empty string")
    return value.strip()


def _validate_changes(changes: Any, field: str, allow_empty: bool) -> List[Dict[str, str]]:
    if not isinstance(changes, list) or (not allow_empty and not changes):
        requirement = "an array" if allow_empty else "a non-empty array"
        raise ReleaseMetadataError(f"{field} must be {requirement}")
    validated: List[Dict[str, str]] = []
    for index, change in enumerate(changes):
        item_field = f"{field}[{index}]"
        if not isinstance(change, dict):
            raise ReleaseMetadataError(f"{item_field} must be an object")
        change_type = _require_nonempty_string(change.get("type"), f"{item_field}.type")
        if change_type not in CHANGE_TYPES:
            raise ReleaseMetadataError(
                f"{item_field}.type must be one of {', '.join(sorted(CHANGE_TYPES))}"
            )
        text = _require_nonempty_string(change.get("text"), f"{item_field}.text")
        validated.append({"type": change_type, "text": text})
    return validated


def load_release_metadata(root: Path = REPOSITORY_ROOT) -> Dict[str, Any]:
    version_path = root / "VERSION"
    releases_path = root / "RELEASES.json"
    try:
        version = version_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ReleaseMetadataError(f"Unable to read {version_path}: {exc}") from exc
    _version_tuple(version)

    try:
        raw = json.loads(releases_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseMetadataError(f"Unable to read {releases_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ReleaseMetadataError("RELEASES.json must contain an object")

    unreleased = _validate_changes(raw.get("unreleased"), "unreleased", allow_empty=True)
    releases = raw.get("releases")
    if not isinstance(releases, list) or not releases:
        raise ReleaseMetadataError("releases must be a non-empty array")

    validated_releases: List[Dict[str, Any]] = []
    seen_versions = set()
    version_order: List[Tuple[int, int, int]] = []
    for index, release in enumerate(releases):
        field = f"releases[{index}]"
        if not isinstance(release, dict):
            raise ReleaseMetadataError(f"{field} must be an object")
        release_version = _require_nonempty_string(release.get("version"), f"{field}.version")
        parsed_version = _version_tuple(release_version)
        if release_version in seen_versions:
            raise ReleaseMetadataError(f"Duplicate release version: {release_version}")
        seen_versions.add(release_version)
        version_order.append(parsed_version)

        release_date = release.get("date")
        if release_date is not None:
            release_date = _require_nonempty_string(release_date, f"{field}.date")
            try:
                dt.date.fromisoformat(release_date)
            except ValueError as exc:
                raise ReleaseMetadataError(f"{field}.date must use YYYY-MM-DD") from exc

        validated_releases.append(
            {
                "version": release_version,
                "date": release_date,
                "title": _require_nonempty_string(release.get("title"), f"{field}.title"),
                "changes": _validate_changes(release.get("changes"), f"{field}.changes", False),
            }
        )

    if version_order != sorted(version_order, reverse=True):
        raise ReleaseMetadataError("releases must be ordered from newest to oldest")
    if validated_releases[0]["version"] != version:
        raise ReleaseMetadataError(
            f"VERSION is {version}, but the newest release is {validated_releases[0]['version']}"
        )
    return {"version": version, "unreleased": unreleased, "releases": validated_releases}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="store_true", help="print the validated current version")
    args = parser.parse_args()
    try:
        metadata = load_release_metadata()
    except ReleaseMetadataError as exc:
        print(f"Release metadata validation failed: {exc}", file=sys.stderr)
        return 1
    if args.version:
        print(metadata["version"])
    else:
        print(f"Release metadata is valid for OpenSLT {metadata['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
