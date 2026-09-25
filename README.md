# QuarterBot

[![CI](https://github.com/AnBasement/quarterbot/actions/workflows/main.yml/badge.svg)](https://github.com/AnBasement/quarterbot/actions/workflows/main.yml) [![codecov](https://codecov.io/gh/AnBasement/quarterbot/branch/main/graph/badge.svg?token=YdZEPsANH4)](https://codecov.io/gh/AnBasement/quarterbot)

A Discord bot for ESPN fantasy football leagues. It runs a weekly NFL pick'em game, tracks the league's power ranking (PPR) through Google Sheets, and posts reminders so nobody forgets their waivers.

QuarterBot was originally built for the private server of the fantasy league Fest i Vest, as a project to learn Python, using AI as an assistant. As I've used Render with UptimeRobot to host it, this README is based on that setup. If you use a different host or run it locally, setup wont be identical.

## Features

Commands use English names by default (e.g. `!results`), with the original Norwegian names kept as permanent aliases (e.g. `!resultater`). Both work for everyone, no setup required.

### Weekly Pick'em

The league's weekly winner-pick pool. Each week, participants pick a winner by reacting with a team's logo on messages representing that week's games. This used to be tracked manually, the bot now handles the whole thing.

The feature's display name is configurable via `GAME_NAME` (default `Weekly Pick'em`).

- Integrated with the Google Sheets API
- Records participants' picks and writes them to a Sheets tab
- Fetches results from ESPN's API and color-codes the Sheets tab based on whether a participant guessed correctly
- Tracks participants' picks and scores weekly and across the season
- Posts weekly and season results to a dedicated Discord channel

**Team logos:** the bot works out of the box with a built-in set of generic Unicode emoji for all 32 NFL teams plus a tie/draw, no setup needed, and Discord reactions require a real emoji either way. If your server has uploaded its own custom team emoji, the bot automatically prefers those instead, matched by name. To use your own logos, upload custom emoji to your server (Server Settings → Emoji) named exactly:

`ne` `buf` `nyj` `mia` `bal` `cin` `cle` `pit` `hou` `ind` `jax` `ten` `den` `kc` `lv` `lac` `dal` `nyg` `phi` `was` `chi` `det` `gb` `min` `atl` `car` `no` `tb` `ari` `lar` `sf` `sea` `draw`

No code or environment variable changes needed: the bot checks for a matching emoji name on its own server automatically.

### PPR

PPR is the fantasy league's own "power ranking", a number that attempts to capture how well a team did over a season, based on total points scored, lowest single-game score, and win rate, normalized against the rest of the league's results. This is heavily inspired/ripped off from the Oklahomiraqi League's OPR system. The formula itself lives in the Google Sheet's template, not in this codebase. The bot only reads the already-calculated value, so any league using this bot inherits the same formula automatically unless they edit their own copy of the spreadsheet.

The formula, in plain terms:

1. **Raw score** = `(avg points per game × 6) + ((highest + lowest single-game score) × 2) + (win% × 200 × 2)`, all divided by 10
2. **Final PPR** = that raw score, divided by the league's average raw score for the season

Dividing by the league average in step 2 normalizes the number so `1.0` always means "exactly league average", which keeps PPR comparable from one season to the next even as overall scoring levels rise or fall.

- Fetches the PPR value from the league's official Sheets document
- Stores a snapshot every week
- Posts an updated PPR ranking each week reflecting leaderboard movement and PPR changes

### Custom commands

Add your own `!commands` that reply with a fixed message, like links or inside jokes. Copy `custom_commands.example.json` to `custom_commands.json` in the bot's root folder and edit it:

```json
{
  "rules": "League rules: https://example.com/your-league-rules",
  "doink": "DOINK"
}
```

Each entry is `"command name": "reply"`, so the example above gives `!rules` and `!doink`. On Render, upload the file as a Secret File named `custom_commands.json`. The file is optional, and a broken one is logged and skipped rather than stopping the bot. Built-in command names (like `export`) can't be used.

### Other

The bot sends a reminder every Tuesday not to forget waivers before the new week starts.

## Planned features

### Trivia

A trivia cog is under development.

- Participants have 60 seconds to answer; faster answers earn more points
- The leaderboard is tracked in an external Sheets document
- Support for single questions and 10-question rounds
- Multiple categories, including a general NFL category and one per decade

### League document automation

It's theoretically possible to fully automate updates to the league document using the ESPN Fantasy API. Missing documentation for that API complicates it.

- Fetch season points-for and points-against
- Check last week's score and compare against the highest/lowest recorded game score cells
- Update win/loss counts and league standings
- Update free agent counts
- Update position/ordering on the all-time leaderboard

## Getting started

This guide takes you from nothing to a running bot on your own Discord server. It assumes you host the bot on [Render](https://render.com), but any host that can run Python works, including running it locally. If you're unfamilar, set aside an hour or two the first time: most of it is clicking through Discord's, Google's and ESPN's websites.

If you get stuck, check [Troubleshooting](#troubleshooting) below, or open an issue.

### 1. What you need before you start

- A Discord server where you're an administrator
- A Google account (for the spreadsheets)
- An ESPN fantasy football league
- A GitHub account (to copy the code) and a Render account (to run it). Both can be created as part of the steps below.

You'll collect a handful of IDs, keys and files along the way. Paste them into a text file as you go: step 5 puts them all in one place.

### 2. Create the Discord bot

A Discord bot is an "application" you create on Discord's developer site, then invite to your server.

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**. The name you give it is the bot's name in your server, e.g. "QuarterBot".
2. Open the **Bot** tab:
    - Click **Reset Token** and copy the token. This is your `DISCORD_TOKEN`. Treat it like a password: anyone who has it can control your bot.
    - Turn on **Message Content Intent** and **Server Members Intent**. "Intents" are permissions for what the bot is allowed to see. Without these two it starts, but ignores every command.
3. Open **OAuth2 → URL Generator**:
    - Under **Scopes**, tick `bot`. A **Bot Permissions** box appears below it.
    - Tick **View Channels**, **Send Messages**, **Read Message History**, **Add Reactions** and **Mention Everyone**.
    - Open the generated URL at the bottom of the page, pick your server and click **Authorize**. The bot shows up in the member list, offline until you start it.

Then collect some IDs from Discord:

1. Turn on **Developer Mode** in Discord (User Settings → Advanced). This adds a *Copy ID* option when you right-click channels and users.
2. Pick or create three text channels: one for reminders, one where the weekly games are posted, and one for admin warnings (recommended to be admin-only). Right-click each → **Copy Channel ID**. These are `REMINDER_CHANNEL_ID`, `GAME_CHANNEL_ID` and `ADMIN_CHANNEL_ID`.
3. Right-click yourself → **Copy User ID**. This goes in `ADMIN_IDS` (add more admins separated by commas). Admins can run commands like `!export` and `!results`.

### 3. Set up Google Sheets access

The bot reads and writes Google Sheets as a "service account": a robot Google user that belongs to you. You create it once, then share your spreadsheets with it like you would with a person. This is the fiddliest step, so take it slowly.

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and create a new project (free). Any name works.
2. Go to **APIs & Services → Library**, and enable both **Google Sheets API** and **Google Drive API**. The bot needs Drive to find your spreadsheets by name.
3. Go to **APIs & Services → Credentials → Create credentials → Service account**. Give it a name and click **Done** (the optional steps can be skipped).
4. Open the new service account → **Keys → Add key → Create new key → JSON**. A file downloads. Rename it to `credentials.json`. Keep it secret: it gives access to every sheet shared with this account.
5. On the service account's page, copy its email address (it ends in `.iam.gserviceaccount.com`). You'll share your sheets with it below.

Now create the two spreadsheets:

- **League spreadsheet (for PPR):** [make a copy of the template spreadsheet](https://docs.google.com/spreadsheets/d/1ySnHGbpJePAFt0-NZyx3gHuUbfC7duTcxcW3rSziMUU/edit?usp=sharing) (File → Make a copy). It has the tab structure and the PPR formula the bot expects.
- **Pick'em spreadsheet:** create a new, empty spreadsheet. In its first tab:
    1. Click the row number **2** on the left to select the whole row, then set Format → Number → **Plain text**. Do this before typing the IDs: Discord IDs are 18–19 digits long, and Sheets could otherwise round them, so the bot would never recognize anyone's picks.
    2. Row 1: a label in `A1` (e.g. `Name`), then one participant's name per column from `B1`.
    3. Row 2: `Discord ID` in `A2`, then each participant's Discord user ID below their name.

    Leave everything below row 2 empty. The bot writes each week's games there itself, and creates a `State` tab on its first start to remember which weeks it has handled.

**Share both spreadsheets** with the service account's email address (Share → paste the address → **Editor**). The bot finds sheets by their name, so give each one a name that no other sheet shared with the service account has. You'll put the names in `LEAGUE_SHEET_NAME` and `PICKEM_SHEET_NAME`.

### 4. Connect to ESPN

- **`ESPN_LEAGUE_ID`:** open your league on [ESPN Fantasy](https://fantasy.espn.com/). The address in your browser contains `leagueId=` followed by a number. That number is your league ID.
- **`ESPN_YEAR`:** the season, e.g. `2026`. Update it when a new season starts.
- **`ESPN_S2` and `ESPN_SWID`** (only for private leagues, which most are): ESPN only shows a private league to logged-in members, so the bot borrows your login from two browser cookies.
    1. Log in to ESPN Fantasy in your browser and open your league.
    2. Open the browser's developer tools (in Chrome: View → Developer → Developer Tools), then **Application → Cookies → `https://fantasy.espn.com`**.
    3. Copy the value of `espn_s2` into `ESPN_S2`, and `SWID` into `ESPN_SWID` (keep the curly braces).

    These cookies are as private as your ESPN password. They expire eventually; if ESPN features stop working and the admin channel reports ESPN errors, copy fresh ones.

**Optional: `discord_ids.json`.** Tells the bot which Discord user owns which ESPN team, so injury warnings ("you have inactive players in your lineup") ping the right person. Find each team's ID in the address of its ESPN team page (`teamId=`), and create the file like this, where the second number is the Discord ID of the team manager:

```json
{
  "1": "123456789012345678",
  "2": "234567890123456789"
}
```

Without the file, everything else works, and injury warnings are still sent: as one general @everyone message instead of pinging each manager.

### 5. Settings (environment variables)

Settings are given to the bot as "environment variables": named values like `GAME_CHANNEL_ID=123...` that the bot reads when it starts. On Render you enter them on a settings page; on your own computer they go in a file called `.env`.

[`.env.example`](.env.example) lists every setting with a short explanation. These are the most important ones:

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | Yes | Bot token from the Discord Developer Portal |
| `ADMIN_IDS` | Yes | Comma-separated list of Discord user IDs with admin access |
| `REMINDER_CHANNEL_ID`, `GAME_CHANNEL_ID`, `ADMIN_CHANNEL_ID` | Yes | Discord channel IDs the bot posts to |
| `GAME_NAME` | No (default `Weekly Pick'em`) | Display name for the weekly pick'em game |
| `LEAGUE_NAME` | No (default `The League`) | Display name for the fantasy league itself, used e.g. in the season-end standings banner |
| `LEAGUE_TIMEZONE` | No (default `Europe/Oslo`) | IANA timezone name used for reminder scheduling and date calculations. See the [IANA timezone list](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) for valid values (e.g. `America/New_York`, `Europe/London`) |
| `GOOGLE_SHEETS_KEYFILE` | No (default `credentials.json`) | Path to the Google service account key |
| `PORT` | No (default `8080`) | Port for the small keep-alive web server. Most hosts set this automatically, so you normally don't set it yourself |
| `ESPN_LEAGUE_ID`, `ESPN_YEAR`, `ESPN_S2`, `ESPN_SWID` | Yes | Access to the ESPN Fantasy API for the league |
| `PICKEM_SHEET_NAME`, `LEAGUE_SHEET_NAME` | No (defaults `Pick'em`, `League`) | Names of the pick'em spreadsheet and the league (PPR) spreadsheet. The bot finds them by name, so each must be unique among the sheets shared with its service account |
| `WEEKLY_POINTS_SHEET_LABEL`, `SEASON_TOTAL_SHEET_LABEL`, `DRAW_SHEET_LABEL` | No (defaults `Weekly points`, `Season total`, `Tie`) | Labels the pick'em feature writes into its sheet and reads back later. If your sheet already contains rows with other labels, set these to match exactly, or season totals restart from zero |
| `PPR_MANAGERS` | Yes, for `!ppr` | One entry per manager, separated by `;`: the manager's tab name in the league sheet, optionally followed by `=` and the team name to show, e.g. `Alice=Aces;Bob=Bombers`. Update when managers join/leave or rename their team |
| `PPR_HISTORY_SHEET_NAME` | No (default `PPR History`) | Name of the Sheets tab that stores PPR snapshot history |
| `FANTASY_FINAL_WEEK` | No (default `17`) | Final week of the league's ESPN fantasy season (regular + playoff weeks); check your own league's ESPN settings |

**Bot-posted message text:** everything the bot says in Discord (reminders, confirmations, the weekly matchup digest, inactive-player alerts, and so on) is also configurable, each with a neutral English default. See `.env.example` for every variable name, its default, and any `{placeholder}` values a message fills in. No code changes are needed to run the bot in your own language or wording.

Besides the settings, the bot reads these files from its folder:

- `credentials.json`: the Google key from step 3
- `discord_ids.json` (optional): ESPN team → Discord user, from step 4
- `custom_commands.json` (optional): your own commands, see [Custom commands](#custom-commands)

> **Keep these private.** `.env`, `credentials.json` and `discord_ids.json` contain your bot token, Google key, ESPN login and your members' Discord IDs. They're listed in `.gitignore`, so git leaves them out of your commits. Don't remove them from `.gitignore` or add them with `git add -f`: a fork of a public repository is always public, so anything committed there can be seen by anyone. If a token or key ever ends up on GitHub, reset it right away (Discord: **Reset Token**; Google: delete the key and create a new one).


### 6. Run it on Render

1. **Copy the code:** on this repository's GitHub page, click **Fork**. This gives you your own copy, which Render will run.
2. **Create the service:** sign up at [Render](https://render.com) with your GitHub account, then **New → Web Service** and pick your fork.
3. **Fill in:**
    - Runtime: **Python 3**
    - Build command: `pip install -r requirements.txt`
    - Start command: `python -m core.bot`
4. **Environment:** add every setting from step 5, plus `PYTHON_VERSION=3.12` (otherwise Render picks its own Python version). If you've collected them in a `.env`-style text file, Render's **Add from .env** option lets you paste them all at once.
5. **Secret Files** (in the same Environment page): upload `credentials.json`, and `discord_ids.json` / `custom_commands.json` if you made them. Use exactly those file names.
6. **Deploy**, and watch the logs. After a minute or so you should see `Bot logged in as …`, and the bot comes online in Discord. Try `!ping`.

On its first start the bot creates the `State` tab in your pick'em sheet, then posts this week's games in your game channel. From then on it runs on its own: games are posted each week, and last week's picks are exported and scored automatically on Tuesdays.

**About Render's free plan:** a free web service goes to sleep after a period without web traffic, and a sleeping bot is offline in Discord. The bot runs a small web page (`Bot is running!`) for exactly this reason: either use a paid instance, or point a free uptime-monitoring service at your Render URL so it's visited every few minutes. Check Render's current plans, as their free tier changes from time to time.

**Running it on your own computer instead** (useful for testing): install Python 3.12, then

```bash
git clone <your fork's URL>
cd <the folder it created>
python -m venv .venv
source .venv/bin/activate    # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env         # then fill in .env
python -m core.bot
```

Put `credentials.json` (and the optional JSON files) in the same folder. Don't run the same bot token on your computer and on Render at the same time: both copies would answer every command.

### Troubleshooting

- **The bot is online but ignores commands:** Message Content Intent or Server Members Intent is off (step 2).
- **The bot stops at startup with `Required environment variable … is not set`:** one of the required settings is missing. The message names it.
- **Every command gets two replies:** the same bot token is running in two places, e.g. on Render and on your computer.
- **The admin channel says it failed to load the State sheet, and no games are posted:** the bot can't open your pick'em spreadsheet. Check that `PICKEM_SHEET_NAME` matches the spreadsheet's name exactly, and that the sheet is shared with the service account's email address.
- **Your picks are never recognized:** the Discord IDs in row 2 of the pick'em sheet were rounded. Format the cells as plain text and paste the IDs again (step 3).
- **ESPN features stop working mid-season:** the `ESPN_S2`/`ESPN_SWID` cookies might have expired. Copy fresh ones (step 4).

## Project structure

```text
├── cogs/                           # Discord bot modules
│   ├── fantasy_reminders.py        # Waiver and kickoff reminders
│   ├── pickem.py                   # Core logic for the pick'em game
│   ├── ppr.py                      # Updates the PPR leaderboard
│   ├── responses.py                # Custom commands from custom_commands.json
│   ├── sheets.py                   # Google Sheets integration
│   └── utility.py                  # Small helper commands
├── core/                           # Core functionality
│   ├── bot.py                      # Bot initialization
│   ├── decorators.py               # Admin-permission checks
│   ├── errors.py                   # Bot error types
│   ├── keep_alive.py               # Web server for uptime
│   ├── logging_config.py           # Centralized logging setup
│   └── utils/                      # Helper utilities
│       ├── discord_helpers.py      # Shared helpers for discord.py objects
│       ├── espn_helpers.py         # Shared helpers for ESPN's API
│       ├── global_cooldown.py      # Cooldown for command spam
│       └── quiet_hours.py          # Keeps reminders out of the night
├── data/                           # Static data and configuration
│   ├── channel_ids.py              # Discord channel IDs (from env vars)
│   ├── config.py                   # Other env-var-driven configuration
│   ├── discord_ids.py              # Loads discord_ids.json (ESPN team → Discord user)
│   ├── messages.py                 # Bot-posted message text (from env vars)
│   └── teams.py                    # NFL team data and emojis
├── tests/                          # Test suite (one test_*.py per module)
├── .env.example                    # Every environment variable, with defaults
└── custom_commands.example.json    # Example format for custom commands
```

## Configuration

The project uses the following configuration files:

- `pyproject.toml`: development tool configuration (ruff, pytest)
- `requirements.txt`: Python packages the bot needs to run (what the host installs)
- `.env`: environment variables (not in the repo)
- `credentials.json`: Google Sheets API access (not in the repo)

## Development

The project follows these development practices:

- Type hints and docstrings for better code comprehension
- Comprehensive test coverage with pytest
- Code formatted with black, and quality checks with ruff and mypy
- CI/CD through GitHub Actions
- Google Sheets integration for data storage

To contribute, see [CONTRIBUTING.md](.github/CONTRIBUTING.md) for the workflow and the checks to run before opening a pull request. Alternatively, file an issue.

## Testing

Run the test suite:

```bash
python -m pytest
```

Run type checking:

```bash
mypy .
```

## Acknowledgements

This project drew inspiration from [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot), for ideas around cogs and bot structure.
[Claude](https://claude.ai) has been very helpful throughout the bot's development, especially when it comes to writing test files and docstrings.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for what has changed in each release.

## License

This project is licensed under the GNU General Public License v3.0. See the [LICENSE](LICENSE) file for details.
