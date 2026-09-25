"""ESPN fantasy API access."""

import os
from espn_api.football import League


def get_league() -> League:
    """Fetches the league set by ESPN_LEAGUE_ID and ESPN_YEAR.

    ESPN_S2 and ESPN_SWID are needed for private leagues. Blocking: call it
    through asyncio.to_thread() from async code. Raises ValueError if
    ESPN_LEAGUE_ID or ESPN_YEAR is missing or not a number.
    """
    league_id = os.getenv("ESPN_LEAGUE_ID")
    year = os.getenv("ESPN_YEAR")
    if league_id is None or year is None:
        raise ValueError("ESPN_LEAGUE_ID and ESPN_YEAR must be set")
    return League(
        league_id=int(league_id),
        year=int(year),
        espn_s2=os.getenv("ESPN_S2"),
        swid=os.getenv("ESPN_SWID"),
    )
