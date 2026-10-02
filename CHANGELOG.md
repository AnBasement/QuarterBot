# Changelog

All notable changes to this project are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/) starting with the `1.0.0` public release.

## [Unreleased]

### Added
- Picks lock automatically at the first Sunday kickoff (the first game of the week in the playoffs): they're exported to the sheet then, so later reactions don't count. Thursday and Saturday games are on the honor system. A failed lock is retried for an hour, and the admin channel is told once if it still hasn't worked after 10 minutes. If it never works, picks are exported on Tuesday as before
- The `State` tab gets a third column, `last_exported_week`, added automatically to existing tabs

### Changed
- The Sunday reminder's default text says that picks lock at kickoff
- The Tuesday run only scores weeks that were locked; it no longer exports them again
- ESPN or Google hiccups during the pick lock are retried quietly instead of posting to the admin channel on every attempt
- Weeks are scored from Tuesday 18:00, as soon as ESPN has marked every game final, and the next week's games are posted right after. Before, both waited until ESPN switched weeks on Wednesday morning. The week waits for delayed games
- The Super Bowl week's games are posted right after the conference championships, since the Pro Bowl week in between has nothing to pick
- A game still not final 60 hours after the week's last kickoff (postponed or canceled) no longer holds the week up: the week is scored without it, and the admin channel is told

### Fixed
- An unplayed game (postponed or canceled) was scored as a 0-0 tie. It's now void: nobody gets points for it
- A Discord error while posting the week's games stopped the weekly posting until the bot restarted. It's now retried an hour later
- Any other unexpected error in the weekly scoring and posting (e.g. ESPN changing its data format) also stopped it silently until a restart. It's now logged, reported to the admin channel and retried an hour later, like the other scheduled tasks
- If the game channel couldn't be found, the week was still announced and marked as posted, without any games, and never retried. It's now retried every hour, and the admin channel is told once
- Export and results read the pick'em players' Discord IDs without a time limit, freezing the bot while Google answered, and a Google error there wasn't reported as an export or results error. That read now works like every other spreadsheet call
- A failed Google login during the pick lock posted to the admin channel on every retry, instead of being retried quietly like other Google hiccups

## [1.0.0] - 25-09-2026

The first public release. Before this, the bot ran privately for one league.

### Added
- Settings as environment variables, all documented in `.env.example`: channel IDs, `LEAGUE_TIMEZONE`, `FANTASY_FINAL_WEEK`, `GAME_NAME`, `LEAGUE_NAME`, the spreadsheet names (`PICKEM_SHEET_NAME`, `LEAGUE_SHEET_NAME`, `PPR_HISTORY_SHEET_NAME`) and the pick'em sheet labels (`WEEKLY_POINTS_SHEET_LABEL`, `SEASON_TOTAL_SHEET_LABEL`, `DRAW_SHEET_LABEL`)
- Every message the bot posts can be overridden with an environment variable, e.g. to translate it. `\n` in a value becomes a line break
- `PPR_MANAGERS` sets the manager tabs and team names for `!ppr`
- Custom commands from an optional `custom_commands.json` (see `custom_commands.example.json`)
- Team emoji: built-in Unicode defaults, or the server's own custom emoji when named after the team (e.g. `ne`)
- The keep-alive web server listens on the host's `PORT` (default `8080`)
- A usage hint for commands with a missing or invalid required argument (`COMMAND_USAGE_MESSAGE`)
- Type hints, checked by mypy in CI
- README: setup notes and a plain-language explanation of the PPR formula

### Changed
- Everything is in English. Commands have English names, and the Norwegian names still work as aliases
- Renamed `cogs/vestsk_tipping.py` to `cogs/pickem.py` (`VestskTipping` and `VestskError` to `Pickem` and `PickemError`), and `data/brukere.py` to `data/discord_ids.py`
- Game reminders are timed from the actual kickoff and kept out of quiet hours (22:00 to 08:00), in any timezone. The Sunday reminder goes out an hour before the first game
- Inactive-player warnings are kept out of quiet hours too
- The waiver reminder is only sent during the fantasy season
- The matchup digest is split into several messages when it's longer than Discord allows
- `!export` can be rerun: it updates the week's rows instead of adding them again
- The Pro Bowl is no longer posted as a game to pick
- Super Bowl picks are exported and scored automatically
- The admin channel is told once when inactive-player checks start failing, and once when they recover
- A restart late on a Tuesday no longer sends the Tuesday messages again
- `!ping` always replies "Pong! ✅"
- Pinned `espn_api`, `google-auth` and `aiohttp` in `requirements.txt`. CI runs Python 3.12
- Broad `except Exception` blocks narrowed to specific exceptions, except around the scheduler loops. `print()` replaced with logging
- Shorter comments and docstrings, and the pull request template is in English
- Test suite rewritten and extended; tests that couldn't fail were replaced

### Removed
- Hardcoded league data: team emoji IDs, manager and team names, the inside-joke commands and the league's sheet names
- `PING_MESSAGE` and `PPR_MANAGER_NAMES` (replaced by `PPR_MANAGERS`)
- `format_cell()` and the `gspread-formatting` dependency, and `ResponseError`
- `runtime.txt`, which Render doesn't read. On Render, set `PYTHON_VERSION` instead

### Fixed
- If Google Sheets failed during `!export` or `!results`, the week was still marked as done. The weekly processing now retries
- Rerunning `!results` counted the week's points twice, and a rematch from an earlier week was scored again
- `!results` run before `!export` overwrote another week's rows. It now reports an error
- `!export` on a new sheet with empty A1 and A2 overwrote the Discord ID row
- `!results` broke for leagues with more than 25 managers
- A new install started mid-season never posted any games
- The season-end digest went out a week early, repeated every Tuesday after that, and ranked teams by record instead of playoff result
- The digest "recapped" week 1 before it had been played
- A slow ESPN response froze the whole bot
- The season-end banner had the league's name and the year hardcoded
- `!ppr` always read the 2026 row. It now uses `ESPN_YEAR`
- `!ppr` locked out admins listed after a space in `ADMIN_IDS`
- A non-admin trying an admin command was reported to the admin channel as an error
- The bot kept running (and answering the health check) after failing to start
- Running locally with a `.env` file crashed at startup
- Several message examples in `.env.example` didn't match the real defaults, and some broke messages when copied
- The "credentials file not found" error repeated itself
- Saving state: a week value of `0` was discarded, and `.update()` passed its arguments in the wrong order for gspread 6
- Loading state couldn't tell "no state yet" from a failed load or corrupt data
- The season window had a wrong timezone offset (+00:53)
- Channel IDs were read as text instead of numbers, so no channel was found
- `get_league()` raised `TypeError` instead of `ValueError` when the ESPN settings were missing
- A pick'em sheet that can't be opened at startup (e.g. a wrong sheet name) is now reported in the admin channel and retried, instead of silently stopping the weekly game posts
- A reaction that isn't one of the game's two teams (e.g. 👍, or the wrong team) no longer replaces a player's pick on export
- Without discord_ids.json (or with a broken one), injury warnings stopped completely. They now go out as one general @everyone message