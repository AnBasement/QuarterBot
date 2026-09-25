"""Settings read from the environment. See .env.example for descriptions."""

import os

# Name of the weekly pick'em game in the bot's messages.
GAME_NAME = os.getenv("GAME_NAME", "Weekly Pick'em")

# Name of the fantasy league, e.g. in the season-end standings banner.
LEAGUE_NAME = os.getenv("LEAGUE_NAME", "The League")

# Spreadsheets are found by name and must be shared with the service account.
PICKEM_SHEET_NAME = os.getenv("PICKEM_SHEET_NAME", "Pick'em")
LEAGUE_SHEET_NAME = os.getenv("LEAGUE_SHEET_NAME", "League")

# Written into the pick'em sheet and searched for later, so they must match
# what's already in the sheet (or season totals restart from zero).
WEEKLY_POINTS_SHEET_LABEL = os.getenv("WEEKLY_POINTS_SHEET_LABEL", "Weekly points")
SEASON_TOTAL_SHEET_LABEL = os.getenv("SEASON_TOTAL_SHEET_LABEL", "Season total")
DRAW_SHEET_LABEL = os.getenv("DRAW_SHEET_LABEL", "Tie")

# Tab in the league sheet for PPR snapshots.
PPR_HISTORY_SHEET_NAME = os.getenv("PPR_HISTORY_SHEET_NAME", "PPR History")


def parse_ppr_managers(raw: str) -> dict[str, str]:
    """Parses "Tab=Team name;Tab 2=Team name 2" into {tab: team name}.

    ";" separates entries since team names can contain commas. Without
    "=Team name", the tab name is used.
    """
    managers: dict[str, str] = {}
    for entry in raw.split(";"):
        tab, _, team = entry.partition("=")
        tab, team = tab.strip(), team.strip()
        if tab:
            managers[tab] = team or tab
    return managers


# Manager tabs in the league sheet, and the team name !ppr shows for each.
PPR_MANAGERS = parse_ppr_managers(os.getenv("PPR_MANAGERS", ""))

# IANA timezone name, e.g. America/New_York.
LEAGUE_TIMEZONE = os.getenv("LEAGUE_TIMEZONE", "Europe/Oslo")

# Last week of the ESPN fantasy season, playoffs included.
FANTASY_FINAL_WEEK = int(os.getenv("FANTASY_FINAL_WEEK", "17"))
