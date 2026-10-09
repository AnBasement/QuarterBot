"""Settings read from the environment. See .env.example for descriptions."""

import os

# Name of the weekly pick'em game in the bot's messages.
GAME_NAME = os.getenv("GAME_NAME", "Weekly Pick'em")

# Name of the fantasy league, e.g. in the season-end standings banner.
LEAGUE_NAME = os.getenv("LEAGUE_NAME", "The League")

# Spreadsheets are found by name and must be shared with the service account.
PICKEM_SHEET_NAME = os.getenv("PICKEM_SHEET_NAME", "Pick'em")

# Written into the pick'em sheet and searched for later, so they must match
# what's already in the sheet (or season totals restart from zero).
WEEKLY_POINTS_SHEET_LABEL = os.getenv("WEEKLY_POINTS_SHEET_LABEL", "Weekly points")
SEASON_TOTAL_SHEET_LABEL = os.getenv("SEASON_TOTAL_SHEET_LABEL", "Season total")
DRAW_SHEET_LABEL = os.getenv("DRAW_SHEET_LABEL", "Tie")

# IANA timezone name, e.g. America/New_York.
LEAGUE_TIMEZONE = os.getenv("LEAGUE_TIMEZONE", "Europe/Oslo")

# Last week of the ESPN fantasy season, playoffs included.
FANTASY_FINAL_WEEK = int(os.getenv("FANTASY_FINAL_WEEK", "17"))
