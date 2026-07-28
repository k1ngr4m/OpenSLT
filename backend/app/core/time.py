from __future__ import annotations

import typing
from datetime import datetime, timedelta, timezone, tzinfo


# Beijing has used UTC+08:00 without daylight-saving transitions since 1991.
# A fixed offset keeps the Python 3.8 runtime independent of the host timezone
# and avoids adding a zoneinfo backport solely for the application's timestamps.
BEIJING_TZ: tzinfo = timezone(timedelta(hours=8), "Asia/Shanghai")
UTC_TZ: tzinfo = timezone.utc


def beijing_now() -> datetime:
    """Return the current instant represented in Beijing time."""

    return datetime.now(BEIJING_TZ)


def as_utc(value: datetime) -> datetime:
    """Return an aware UTC value, treating legacy naive database values as UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC_TZ)
    return value.astimezone(UTC_TZ)


def as_beijing(value: datetime) -> datetime:
    """Return an instant represented in Beijing time."""

    return as_utc(value).astimezone(BEIJING_TZ)


def as_storage_utc(value: datetime) -> datetime:
    """Normalize a timestamp to the naive UTC form stored by SQLite/MySQL."""

    return as_utc(value).replace(tzinfo=None)


def from_unix_timestamp(value: typing.Union[int, float]) -> datetime:
    """Convert a Unix timestamp to an aware Beijing datetime."""

    return datetime.fromtimestamp(value, BEIJING_TZ)


def format_beijing(value: datetime, *, timespec: str = "seconds") -> str:
    """Format a timestamp as ISO 8601 with an explicit +08:00 offset."""

    return as_beijing(value).isoformat(timespec=timespec)
