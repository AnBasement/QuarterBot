"""Tests for core/utils/quiet_hours.py, shared reminder-time clamping."""

from datetime import datetime
import pytz

from core.utils.quiet_hours import (
    clamp_to_quiet_hours,
    QUIET_HOURS_START_HOUR,
    QUIET_HOURS_END_HOUR,
)

TZ = pytz.timezone("Europe/Oslo")


def test_daytime_time_unchanged():
    natural = TZ.localize(datetime(2024, 9, 8, 18, 0))
    assert clamp_to_quiet_hours(natural) == natural


def test_early_morning_clamped_to_previous_evening():
    natural = TZ.localize(datetime(2024, 9, 6, 0, 15))
    expected = TZ.localize(datetime(2024, 9, 5, 22, 0))
    assert clamp_to_quiet_hours(natural) == expected


def test_late_evening_clamped_to_window_close_same_day():
    natural = TZ.localize(datetime(2024, 9, 5, 23, 15))
    expected = TZ.localize(datetime(2024, 9, 5, 22, 0))
    assert clamp_to_quiet_hours(natural) == expected


def test_window_boundaries():
    # 08:00 is outside quiet hours: unchanged.
    end = TZ.localize(datetime(2024, 9, 5, QUIET_HOURS_END_HOUR, 0))
    assert clamp_to_quiet_hours(end) == end
    # One minute before is still quiet: moved to 22:00 the evening before.
    assert clamp_to_quiet_hours(
        TZ.localize(datetime(2024, 9, 5, QUIET_HOURS_END_HOUR - 1, 59))
    ) == TZ.localize(datetime(2024, 9, 4, QUIET_HOURS_START_HOUR, 0))
    # 21:59 is outside quiet hours: unchanged.
    before_start = TZ.localize(datetime(2024, 9, 5, QUIET_HOURS_START_HOUR - 1, 59))
    assert clamp_to_quiet_hours(before_start) == before_start
