"""Posts every add, drop, waiver claim and completed trade in the ESPN league."""

import asyncio
import logging
from typing import Any

from discord.ext import commands
from espn_api.football import League
from gspread.worksheet import Worksheet

from cogs.sheets import get_or_create_tab
from core.utils.discord_helpers import get_text_channel
from core.utils.espn_helpers import get_league
from data.channel_ids import ADMIN_CHANNEL_ID, TRANSACTIONS_CHANNEL_ID
from data.config import PICKEM_SHEET_NAME
from data.messages import (
    TRANSACTION_ADD_TEMPLATE,
    TRANSACTION_WAIVER_TEMPLATE,
    TRANSACTION_DROP_TEMPLATE,
    TRANSACTION_TRADE_HEADER,
    TRANSACTION_TRADE_LINE_TEMPLATE,
)

logger = logging.getLogger(__name__)

CHECK_SECONDS = 60  # how often ESPN is asked for new moves (one small request)
ERROR_BACKOFF_SECONDS = 600  # wait after a failed check
# Moves fetched per check. If more than this happen between two checks (e.g.
# while the bot is down), the oldest ones are never posted.
ACTIVITY_FETCH_SIZE = 25
STATE_TAB = "Transactions"  # tab in the pick'em spreadsheet


def _name(obj: Any) -> str:
    """A team's or player's name. espn_api sometimes only has an ID."""
    for attr in ("team_name", "name"):
        value = getattr(obj, attr, None)
        if value:
            return str(value)
    return str(obj)


def format_activity(activity: Any) -> str | None:
    """The Discord message for one ESPN activity, or None if there's nothing to post.

    An activity is one move: an add, a drop, an add/drop pair or a whole
    trade. espn_api gives its parts as (team, action, player, bid) tuples.
    """
    lines: list[str] = []
    trade: dict[str, list[str]] = {}
    for team, action, player, _bid in activity.actions:
        team_name, player_name = _name(team), _name(player)
        if action == "FA ADDED":
            lines.append(
                TRANSACTION_ADD_TEMPLATE.format(team=team_name, player=player_name)
            )
        elif action == "WAIVER ADDED":
            lines.append(
                TRANSACTION_WAIVER_TEMPLATE.format(team=team_name, player=player_name)
            )
        elif action == "DROPPED":
            lines.append(
                TRANSACTION_DROP_TEMPLATE.format(team=team_name, player=player_name)
            )
        elif action == "TRADE_RECEIVED":
            trade.setdefault(team_name, []).append(player_name)
        # TRADE_SENT is the same player seen from the other team, and anything
        # espn_api couldn't identify ("UNKNOWN") is skipped.
    if trade:
        lines.append(TRANSACTION_TRADE_HEADER)
        for team_name, players in trade.items():
            lines.append(
                TRANSACTION_TRADE_LINE_TEMPLATE.format(
                    team=team_name, players=", ".join(players)
                )
            )
    return "\n".join(lines) or None


def new_activities(activities: list[Any], last_date: int) -> list[Any]:
    """The activities newer than last_date, oldest first."""
    newer = [activity for activity in activities if activity.date > last_date]
    return sorted(newer, key=lambda activity: activity.date)


class Transactions(commands.Cog):
    """Posts league moves to TRANSACTIONS_CHANNEL_ID as they happen."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.league: League | None = None
        # Date of the newest posted move, in milliseconds (as ESPN gives it).
        # None until loaded from the spreadsheet.
        self.last_date: int | None = None
        self._failing = False
        self.task: asyncio.Task[None] | None = None
        if TRANSACTIONS_CHANNEL_ID is None:
            logger.info("TRANSACTIONS_CHANNEL_ID not set; transactions feature off.")
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
            logger.warning("Failed to send admin warning in Transactions: %s", exc)

    async def _state_tab(self) -> Worksheet:
        """The Transactions tab of the pick'em spreadsheet, created if missing."""
        return await asyncio.to_thread(
            get_or_create_tab, PICKEM_SHEET_NAME, STATE_TAB, ["last_posted_date"]
        )

    async def _load_last_date(self) -> int | None:
        """The saved date of the newest posted move, or None if there's none yet."""
        tab = await self._state_tab()
        values = await asyncio.to_thread(tab.get, "A2")
        raw = values[0][0] if values and values[0] else ""
        return int(raw) if raw else None

    async def _save_last_date(self, date: int) -> None:
        """Saves the date of the newest posted move."""
        tab = await self._state_tab()
        # As text: a 13-digit number might be shown (and read back) rounded.
        await asyncio.to_thread(tab.update, [[str(date)]], "A2")

    async def check_once(self) -> None:
        """Posts the moves made since the last check. Raises if ESPN, Google or
        Discord fails; the scheduler handles that."""
        channel_id = TRANSACTIONS_CHANNEL_ID
        if channel_id is None:
            return
        channel = get_text_channel(self.bot, channel_id)
        if channel is None:
            logger.warning("Transactions channel %s not found.", channel_id)
            return

        if self.last_date is None:
            self.last_date = await self._load_last_date()
        if self.league is None:
            league = await asyncio.to_thread(get_league)
            self.league = league
        else:
            league = self.league
        activities = await asyncio.to_thread(
            league.recent_activity, ACTIVITY_FETCH_SIZE
        )

        if self.last_date is None:
            # First start: take the newest move as the starting point, rather
            # than flooding the channel with the last 25 moves.
            self.last_date = max((a.date for a in activities), default=0)
            await self._save_last_date(self.last_date)
            logger.info("First transactions check: earlier moves not posted.")
            return

        start_date = self.last_date
        try:
            for activity in new_activities(activities, start_date):
                message = format_activity(activity)
                if message:
                    await channel.send(message)
                self.last_date = activity.date
        finally:
            # Save whatever was posted, even if a later send failed, so a
            # restart doesn't post those moves twice.
            if self.last_date != start_date:
                await self._save_last_date(self.last_date)

    async def _run_check(self) -> int:
        """Runs one check, tells the admin when it starts and stops failing.

        Returns the seconds to wait before the next check.
        """
        try:
            await self.check_once()
        except Exception as exc:
            # Broad on purpose: this is the scheduler loop's boundary, and one
            # failed check (ESPN, Google or Discord down) must not end it.
            logger.error("Transactions check failed: %s", exc)
            self.league = None  # fetched fresh next time, in case it's stale
            if not self._failing:
                self._failing = True
                await self._notify_admin(
                    f"[transactions] Couldn't check for new moves: {exc}. "
                    "Retrying every 10 minutes; you'll be told when it works again."
                )
            return ERROR_BACKOFF_SECONDS

        if self._failing:
            self._failing = False
            await self._notify_admin(
                "[transactions] Checking for new moves works again."
            )
        return CHECK_SECONDS

    async def scheduler(self) -> None:
        """Checks for new moves every CHECK_SECONDS. Runs forever."""
        await self.bot.wait_until_ready()
        while True:
            await asyncio.sleep(await self._run_check())


async def setup(bot: commands.Bot) -> None:
    """Loads the cog (called by discord.py's load_extension)."""
    await bot.add_cog(Transactions(bot))
