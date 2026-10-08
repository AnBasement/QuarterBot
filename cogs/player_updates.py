"""Posts NFL injury and status reports to PLAYER_UPDATES_CHANNEL_ID."""

import re
from core.utils.espn_site import parse_espn_date
from typing import Any
import asyncio
import logging
import pytz
from discord.ext import commands
from datetime import datetime
import time
import requests


from cogs.sheets import get_or_create_tab
from core.utils.discord_helpers import get_text_channel
from core.utils.espn_helpers import get_league
from core.utils.espn_site import fetch_json
from data.channel_ids import ADMIN_CHANNEL_ID, PLAYER_UPDATES_CHANNEL_ID
from data.discord_ids import load_discord_ids
from data.messages import PLAYER_UPDATE_TEMPLATE
from data.config import PICKEM_SHEET_NAME
from espn_api.requests.espn_requests import (
    ESPNAccessDenied,
    ESPNInvalidLeague,
    ESPNUnknownError,
)
from gspread.worksheet import Worksheet

logger = logging.getLogger(__name__)

ERROR_BACKOFF_SECONDS = 600
INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
INJURIES_PAGE = "https://www.espn.com/nfl/injuries"  # fallback if no player page
INJURY_STATUSES = {"Questionable", "Doubtful", "Out", "Injured Reserve"}
# ESPN's reports write an injury right after the player's name: "Sweat (groin)
# is active...". Up to four words of the player's name, then the brackets.
# One word of a player's name: either it starts with a capital letter
# ("Sweat", "St.", "Smith-Njigba"), or it's one of the small lowercase
# words some surnames have ("van" and "den" in "van den Berg").
NAME_WORD = r"(?:[A-Z][\w.'\-]*|van|von|der|den|de|da|di|du|la|le|del)"
NAME_THEN_BRACKETS = re.compile(rf"^(?:{NAME_WORD}\s){{0,3}}{NAME_WORD} \(([^)]+)\)")
NFL_TIMEZONE = pytz.timezone("America/New_York")
GAME_DAYS = {0, 3, 5, 6}  # Monday, Thursday, Saturday, Sunday (Monday=0)
GAME_DAY_CHECK_SECONDS = 60
OTHER_DAY_CHECK_SECONDS = 300
INACTIVE = re.compile(r"\binactive\b", re.IGNORECASE)
PLAYER_ID_IN_LINK = re.compile(r"/id/(\d+)")  # e.g. ".../player/_/id/4363538/..."
ROSTER_REFRESH_SECONDS = 1800  # rosters change with pickups and trades
STATE_TAB = "Player updates"


def is_injury_news(report: dict[str, Any]) -> bool:
    """Whether a report is worth posting: an injury status, or an "Active"
    report that names an injury (cleared to play, practice updates). Not the
    after-game stat lines, not coaches' decisions, and not the plain entries
    of the official inactive lists."""
    comment = report.get("shortComment", "")
    match = NAME_THEN_BRACKETS.match(comment)
    if match and match.group(1).lower() == "coach's decision":
        return False
    if match is None and INACTIVE.search(comment):
        return False  # a plain "inactive": no injury named
    return report.get("status") in INJURY_STATUSES or match is not None


def player_id(report: dict[str, Any]) -> int | None:
    """The player's ESPN ID, read from the link to their ESPN page. The same ID
    the fantasy league uses for its players."""
    for link_info in report.get("athlete", {}).get("links", []):
        found = PLAYER_ID_IN_LINK.search(link_info.get("href", ""))
        if found:
            return int(found.group(1))
    return None


def format_update(report: dict[str, Any], owner: int | None = None) -> str:
    """The Discord message for one report, with a ping for `owner` (a Discord
    user ID) if the player is on a fantasy team."""
    athlete = report.get("athlete", {})
    link = next(
        (
            link_info["href"]
            for link_info in athlete.get("links", [])
            if "playercard" in link_info.get("rel", [])
        ),
        INJURIES_PAGE,
    )
    message = PLAYER_UPDATE_TEMPLATE.format(
        player=athlete.get("displayName", "?"),
        team=athlete.get("team", {}).get("abbreviation", "?"),
        position=athlete.get("position", {}).get("abbreviation", "?"),
        status=report.get("status", "?"),
        comment=report.get("shortComment", ""),
        link=link,
    )
    if owner is not None:
        message += f"\n<@{owner}>"
    return message


def new_reports(
    reports: list[dict[str, Any]], last_minute: str, ids_at_last_minute: set[str]
) -> list[dict[str, Any]]:
    """The reports not handled yet, oldest first: from a later minute than
    `last_minute`, or from that minute but not in `ids_at_last_minute`."""
    last = parse_espn_date(last_minute)
    fresh = [
        r
        for r in reports
        if parse_espn_date(r["date"]) > last
        or (parse_espn_date(r["date"]) == last and r["id"] not in ids_at_last_minute)
    ]
    return sorted(fresh, key=lambda r: parse_espn_date(r["date"]))


def advance_marker(
    last_minute: str, ids_at_last_minute: set[str], report: dict[str, Any]
) -> tuple[str, set[str]]:
    """The marker after handling `report`: its minute, and the IDs handled
    from that minute."""
    if parse_espn_date(report["date"]) > parse_espn_date(last_minute):
        return report["date"], {report["id"]}
    return last_minute, ids_at_last_minute | {report["id"]}


def seconds_until_next_check(now: datetime) -> int:
    """Every minute on NFL game days, every 5 minutes otherwise. Game days go
    by the US Eastern date, so a late Thursday or Monday night game counts."""
    if now.astimezone(NFL_TIMEZONE).weekday() in GAME_DAYS:
        return GAME_DAY_CHECK_SECONDS
    return OTHER_DAY_CHECK_SECONDS


class PlayerUpdates(commands.Cog):
    """Posts league moves to PLAYER_UPDATES_CHANNEL_ID as they happen."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.last_minute: str | None = None
        self.ids_at_last_minute: set[str] = set()
        self._failing = False
        self.task: asyncio.Task[None] | None = None
        self.owners: dict[int, int] = {}
        self._owners_loaded_at: float | None = None
        self._owners_failing = False
        if PLAYER_UPDATES_CHANNEL_ID is None:
            logger.info("PLAYER_UPDATES_CHANNEL_ID not set; player update feature off.")
        else:
            self.task = self.bot.loop.create_task(self.scheduler())

    async def cog_unload(self) -> None:
        """Stops the background task."""
        if self.task is not None:
            self.task.cancel()

    async def _notify_admin(self, message: str) -> None:
        """Posts to the admin channel. A failed send is logged, never raised."""
        admin_channel = get_text_channel(self.bot, ADMIN_CHANNEL_ID)
        if admin_channel is None:
            return
        try:
            await admin_channel.send(message)
        except Exception as exc:
            # Broad on purpose: a failed notification must never stop the caller.
            logger.warning("Failed to send admin warning in PlayerUpdates: %s", exc)

    async def _state_tab(self) -> Worksheet:
        """The Player updates tab of the pick'em spreadsheet, created if missing."""
        return await asyncio.to_thread(
            get_or_create_tab,
            PICKEM_SHEET_NAME,
            STATE_TAB,
            ["last_minute", "ids_at_last_minute"],
        )

    async def _refresh_owners(self) -> None:
        """Reloads which manager has which player, at most every 30 minutes.
        Never raises: without discord_ids.json there's nobody to ping, and if
        ESPN fails, the reports go out without pings."""
        now = time.monotonic()
        if (
            self._owners_loaded_at is not None
            and now - self._owners_loaded_at < ROSTER_REFRESH_SECONDS
        ):
            return
        self._owners_loaded_at = now
        try:
            discord_ids = load_discord_ids()
        except (FileNotFoundError, ValueError):
            self.owners = {}
            return  # broken or no file: no pings
        try:
            league = await asyncio.to_thread(get_league)
        except (
            requests.exceptions.RequestException,
            ESPNAccessDenied,
            ESPNInvalidLeague,
            ESPNUnknownError,
            ValueError,
        ) as exc:
            logger.warning("Couldn't load fantasy rosters for player pings: %s", exc)
            if not self._owners_failing:
                self._owners_failing = True
                await self._notify_admin(
                    f"[player-updates] Couldn't load the fantasy rosters, reports "
                    f"are posted without pings: {exc}"
                )
            return  # keep the last list
        self._owners_failing = False
        self.owners = {
            player.playerId: discord_ids[team.team_id]
            for team in league.teams
            if team.team_id in discord_ids
            for player in team.roster
        }

    async def _load_marker(self) -> None:
        """Reads the saved marker, which is the newest minute handled and the player IDs
        handled at that minute."""
        tab = await self._state_tab()
        values = await asyncio.to_thread(tab.get, "A2:B2")
        row = values[0] if values else []
        self.last_minute = row[0] if row and row[0] else None
        ids_text = row[1] if len(row) > 1 else ""
        self.ids_at_last_minute = set(ids_text.split(",")) if ids_text else set()

    async def _save_marker(self) -> None:
        """Saves a marker of the newest minute handled and player ID handled at
        that minute."""
        tab = await self._state_tab()
        await asyncio.to_thread(
            tab.update,
            [[self.last_minute, ",".join(sorted(self.ids_at_last_minute))]],
            "A2",
        )

    async def check_once(self) -> None:
        """Posts the reports that came in since the last check. Raises if ESPN,
        Google or Discord fails; the scheduler handles that."""
        channel_id = PLAYER_UPDATES_CHANNEL_ID
        if channel_id is None:
            return
        channel = get_text_channel(self.bot, channel_id)
        if channel is None:
            logger.warning("Player updates channel %s not found.", channel_id)
            return

        if self.last_minute is None:
            await self._load_marker()
        await self._refresh_owners()
        data = await fetch_json(INJURIES_URL)
        reports = [
            r for team in data.get("injuries", []) for r in team.get("injuries", [])
        ]

        if not reports:
            return  # an empty feed: nothing to do this round
        if self.last_minute is None:
            # First start: take the newest report as the starting point, instead of
            # flooding the channel with two weeks' worth of reports.
            newest = max(reports, key=lambda r: parse_espn_date(r["date"]))
            self.last_minute = newest["date"]
            self.ids_at_last_minute = {
                r["id"] for r in reports if r["date"] == newest["date"]
            }
            await self._save_marker()
            logger.info("First player updates check: earlier reports not posted.")
            return

        start = (self.last_minute, set(self.ids_at_last_minute))
        try:
            for report in new_reports(
                reports, self.last_minute, self.ids_at_last_minute
            ):
                if is_injury_news(report):
                    pid = player_id(report)
                    owner = self.owners.get(pid) if pid is not None else None
                    await channel.send(format_update(report, owner))
                self.last_minute, self.ids_at_last_minute = advance_marker(
                    self.last_minute, self.ids_at_last_minute, report
                )
        finally:
            # Save whatever was handled, even if a later send failed, so a
            # restart doesn't post reports twice.
            if (self.last_minute, self.ids_at_last_minute) != start:
                await self._save_marker()

    async def _run_check(self) -> int:
        """Runs one check, tells the admin when it starts and stops failing.

        Returns the seconds to wait before the next check.
        """
        try:
            await self.check_once()
        except Exception as exc:
            # Broad on purpose: this is the scheduler loop's boundary, and one
            # failed check (ESPN, Google or Discord down) must not end it.
            logger.error("Player updates check failed: %s", exc)
            if not self._failing:
                self._failing = True
                await self._notify_admin(
                    f"[player-updates] Couldn't check for new updates: {exc}. "
                    "Retrying every 10 minutes; you'll be told when it works again."
                )
            return ERROR_BACKOFF_SECONDS

        if self._failing:
            self._failing = False
            await self._notify_admin(
                "[player-updates] Checking for new moves works again."
            )
        return seconds_until_next_check(datetime.now(NFL_TIMEZONE))

    async def scheduler(self) -> None:
        """Checks for new updates. Runs forever."""
        await self.bot.wait_until_ready()
        while True:
            await asyncio.sleep(await self._run_check())


async def setup(bot: commands.Bot) -> None:
    """Loads the cog (called by discord.py's load_extension)."""
    await bot.add_cog(PlayerUpdates(bot))
