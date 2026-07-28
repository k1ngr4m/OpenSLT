from __future__ import annotations

from datetime import datetime, timezone

from app.core.logging import add_beijing_timestamp
from app.core.time import as_beijing, as_storage_utc, beijing_now, format_beijing


def test_time_helpers_preserve_the_instant_across_storage_and_display() -> None:
    instant = datetime(2026, 7, 28, 8, 30, 45, 123000, tzinfo=timezone.utc)

    stored = as_storage_utc(instant)
    displayed = as_beijing(stored)

    assert stored.isoformat() == "2026-07-28T08:30:45.123000"
    assert displayed.isoformat() == "2026-07-28T16:30:45.123000+08:00"
    assert format_beijing(instant, timespec="milliseconds") == "2026-07-28T16:30:45.123+08:00"


def test_beijing_now_and_structured_log_timestamp_have_explicit_offset() -> None:
    assert beijing_now().utcoffset().total_seconds() == 8 * 60 * 60

    event = add_beijing_timestamp(None, "info", {})

    assert event["timestamp"].endswith("+08:00")
