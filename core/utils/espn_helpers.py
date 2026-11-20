"""ESPN fantasy API access."""

import os
from espn_api.football import League
import requests
from espn_api.requests.espn_requests import (
    ESPNAccessDenied,
    ESPNInvalidLeague,
    ESPNUnknownError,
)

# What get_league() can raise when ESPN or the settings are the problem.
ESPN_ERRORS = (
    requests.exceptions.RequestException,
    ESPNAccessDenied,
    ESPNInvalidLeague,
    ESPNUnknownError,
    ValueError,
)


def get_league(year: int | None = None) -> League:
    """Fetches the league set by ESPN_LEAGUE_ID for `year` or for `ESPN_YEAR`
    if no year is given.

    ESPN_S2 and ESPN_SWID are needed for private leagues. Blocking: call it
    through asyncio.to_thread() from async code. Raises ValueError if
    ESPN_LEAGUE_ID or ESPN_YEAR is missing or not a number.
    """
    league_id = os.getenv("ESPN_LEAGUE_ID")
    if year is None:
        env_year = os.getenv("ESPN_YEAR")
        if env_year is None:
            raise ValueError("ESPN_YEAR must be set")
        year = int(env_year)
    if league_id is None:
        raise ValueError("ESPN_LEAGUE_ID must be set")
    return League(
        league_id=int(league_id),
        year=year,
        espn_s2=os.getenv("ESPN_S2"),
        swid=os.getenv("ESPN_SWID"),
    )
