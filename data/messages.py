"""Text the bot posts to Discord, each overridable by an environment variable.

Admin channel messages and logs aren't here; they stay in English.
"""

import os


def _message(name: str, default: str) -> str:
    """Reads a message from the environment. A written "\\n" becomes a line break."""
    return os.getenv(name, default).replace("\\n", "\n")


# Command errors (core/bot.py)
PERMISSION_DENIED_MESSAGE = _message(
    "PERMISSION_DENIED_MESSAGE",
    "Maybe if you ask nicely enough, you'll be allowed to use the bot.",
)
COMMAND_USAGE_MESSAGE = _message(
    "COMMAND_USAGE_MESSAGE", "Couldn't read that. Usage: `{usage}`"
)

# Pick'em game (cogs/pickem.py)
GAMES_POSTED_MESSAGE = _message(
    "GAMES_POSTED_MESSAGE",
    "@everyone This week's games are posted in <#{channel}>!",
)
WEEKLY_GAMES_POSTED_MESSAGE = _message(
    "WEEKLY_GAMES_POSTED_MESSAGE",
    "@everyone This week's games (week {week}) are posted in <#{channel}>!",
)
THURSDAY_GAME_REMINDER_MESSAGE = _message(
    "THURSDAY_GAME_REMINDER_MESSAGE",
    "@everyone Heads up, the week is starting soon, check <#{channel}>!",
)
SUNDAY_GAME_REMINDER_MESSAGE = _message(
    "SUNDAY_GAME_REMINDER_MESSAGE",
    "@everyone Early window starting soon, don't forget <#{channel}>",
)
PICK_INSTRUCTIONS_MESSAGE = _message(
    "PICK_INSTRUCTIONS_MESSAGE",
    "React with the team you think will win on the messages above.",
)
EXPORT_SUCCESS_MESSAGE = _message(
    "EXPORT_SUCCESS_MESSAGE", "Game data exported to Sheets."
)
EXPORT_NO_DATA_MESSAGE = _message("EXPORT_NO_DATA_MESSAGE", "No values to update")
CURRENT_WEEK_LABEL = _message("CURRENT_WEEK_LABEL", "current")
RESULTS_HEADER_TEMPLATE = _message("RESULTS_HEADER_TEMPLATE", "Points for week {week}:")
SEASON_TOTAL_LABEL = _message("SEASON_TOTAL_LABEL", "Season total:")
RESULTS_UPDATED_MESSAGE = _message(
    "RESULTS_UPDATED_MESSAGE", "✅ Results for week {week} updated."
)

# PPR (cogs/ppr.py)
PPR_NO_DATA_MESSAGE = _message(
    "PPR_NO_DATA_MESSAGE", "@everyone, this week's PPR update: No data available."
)
PPR_UPDATE_MESSAGE = _message(
    "PPR_UPDATE_MESSAGE", "@everyone, this week's PPR update:\n```\n{rankings}\n```"
)

# Fantasy reminders (cogs/fantasy_reminders.py)
WAIVER_REMINDER_MESSAGE = _message(
    "WAIVER_REMINDER_MESSAGE", "@everyone Don't forget waivers!"
)
DIGEST_FAILURE_MESSAGE = _message(
    "DIGEST_FAILURE_MESSAGE", "Could not generate matchup digest this week."
)
COOLDOWN_MESSAGE_TEMPLATE = _message(
    "COOLDOWN_MESSAGE_TEMPLATE",
    "{user} tried again too soon! Please wait {seconds:.1f} more seconds.",
)

# Weekly matchup digest
RECAP_HEADER_TEMPLATE = _message(
    "RECAP_HEADER_TEMPLATE", "**Weekly recap (Week {week}):**"
)
NO_GAMES_RECAP_LABEL = _message("NO_GAMES_RECAP_LABEL", "- No games to recap.")
AWARDS_HEADER_LABEL = _message("AWARDS_HEADER_LABEL", "**Weekly awards:**")

# Award values, e.g. "Team X with 142.30 points"
SCORE_SUMMARY_TEMPLATE = _message(
    "SCORE_SUMMARY_TEMPLATE", "{team} with {score:.2f} points"
)
BENCH_SUMMARY_TEMPLATE = _message(
    "BENCH_SUMMARY_TEMPLATE", "{team} with {score:.2f} points on the bench"
)
OVERACHIEVER_SUMMARY_TEMPLATE = _message(
    "OVERACHIEVER_SUMMARY_TEMPLATE", "{team} overachieved by {diff:.2f} vs. proj."
)
UNDERACHIEVER_SUMMARY_TEMPLATE = _message(
    "UNDERACHIEVER_SUMMARY_TEMPLATE", "{team} underachieved by {diff:.2f} vs. proj."
)
STANDINGS_LINE_TEMPLATE = _message(
    "STANDINGS_LINE_TEMPLATE",
    "{medal}{rank}. {team}: {wins}-{losses} ({points:.2f} points)",
)
NAILBITER_LABEL = _message("NAILBITER_LABEL", "Nail-biter")
TOP_SCORER_LABEL = _message("TOP_SCORER_LABEL", "Top scorer")
LOWEST_SCORER_LABEL = _message("LOWEST_SCORER_LABEL", "Lowest scorer")
BENCH_AWARD_LABEL = _message("BENCH_AWARD_LABEL", "Best bench")
OVERACHIEVER_LABEL = _message("OVERACHIEVER_LABEL", "Overachiever")
UNDERACHIEVER_LABEL = _message("UNDERACHIEVER_LABEL", "Underachiever")
WIN_STREAKS_HEADER_LABEL = _message("WIN_STREAKS_HEADER_LABEL", "**Win streaks (3+):**")
LOSS_STREAKS_HEADER_LABEL = _message(
    "LOSS_STREAKS_HEADER_LABEL", "**Loss streaks (3+):**"
)
NO_STREAKS_LABEL = _message("NO_STREAKS_LABEL", "- None")
WIN_STREAK_LINE_TEMPLATE = _message(
    "WIN_STREAK_LINE_TEMPLATE", "{name}: {run} wins in a row"
)
LOSS_STREAK_LINE_TEMPLATE = _message(
    "LOSS_STREAK_LINE_TEMPLATE", "{name}: {run} losses in a row"
)
FINAL_STANDINGS_BANNER_TEMPLATE = _message(
    "FINAL_STANDINGS_BANNER_TEMPLATE", "**{league_name} {year}**"
)
SEASON_END_MESSAGE = _message("SEASON_END_MESSAGE", "Thanks for the season! \U0001f389")
PREVIEW_HEADER_TEMPLATE = _message(
    "PREVIEW_HEADER_TEMPLATE", "**Next week's games (Week {week}):**"
)

# Inactive-player alerts
INACTIVE_ALERT_HEADER_TEMPLATE = _message(
    "INACTIVE_ALERT_HEADER_TEMPLATE",
    "<@{user}>: You have inactive players in your lineup!",
)
INACTIVE_PLAYER_LINE_TEMPLATE = _message(
    "INACTIVE_PLAYER_LINE_TEMPLATE", "- {name} ({status}) starts around {time}"
)
INACTIVE_PLAYER_LINE_WITH_TEAM_TEMPLATE = _message(
    "INACTIVE_PLAYER_LINE_WITH_TEAM_TEMPLATE",
    "- {team}: {name} ({status}) starts around {time}",
)
SOON_LABEL = _message("SOON_LABEL", "soon")
INACTIVE_FALLBACK_HEADER = _message(
    "INACTIVE_FALLBACK_HEADER",
    "@everyone Someone has inactive players in their active lineup:",
)
