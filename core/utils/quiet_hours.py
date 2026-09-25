"""Keeps notifications out of quiet hours, when nobody would see them."""

from datetime import datetime, timedelta

# In the timezone of the time being checked (LEAGUE_TIMEZONE in practice).
QUIET_HOURS_START_HOUR = 22
QUIET_HOURS_END_HOUR = 8


def clamp_to_quiet_hours(natural_time: datetime) -> datetime:
    """Moves a send time inside quiet hours back to when they started.

    That's 22:00 the same evening, or the evening before for times after
    midnight. Times outside quiet hours are returned unchanged.
    """
    if QUIET_HOURS_END_HOUR <= natural_time.hour < QUIET_HOURS_START_HOUR:
        return natural_time

    clamp_day = natural_time
    if natural_time.hour < QUIET_HOURS_END_HOUR:
        # After midnight: quiet hours began the previous evening.
        clamp_day = natural_time - timedelta(days=1)
    return clamp_day.replace(
        hour=QUIET_HOURS_START_HOUR, minute=0, second=0, microsecond=0
    )
