"""Weekly pick'em game.

Posts each week's NFL games, reads picks from reactions, exports them to
Google Sheets, scores them, and sends reminders before kickoff.
"""

import asyncio
from datetime import datetime, timedelta, date
import logging
import re
from types import SimpleNamespace
from typing import Any, Callable, Protocol, TypeVar, cast
import aiohttp
from aiohttp import ClientTimeout
import pytz
import discord
import gspread.exceptions
from gspread.cell import Cell
from gspread.utils import rowcol_to_a1
from gspread.worksheet import Worksheet
import requests
from espn_api.requests.espn_requests import (
    ESPNAccessDenied,
    ESPNInvalidLeague,
    ESPNUnknownError,
)
from discord.ext import commands
from gspread.exceptions import WorksheetNotFound

from core.utils.espn_helpers import get_league
from core.utils.quiet_hours import clamp_to_quiet_hours
from core.errors import (
    APIFetchError,
    NoEventsFoundError,
    PickemError,
    ExportError,
    ResultsError,
    SheetsError,
)
from core.decorators import admin_only
from data.teams import (
    teams,
    team_location,
    get_team_emoji_by_name,
    get_draw_emoji,
    get_emoji_to_team_short_map,
)
from data.channel_ids import ADMIN_CHANNEL_ID, REMINDER_CHANNEL_ID, GAME_CHANNEL_ID
from cogs.sheets import get_sheet, green_format, red_format, yellow_format
from data.config import (
    PICKEM_SHEET_NAME,
    LEAGUE_TIMEZONE,
    WEEKLY_POINTS_SHEET_LABEL,
    SEASON_TOTAL_SHEET_LABEL,
    DRAW_SHEET_LABEL,
)
from data.messages import (
    GAMES_POSTED_MESSAGE,
    WEEKLY_GAMES_POSTED_MESSAGE,
    THURSDAY_GAME_REMINDER_MESSAGE,
    SUNDAY_GAME_REMINDER_MESSAGE,
    PICK_INSTRUCTIONS_MESSAGE,
    EXPORT_SUCCESS_MESSAGE,
    EXPORT_NO_DATA_MESSAGE,
    CURRENT_WEEK_LABEL,
    RESULTS_HEADER_TEMPLATE,
    SEASON_TOTAL_LABEL,
    RESULTS_UPDATED_MESSAGE,
)

logger = logging.getLogger(__name__)

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
PROCESS_WEEKDAY = 1  # Tuesday (Monday=0)
PROCESS_HOUR = 20  # 20:00 league time
# The Super Bowl in the bot's week count (playoff weeks follow 18: 19 to 23).
SUPER_BOWL_WEEK = 18 + 5

# NFL games are scheduled in US Eastern time. A game's local weekday can
# differ (Thursday Night Football is Friday morning in Oslo), so games are
# matched by their Eastern weekday. Fixed NFL fact, not configurable.
NFL_SCHEDULE_TIMEZONE = pytz.timezone("America/New_York")

# Reminders
#
# The scheduler starts checking the day before each game day, because in
# timezones behind US Eastern the reminder can fall early on the game day
# itself (TNF's is 15:15 Thursday in Los Angeles). Waking early is
# harmless: the check just sleeps until the real reminder time.
THURSDAY_WAKE_WEEKDAY = 2  # Wednesday
THURSDAY_WAKE_HOUR = 8
THURSDAY_REMINDER_BUFFER_MINUTES = 120
SUNDAY_WAKE_WEEKDAY = 5  # Saturday
SUNDAY_WAKE_HOUR = 8
SUNDAY_REMINDER_BUFFER_MINUTES = 60

# Timeouts and retries
SHEETS_API_TIMEOUT_SECONDS = 10
ESPN_API_TIMEOUT_SECONDS = 10
ESPN_RETRY_DELAY_SECONDS = 5
CHANNEL_LOOKUP_RETRY_SECONDS = 30

ERROR_BACKOFF_SECONDS = 300
AUTO_POST_RETRY_SECONDS = 3600

# Discord message history
MESSAGE_HISTORY_LOOKBACK_DAYS = 14
EVENT_HISTORY_CHECK_LIMIT = 500  # messages checked for already-posted games
EXPORT_MESSAGE_SCAN_LIMIT = 200
EXPORT_MESSAGE_GROUP_WINDOW_SECONDS = 7200  # messages closer than this are one batch


def parse_espn_date(datestr: str) -> datetime:
    """Parses an ESPN date like "2025-09-21T18:00Z" into an aware UTC datetime."""
    return datetime.fromisoformat(datestr.replace("Z", "+00:00"))


def is_pickable_game(ev: dict[str, Any]) -> bool:
    """Whether an ESPN event is a game between two of the 32 NFL teams.

    Filters out the Pro Bowl ("NFC at AFC"). An event whose teams can't be read
    is kept, so a changed ESPN format shows up as an error, not missing games.
    """
    try:
        competitors = ev["competitions"][0]["competitors"]
        names = [c["team"]["displayName"] for c in competitors]
    except (KeyError, IndexError, TypeError):
        return True
    return all(name in teams for name in names)


def strip_emoji(text: str) -> str:
    """Removes custom and Unicode emoji from a game message.

    Team names are plain ASCII, so every non-ASCII character is dropped.

    >>> strip_emoji("🔔 New England Patriots @ Buffalo Bills 🦬")
    'New England Patriots @ Buffalo Bills'
    """
    text = re.sub(r"<a?:\w+:\d+>", "", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return " ".join(text.split())


T = TypeVar("T")


async def sheets_call(
    error_cls: Callable[[str], PickemError], func: Callable[..., T], *args: Any
) -> T:
    """Runs a blocking Sheets call in a thread, with a timeout.

    Failures raise error_cls instead of returning, since the weekly processing
    treats a normal return as "this week is done".
    """
    name = getattr(func, "__name__", "call")
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(func, *args), timeout=SHEETS_API_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError as exc:
        raise error_cls(f"Timeout in Google Sheets {name}()") from exc
    except (
        gspread.exceptions.GSpreadException,
        requests.exceptions.RequestException,
    ) as exc:
        raise error_cls(f"Google Sheets error in {name}(): {exc}") from exc


FIRST_GAME_ROW = 3  # Row 1: manager names, row 2: Discord IDs


def find_week_rows(
    rows: list[list[str]], game_codes: set[str], season_total_label: str
) -> tuple[list[tuple[str, int]], int | None]:
    """Finds this week's game rows, and the season-total row above them.

    A week is a block of game rows, then a weekly points row and a season-total
    row. Only this week's block is used, so a rerun builds on last week's total
    and a rematch in an earlier week is left alone.

    Returns ([(game_code, row), ...], previous_total_row), 1-based. The list is
    empty if none of the games are in the sheet; previous_total_row is None for
    the first week.
    """

    def label(row_number: int) -> str:
        row = rows[row_number - 1]
        return row[0].strip() if row else ""

    game_rows = [
        i for i in range(FIRST_GAME_ROW, len(rows) + 1) if label(i) in game_codes
    ]
    if not game_rows:
        return [], None
    last_game_row = game_rows[-1]

    previous_total_row = next(
        (
            i
            for i in range(last_game_row - 1, FIRST_GAME_ROW - 1, -1)
            if label(i) == season_total_label
        ),
        None,
    )
    block_start = previous_total_row + 1 if previous_total_row else FIRST_GAME_ROW
    return [(label(i), i) for i in game_rows if i >= block_start], previous_total_row


class _SendableContext(Protocol):
    """What _export_impl and _results_impl need: a command Context, or the
    stand-in the scheduler builds to run them without a command."""

    @property
    def channel(self) -> discord.TextChannel: ...

    async def send(self, content: str) -> Any: ...


class Pickem(commands.Cog):
    """Weekly pick'em: posting games, export, scoring and reminders."""

    @staticmethod
    def is_valid_game_message(msg_content: str) -> bool:
        """Whether a message is a posted game ("Away @ Home") or a result line.

        Emoji are ignored; a mention makes it invalid.
        """
        clean_text = strip_emoji(msg_content)
        if re.search(r"<@|@everyone|@here", clean_text):
            return False
        return bool(
            re.match(r"^[A-Za-z0-9 .]+ @ [A-Za-z0-9 .]+$", clean_text)
            or re.match(r"^[A-Za-z0-9 .]+ - [A-Za-z0-9 .]+: \d+-\d+$", clean_text)
        )

    def __init__(self, bot: commands.Bot) -> None:
        """Starts the reminder and auto-post schedulers."""
        self.bot: commands.Bot = bot
        self.league_tz = pytz.timezone(LEAGUE_TIMEZONE)
        self.last_reminder_week: int | None = None
        self.last_reminder_sunday: date | None = None
        self.last_posted_week: int | None = None
        self.last_processed_week: int | None = None
        self.state_loaded: bool = False
        self._state_dirty: bool = False
        task = self.reminder_scheduler()
        self.reminder_task: asyncio.Task[None] = self.bot.loop.create_task(task)
        self.auto_post_task: asyncio.Task[None] = self.bot.loop.create_task(
            self.auto_post_scheduler()
        )

    def _get_text_channel(self, channel_id: int) -> discord.TextChannel | None:
        """The channel if it exists and is a text channel, else None."""
        channel = self.bot.get_channel(channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    def _admin_channel(self) -> discord.TextChannel | None:
        """The admin channel, or None."""

        return self._get_text_channel(ADMIN_CHANNEL_ID)

    async def _notify_admin(self, message: str) -> None:
        """Posts to the admin channel. A failed send is logged, never raised."""
        admin_channel = self._admin_channel()
        if not admin_channel:
            return
        try:
            await admin_channel.send(message)
        except Exception as exc:
            # Broad on purpose: a failed notification must never stop the caller.
            logger.warning("Failed to send admin warning: %s", exc)

    def get_managers(self, sheet: Worksheet) -> dict[str, int]:
        """{Discord ID: column index} from row 2 (column B is index 1)."""
        id_row = sheet.row_values(2)
        managers = {id_row[i]: i for i in range(1, len(id_row)) if id_row[i]}
        logger.debug("get_managers: %s", managers)
        return managers

    async def cog_unload(self) -> None:
        """Stops the background tasks."""
        self.reminder_task.cancel()
        self.auto_post_task.cancel()

    @commands.command(name="games", aliases=["kamper"])
    @admin_only()
    async def games(self, ctx: commands.Context, week: int | None = None) -> None:
        """Posts this week's NFL games (or a given week's) in the channel."""
        await self._games_impl(ctx, week)

    async def _games_impl(self, ctx: commands.Context, week: int | None = None) -> None:
        events = await self._fetch_week_events(week)
        for ev in events:
            comps = ev["competitions"][0]["competitors"]
            home = next(c for c in comps if c["homeAway"] == "home")
            away = next(c for c in comps if c["homeAway"] == "away")
            home_team = home["team"]["displayName"]
            away_team = away["team"]["displayName"]
            away_emoji = get_team_emoji_by_name(ctx.guild, away_team)
            home_emoji = get_team_emoji_by_name(ctx.guild, home_team)
            await ctx.send(f"{away_emoji} {away_team} @ {home_team} {home_emoji}")

        channel = ctx.bot.get_channel(REMINDER_CHANNEL_ID)
        if channel:
            await channel.send(GAMES_POSTED_MESSAGE.format(channel=GAME_CHANNEL_ID))

    async def _fetch_week_events(self, week: int | None) -> list[dict[str, Any]]:
        """A week's pickable NFL games from ESPN (current week if None), by kickoff.

        Weeks 19 and up are ESPN's playoff weeks 1 to 5. Raises NoEventsFoundError
        if there are none.
        """
        now = datetime.now()
        season = now.year if now.month >= 3 else now.year - 1
        url = SCOREBOARD_URL
        if week:
            # Playoffs, by ESPN week: 1 Wild Card, 2 Divisional, 3 Conference,
            # 4 Pro Bowl, 5 Super Bowl.
            if week > 18:
                playoff_week = week - 18
                url = (
                    f"{SCOREBOARD_URL}?dates={season}&seasontype=3"
                    f"&week={playoff_week}"
                )
            else:
                url = f"{SCOREBOARD_URL}?dates={season}&seasontype=2&week={week}"
        logger.debug("Fetching URL: %s", url)

        try:
            async with aiohttp.ClientSession(
                timeout=ClientTimeout(total=ESPN_API_TIMEOUT_SECONDS)
            ) as session:
                try:
                    async with session.get(url) as resp:
                        data = await resp.json()
                except asyncio.TimeoutError:
                    logger.warning(
                        "API timeout against ESPN, retrying in 5 seconds. URL=%s", url
                    )
                    await asyncio.sleep(ESPN_RETRY_DELAY_SECONDS)
                    async with session.get(url) as resp:
                        data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise APIFetchError(url, e) from e

        events = [ev for ev in data.get("events", []) if is_pickable_game(ev)]
        if not events:
            raise NoEventsFoundError(week)

        events.sort(key=lambda ev: parse_espn_date(ev.get("date")))
        return events

    async def _check_thursday_reminder(
        self, channel: discord.TextChannel, now: datetime, weekday: int
    ) -> bool:
        """Sends the Thursday reminder, timed from the actual TNF kickoff.

        The reminder goes out THURSDAY_REMINDER_BUFFER_MINUTES before
        kickoff, moved out of quiet hours if needed (in Oslo, 00:15 Friday
        becomes 22:00 Thursday). The game is found by its US Eastern
        weekday (see NFL_SCHEDULE_TIMEZONE), so this works in any timezone.

        Runs on Wednesday too: behind US Eastern, the reminder can fall
        early on Thursday itself.

        Returns True if this round was handled (slept, sent if due, slept
        until tomorrow). Returns False if it's not Wednesday or Thursday,
        there's no Thursday game, or the reminder time has passed; the
        scheduler then uses its normal wait.
        """

        if weekday not in (THURSDAY_WAKE_WEEKDAY, 3):
            return False

        thursday_events = await self._fetch_events_for_nfl_weekday(3)
        if thursday_events is None:
            # The fetch failed and has already backed off; try again next round.
            return True
        if not thursday_events:
            return False

        thursday_events.sort(key=lambda ev: ev.get("date"))
        thursday_game = parse_espn_date(thursday_events[0]["date"]).astimezone(
            self.league_tz
        )
        week_num = now.isocalendar()[1]
        natural_reminder_time = thursday_game - timedelta(
            minutes=THURSDAY_REMINDER_BUFFER_MINUTES
        )
        reminder_time = clamp_to_quiet_hours(natural_reminder_time)
        if now >= reminder_time:
            return False

        sleep_seconds = (reminder_time - now).total_seconds()
        await asyncio.sleep(sleep_seconds)

        if self.last_reminder_week is None or self.last_reminder_week != week_num:
            await channel.send(
                THURSDAY_GAME_REMINDER_MESSAGE.format(channel=GAME_CHANNEL_ID)
            )
            self.last_reminder_week = week_num
            logger.info(
                "Thursday reminder sent for week %s (%s)", week_num, reminder_time
            )

        tomorrow = reminder_time + timedelta(days=1)
        time_diff = (tomorrow - datetime.now(self.league_tz)).total_seconds()
        await asyncio.sleep(time_diff)
        return True

    async def _fetch_events_for_nfl_weekday(self, target_weekday: int) -> list | None:
        """This week's pickable games on a weekday in US Eastern time (Monday=0).

        Returns None if ESPN can't be reached, after a five minute backoff.
        """
        url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

        try:
            async with aiohttp.ClientSession(
                timeout=ClientTimeout(total=ESPN_API_TIMEOUT_SECONDS)
            ) as session:
                try:
                    async with session.get(url) as resp:
                        data = await resp.json()
                except asyncio.TimeoutError:
                    logger.warning(
                        "API timeout against ESPN, retrying in 5 seconds. URL=%s", url
                    )
                    await asyncio.sleep(ESPN_RETRY_DELAY_SECONDS)
                    async with session.get(url) as resp:
                        data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            logger.error(
                "Failed to fetch data from ESPN API: %s. Retrying in 5 min.", e
            )
            await asyncio.sleep(ERROR_BACKOFF_SECONDS)
            return None

        events = data.get("events", [])
        return [
            ev
            for ev in events
            if is_pickable_game(ev)
            and parse_espn_date(ev["date"]).astimezone(NFL_SCHEDULE_TIMEZONE).weekday()
            == target_weekday
        ]

    async def _check_sunday_reminder(
        self, channel: discord.TextChannel, now: datetime, weekday: int
    ) -> bool:
        """Sends the reminder before the first Sunday game.

        Works like _check_thursday_reminder, starting on Saturday.
        """
        if weekday not in (SUNDAY_WAKE_WEEKDAY, 6):
            return False

        sunday_events = await self._fetch_events_for_nfl_weekday(6)
        if sunday_events is None:
            # The fetch failed and has already backed off; try again next round.
            return True
        if not sunday_events:
            return False

        sunday_events.sort(key=lambda ev: ev.get("date"))
        first_sunday_game = parse_espn_date(sunday_events[0]["date"]).astimezone(
            self.league_tz
        )
        natural_reminder_time = first_sunday_game - timedelta(
            minutes=SUNDAY_REMINDER_BUFFER_MINUTES
        )
        reminder_time = clamp_to_quiet_hours(natural_reminder_time)

        if now >= reminder_time:
            return False

        sleep_seconds = (reminder_time - now).total_seconds()
        await asyncio.sleep(sleep_seconds)

        if self.last_reminder_sunday is None or (
            self.last_reminder_sunday != first_sunday_game.date()
        ):
            await channel.send(
                SUNDAY_GAME_REMINDER_MESSAGE.format(channel=GAME_CHANNEL_ID)
            )
            self.last_reminder_sunday = first_sunday_game.date()
            logger.info(
                "Sunday reminder sent for %s (%s)",
                first_sunday_game.date(),
                reminder_time,
            )

        tomorrow = reminder_time + timedelta(days=1)
        await asyncio.sleep((tomorrow - datetime.now(self.league_tz)).total_seconds())
        return True

    def _next_reminder_window_seconds(self, now: datetime, weekday: int) -> float:
        """Seconds until the next Wednesday or Saturday wake-up."""
        next_thursday = now + timedelta(days=(THURSDAY_WAKE_WEEKDAY - weekday) % 7)
        next_thursday = next_thursday.replace(
            hour=THURSDAY_WAKE_HOUR, minute=0, second=0, microsecond=0
        )
        if next_thursday <= now:
            next_thursday += timedelta(days=7)

        next_sunday = now + timedelta(days=(SUNDAY_WAKE_WEEKDAY - weekday) % 7)
        next_sunday = next_sunday.replace(
            hour=SUNDAY_WAKE_HOUR, minute=0, second=0, microsecond=0
        )
        if next_sunday <= now:
            next_sunday += timedelta(days=7)

        sleep_until = min(next_thursday, next_sunday)
        return (sleep_until - now).total_seconds()

    async def reminder_scheduler(self) -> None:
        """Sends the Thursday and Sunday game reminders. Runs forever.

        Errors are logged, and the loop retries after five minutes.
        """
        await self.bot.wait_until_ready()
        channel: discord.TextChannel | None = None
        while not isinstance(channel, discord.TextChannel):
            fetched = self.bot.get_channel(REMINDER_CHANNEL_ID)
            if isinstance(fetched, discord.TextChannel):
                channel = fetched
            else:
                logger.warning(
                    "Failed to find text channel with id %s, retrying in 30s",
                    REMINDER_CHANNEL_ID,
                )
                await asyncio.sleep(CHANNEL_LOOKUP_RETRY_SECONDS)

        while True:
            now = datetime.now(self.league_tz)
            weekday = now.weekday()

            try:
                if await self._check_thursday_reminder(channel, now, weekday):
                    continue
                if await self._check_sunday_reminder(channel, now, weekday):
                    continue

                sleep_seconds = self._next_reminder_window_seconds(now, weekday)
                await asyncio.sleep(sleep_seconds)

            except Exception as e:
                # Broad on purpose: the scheduler loop must never die.
                logger.exception(
                    "Error in reminder_scheduler: %s. Retrying in 5 min.", e
                )
                await self._notify_admin(
                    f"[pickem] Error in reminder_scheduler: {e}. Retrying in 5 min."
                )
                await asyncio.sleep(ERROR_BACKOFF_SECONDS)

    async def _get_state_sheet(self) -> Worksheet:
        """The State tab of the pick'em spreadsheet, created if missing."""
        base_sheet = await asyncio.to_thread(get_sheet, PICKEM_SHEET_NAME)
        spreadsheet = base_sheet.spreadsheet
        try:
            return await asyncio.to_thread(spreadsheet.worksheet, "State")
        except WorksheetNotFound:
            logger.info("Failed to find State sheet, creating new.")
            state_ws = await asyncio.to_thread(
                spreadsheet.add_worksheet, title="State", rows=2, cols=2
            )
            await asyncio.to_thread(
                state_ws.update, [["last_processed_week", "last_posted_week"]], "A1:B1"
            )
            return state_ws

    async def _load_state(self) -> None:
        """Loads processed and posted weeks from the State tab, so restarts don't repeat work."""
        try:
            state_ws = await self._get_state_sheet()
            values = await asyncio.to_thread(state_ws.get, "A2:B2")
        except (
            gspread.exceptions.GSpreadException,
            requests.exceptions.RequestException,
            SheetsError,  # e.g. sheet not found, or Google login failed
        ) as exc:
            logger.error("Failed to load state from sheet: %s", exc)
            await self._notify_admin(f"[pickem] Failed to load State sheet: {exc}")
            return  # stays unloaded; retried later

        row = values[0] if values else []
        lpw = row[0] if len(row) > 0 else ""
        lpost = row[1] if len(row) > 1 else ""

        try:
            self.last_processed_week = int(lpw) if lpw else None
            self.last_posted_week = int(lpost) if lpost else None
        except ValueError as exc:
            logger.error("Corrupt state data in sheet: %s (row=%s)", exc, row)
            await self._notify_admin(
                f"[pickem] State sheet contains invalid values: {row}"
            )
            return  # stays unloaded

        self.state_loaded = True
        logger.info(
            "State loaded: last_processed_week=%s, last_posted_week=%s",
            self.last_processed_week,
            self.last_posted_week,
        )

    async def _save_state(self) -> bool:
        """Writes state to the State tab. Returns whether it worked."""
        if not self.state_loaded:
            return False
        try:
            state_ws = await self._get_state_sheet()
            await asyncio.to_thread(
                state_ws.update,
                [
                    [
                        (
                            self.last_processed_week
                            if self.last_processed_week is not None
                            else ""
                        ),
                        (
                            self.last_posted_week
                            if self.last_posted_week is not None
                            else ""
                        ),
                    ]
                ],
                "A2:B2",
            )
        except (
            gspread.exceptions.GSpreadException,
            requests.exceptions.RequestException,
            SheetsError,  # e.g. sheet not found, or Google login failed
        ) as exc:
            logger.error("Failed to save state to sheet: %s", exc)
            self._state_dirty = True
            await self._notify_admin(f"[pickem] Failed to save State sheet: {exc}")
            return False

        self._state_dirty = False
        logger.info(
            "State saved: last_processed_week=%s, last_posted_week=%s",
            self.last_processed_week,
            self.last_posted_week,
        )
        return True

    async def _flush_pending_state(self) -> None:
        """Retries a failed state save, before the scheduler acts on that state."""
        if not self._state_dirty:
            return
        logger.info("Attempting to save previously failed state again.")
        saved = await self._save_state()
        if saved:
            logger.info("Previously failed state save succeeded now.")
        else:
            logger.warning("State save still failing. Will retry next round.")

    async def _get_nfl_current_week(self) -> int:
        """The current NFL week: 1 to 18, then the playoffs as 19 to 23.

        Falls back to the fantasy league's week if ESPN's scoreboard can't be read.
        """
        url = SCOREBOARD_URL
        try:
            async with aiohttp.ClientSession(
                timeout=ClientTimeout(total=ESPN_API_TIMEOUT_SECONDS)
            ) as session:
                async with session.get(url) as resp:
                    data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            logger.error("Failed to fetch NFL current_week: %s", e)
            league = await asyncio.to_thread(get_league)
            return league.current_week

        week_info = data.get("week", {})
        week_number = week_info.get("number", 1)

        # seasontype 3 is the playoffs
        season_info = data.get("season", {})
        season_type = season_info.get("type", 2)

        if season_type == 3:
            return 18 + week_number
        else:
            return week_number

    def _season_window(
        self, now: datetime
    ) -> tuple[bool, datetime | None, datetime | None]:
        """Returns (in_season, season_end, next_start).

        The season runs from the Wednesday after Labor Day to the Tuesday after the
        Super Bowl (second Sunday in February), in league time.
        """

        def season_start(year: int) -> datetime:
            first_sept = self.league_tz.localize(datetime(year, 9, 1))
            first_monday = first_sept + timedelta(days=(0 - first_sept.weekday()) % 7)
            start_day = first_monday + timedelta(days=5)  # the Saturday after Labor Day
            start_day = start_day - timedelta(days=3)  # back to the Wednesday
            return start_day.replace(hour=0, minute=0, second=0, microsecond=0)

        def season_end(year: int) -> datetime:
            feb_first = self.league_tz.localize(datetime(year + 1, 2, 1))
            first_sunday = feb_first + timedelta(days=(6 - feb_first.weekday()) % 7)
            second_sunday = first_sunday + timedelta(days=7)
            end_day = second_sunday + timedelta(
                days=2
            )  # the Tuesday after the Super Bowl
            return end_day.replace(hour=23, minute=59, second=59, microsecond=0)

        for start_dt, end_dt in (
            (season_start(now.year - 1), season_end(now.year - 1)),
            (season_start(now.year), season_end(now.year)),
        ):
            if start_dt <= now <= end_dt:
                return True, end_dt, None

        future_starts = [
            dt
            for dt in (season_start(now.year), season_start(now.year + 1))
            if dt > now
        ]
        next_start = min(future_starts) if future_starts else None
        return False, None, next_start

    def _should_process_previous_week(self, now: datetime, current_week: int) -> bool:
        """Whether last week is due for export and results (from Tuesday 20:00)."""
        previous_week = current_week - 1
        if self.last_processed_week == previous_week:
            return False

        # There's no week before week 1.
        if previous_week < 1:
            return False

        # From Tuesday 20:00, or any later day if it was missed.
        if now.weekday() < PROCESS_WEEKDAY:
            return False
        if now.weekday() == PROCESS_WEEKDAY:
            earliest = now.replace(hour=PROCESS_HOUR, minute=0, second=0, microsecond=0)
            return now >= earliest
        return True

    async def _super_bowl_needs_processing(self, now: datetime) -> bool:
        """Whether the Super Bowl is over and due to be exported and scored.

        Other weeks wait for ESPN to report the next week, but nothing comes after
        the Super Bowl.
        """
        if self.last_processed_week != SUPER_BOWL_WEEK - 1:
            return False
        if not self._should_process_previous_week(now, SUPER_BOWL_WEEK + 1):
            return False
        try:
            events = await self._fetch_week_events(SUPER_BOWL_WEEK)
        except (NoEventsFoundError, APIFetchError) as exc:
            logger.warning("Could not check whether the Super Bowl is over: %s", exc)
            return False
        return all(
            ev.get("status", {}).get("type", {}).get("completed") for ev in events
        )

    async def _process_previous_week(
        self, current_week: int, channel: discord.TextChannel | None
    ) -> bool:
        """Exports and scores last week. Returns False if it should be retried."""
        previous_week = current_week - 1
        if previous_week < 1:
            logger.debug("previous_week=%s < 1, skipping processing", previous_week)
            return True

        if self.last_processed_week == previous_week:
            logger.debug(
                "Week %s already processed (last_processed_week=%s). Skipping.",
                previous_week,
                self.last_processed_week,
            )
            return True

        if not isinstance(channel, discord.TextChannel):
            logger.warning(
                "Missing valid channel for processing week %s", previous_week
            )
            return False

        # The Pro Bowl week has nothing to pick. Mark it done, or it would block
        # every later week.
        try:
            await self._fetch_week_events(previous_week)
        except NoEventsFoundError:
            logger.info("Week %s had no games to pick, skipping", previous_week)
            self.last_processed_week = previous_week
            await self._save_state()
            return True
        except APIFetchError as exc:
            logger.warning("Could not check week %s's games: %s", previous_week, exc)
            return False

        ctx: _SendableContext = SimpleNamespace(
            channel=channel, send=channel.send, bot=self.bot
        )

        try:
            logger.info("Running export for week %s", previous_week)
            await self._export_impl(ctx, previous_week)
        except Exception as exc:
            # Broad on purpose: whatever fails in export, the scheduler loop must survive.
            logger.error("Failed export for week %s: %s", previous_week, exc)
            await self._notify_admin(
                f"[pickem] Failed export for week {previous_week}: {exc}"
            )
            return False

        try:
            logger.info("Running results for week %s", previous_week)
            await self._results_impl(ctx, previous_week)
        except Exception as exc:
            # Broad on purpose, as above.
            logger.error(
                "Failed to calculate results for week %s: %s", previous_week, exc
            )
            await self._notify_admin(
                f"[pickem] Failed to calculate results for week {previous_week}: {exc}"
            )
            return False

        self.last_processed_week = previous_week
        saved = await self._save_state()
        if not saved:
            logger.warning(
                "Week %s processed, but saving state failed."
                "Retrying save next round.",
                previous_week,
            )
        logger.info(
            "Successfully processed week %s. Updated last_processed_week to %s",
            previous_week,
            self.last_processed_week,
        )
        return True

    async def _start_from_current_week_if_first_run(self, current_week: int) -> None:
        """On a first start (empty State tab), treats earlier weeks as processed.

        Otherwise a bot started mid-season would keep trying to process a week
        it never posted, and never post anything.
        """
        if self.last_processed_week is not None:
            return
        self.last_processed_week = current_week - 1
        logger.info(
            "First start (empty State tab): starting from week %s, "
            "earlier weeks treated as already processed.",
            current_week,
        )
        await self._save_state()

    async def auto_post_scheduler(self) -> None:
        """Processes last week, then posts the new week's games. Runs forever."""
        await self.bot.wait_until_ready()
        game_channel = self._get_text_channel(GAME_CHANNEL_ID)
        reminder_channel = self._get_text_channel(REMINDER_CHANNEL_ID)

        while True:
            # Wait for the state to load, rather than assume nothing was done and
            # risk processing a week twice.
            if not self.state_loaded:
                await self._load_state()
                if not self.state_loaded:
                    logger.warning("State not yet loaded. Retrying in 5 min.")
                    await asyncio.sleep(ERROR_BACKOFF_SECONDS)
                    continue

                logger.info(
                    "Auto-post scheduler started. Current state: "
                    "last_processed_week=%s, last_posted_week=%s",
                    self.last_processed_week,
                    self.last_posted_week,
                )

            # Retry a failed state save before acting on the state.
            await self._flush_pending_state()

            try:
                now = datetime.now(self.league_tz)

                in_season, _season_end, next_start = self._season_window(now)
                if not in_season:
                    if next_start:
                        sleep_seconds = max(60, (next_start - now).total_seconds())
                        logger.info(
                            "Outside season. Sleeping until next season starts: %s",
                            next_start,
                        )
                        await asyncio.sleep(sleep_seconds)
                        continue
                    await asyncio.sleep(AUTO_POST_RETRY_SECONDS)
                    continue

                current_week = await self._get_nfl_current_week()
                logger.debug(
                    "Checking auto-post scheduler: current_week=%s, "
                    "last_processed_week=%s, last_posted_week=%s",
                    current_week,
                    self.last_processed_week,
                    self.last_posted_week,
                )
            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
                ValueError,
                TypeError,
                requests.exceptions.RequestException,
                ESPNAccessDenied,
                ESPNInvalidLeague,
                ESPNUnknownError,
            ) as exc:
                logger.error(
                    "Failed to fetch league info for autopost: %s. Retrying in 1 hour.",
                    exc,
                )
                await asyncio.sleep(AUTO_POST_RETRY_SECONDS)
                continue

            await self._start_from_current_week_if_first_run(current_week)

            if await self._super_bowl_needs_processing(now):

                if not await self._process_previous_week(
                    SUPER_BOWL_WEEK + 1, game_channel
                ):
                    logger.warning("Super Bowl processing failed. Retrying in 1 hour.")
                await asyncio.sleep(AUTO_POST_RETRY_SECONDS)
                continue

            # Last week first: export and results.
            if self._should_process_previous_week(now, current_week):
                logger.info(
                    "Processing triggered for week %s (current=%s). "
                    "Running export and results.",
                    current_week - 1,
                    current_week,
                )
                processing_ok = await self._process_previous_week(
                    current_week, game_channel
                )
                if not processing_ok:
                    logger.warning(
                        "Processing failed for week %s. Retrying in 1 hour.",
                        current_week - 1,
                    )
                    await asyncio.sleep(AUTO_POST_RETRY_SECONDS)
                    continue

            # Post the new week only after last week is processed.
            if current_week > 1 and self.last_processed_week != current_week - 1:
                logger.debug(
                    "Blocking post: current_week=%s but last_processed_week=%s (need %s). "
                    "Waiting for processing to complete.",
                    current_week,
                    self.last_processed_week,
                    current_week - 1,
                )
                await asyncio.sleep(AUTO_POST_RETRY_SECONDS)
                continue

            if self.last_posted_week == current_week:
                logger.debug(
                    "Week %s already posted (last_posted_week=%s). Waiting for next week.",
                    current_week,
                    self.last_posted_week,
                )
                await asyncio.sleep(AUTO_POST_RETRY_SECONDS)
                continue

            try:
                events = await self._fetch_week_events(current_week)
            except NoEventsFoundError:
                logger.info(
                    "No games found for week %s yet. Retrying in 1 hour.",
                    current_week,
                )
                await asyncio.sleep(AUTO_POST_RETRY_SECONDS)
                continue
            except APIFetchError as exc:
                logger.error(
                    "Error fetching games for week %s: %s. Retrying in 1 hour.",
                    current_week,
                    exc,
                )
                await asyncio.sleep(AUTO_POST_RETRY_SECONDS)
                continue

            if not events:
                await asyncio.sleep(AUTO_POST_RETRY_SECONDS)
                continue

            # Already posted, e.g. before a restart?
            if isinstance(game_channel, discord.TextChannel):
                try:
                    already = await self._events_posted_recently(events, game_channel)
                except discord.HTTPException as exc:
                    logger.warning("Failed to check history: %s", exc)
                    already = False
                if already:
                    logger.info(
                        "Week %s events already posted in history. "
                        "Updating state and skipping posting.",
                        current_week,
                    )
                    self.last_posted_week = current_week
                    saved = await self._save_state()
                    if not saved:
                        logger.warning(
                            "Week %s tagged as posted, but saving of state failed."
                            "Retrying save next round.",
                            current_week,
                        )
                    await asyncio.sleep(AUTO_POST_RETRY_SECONDS)
                    continue

            if isinstance(game_channel, discord.TextChannel):
                logger.info(
                    "Posting %d events for week %s to Discord",
                    len(events),
                    current_week,
                )
                for ev in events:
                    await game_channel.send(self._format_event(ev, game_channel.guild))
                await game_channel.send(PICK_INSTRUCTIONS_MESSAGE)
            if isinstance(reminder_channel, discord.TextChannel):
                await reminder_channel.send(
                    WEEKLY_GAMES_POSTED_MESSAGE.format(
                        week=current_week, channel=GAME_CHANNEL_ID
                    )
                )

            self.last_posted_week = current_week
            saved = await self._save_state()
            if not saved:
                logger.warning(
                    "Week %s posted, but saving of state failed."
                    "Retrying save next round.",
                    current_week,
                )
            logger.info(
                "Auto-posted games for week %s. Updated state: "
                "last_processed_week=%s, last_posted_week=%s",
                current_week,
                self.last_processed_week,
                self.last_posted_week,
            )
            await asyncio.sleep(AUTO_POST_RETRY_SECONDS)

    def _format_event(self, ev: dict[str, Any], guild: discord.Guild | None) -> str:
        """Formats an ESPN game as "emoji Away @ Home emoji" for the given server."""
        comps = ev["competitions"][0]["competitors"]
        home = next(c for c in comps if c["homeAway"] == "home")
        away = next(c for c in comps if c["homeAway"] == "away")
        home_team = home["team"]["displayName"]
        away_team = away["team"]["displayName"]

        return (
            f"{get_team_emoji_by_name(guild, away_team)} {away_team} @ "
            f"{home_team} {get_team_emoji_by_name(guild, home_team)}"
        )

    async def _events_posted_recently(
        self, events: list[dict[str, Any]], channel: discord.TextChannel
    ) -> bool:
        """Whether this week's games were already posted in the last 14 days.

        Also True if only some were found, to be safe against double posting.
        """
        if not events:
            return False
        two_weeks_ago = datetime.now(self.league_tz) - timedelta(
            days=MESSAGE_HISTORY_LOOKBACK_DAYS
        )
        needed = {self._format_event(ev, channel.guild) for ev in events}
        found: set[str] = set()

        async for msg in channel.history(
            limit=EVENT_HISTORY_CHECK_LIMIT, after=two_weeks_ago
        ):
            if msg.author != self.bot.user:
                continue
            content = msg.content.strip()
            if content in needed:
                found.add(content)
            if needed == found:
                logger.info(
                    "All games for current week already posted. Skipping posting."
                )
                return True

        if found:
            logger.warning(
                "Found %d of %d games posted. This indicates possible duplicates. "
                "Skipping posting just in case.",
                len(found),
                len(needed),
            )
            return True

        return False

    @commands.command(name="export", aliases=["eksporter"])
    @admin_only()
    async def export(self, ctx: commands.Context, week: int | None = None) -> None:
        """Exports the picks on the most recently posted games to the sheet."""

        # @admin_only() means a guild text channel in practice; mypy can't know that.
        await self._export_impl(cast(_SendableContext, ctx), week)

    async def _export_impl(
        self, ctx: _SendableContext, week: int | None = None
    ) -> None:
        sheet = await sheets_call(ExportError, get_sheet, PICKEM_SHEET_NAME)
        channel = ctx.channel

        managers = self.get_managers(sheet)
        num_managers = len(managers)

        league_tz = pytz.timezone(LEAGUE_TIMEZONE)
        now = datetime.now(league_tz)

        search_limit = now - timedelta(days=MESSAGE_HISTORY_LOOKBACK_DAYS)

        emoji_to_team_short = get_emoji_to_team_short_map(channel.guild)
        draw_emoji = get_draw_emoji(channel.guild)
        is_valid_game_message = Pickem.is_valid_game_message

        all_bot_messages = []
        async for msg in channel.history(
            limit=EXPORT_MESSAGE_SCAN_LIMIT, after=search_limit
        ):
            if msg.author == self.bot.user and is_valid_game_message(msg.content):
                all_bot_messages.append(msg)

        if not all_bot_messages:
            raise ExportError(f"No valid bot messages found since {search_limit}")

        all_bot_messages.sort(key=lambda m: m.created_at, reverse=True)

        # The newest batch: messages posted within 2 hours of each other.
        messages = [all_bot_messages[0]]

        for i in range(1, len(all_bot_messages)):
            current_msg = all_bot_messages[i]
            previous_msg = all_bot_messages[i - 1]

            time_diff = previous_msg.created_at - current_msg.created_at
            if time_diff.total_seconds() > EXPORT_MESSAGE_GROUP_WINDOW_SECONDS:
                break

            messages.append(current_msg)

        messages.sort(key=lambda m: m.created_at)

        if not messages:
            raise ExportError("No messages found from last posting")

        values = []
        for msg in messages:
            clean_text = strip_emoji(msg.content)
            comps = clean_text.split("@")
            if len(comps) == 2:
                away_team_full = comps[0].strip()
                home_team_full = comps[1].strip()
                away = team_location.get(away_team_full, away_team_full.split()[-1])
                home = team_location.get(home_team_full, home_team_full.split()[-1])
                game_code = f"{away}@{home}"
                playing: set[str] | None = {away, home}
            else:
                game_code = clean_text
                playing = None  # teams unknown, so accept any team

            row = [game_code] + [""] * num_managers
            for reaction in msg.reactions:
                emoji_str = str(reaction.emoji)
                if emoji_str == draw_emoji:
                    pick = DRAW_SHEET_LABEL
                else:
                    pick = emoji_to_team_short.get(emoji_str, "")
                    # Skip other emoji and teams not in this game, so they
                    # can't replace a real pick.
                    if not pick or (playing is not None and pick not in playing):
                        continue
                async for user in reaction.users():
                    discord_id = str(user.id)
                    if user != self.bot.user and discord_id in managers:
                        row[managers[discord_id]] = pick
            values.append(row)

        if not values:
            await ctx.send(EXPORT_NO_DATA_MESSAGE)
            return

        # Already in the sheet (a rerun or retry)? Update those rows: appending
        # them again would count every pick twice.
        all_rows = await sheets_call(ExportError, sheet.get_all_values)
        game_codes = {row[0] for row in values}
        existing, _ = find_week_rows(all_rows, game_codes, SEASON_TOTAL_SHEET_LABEL)
        if existing:
            existing_rows = dict(existing)
            if set(existing_rows) != game_codes:
                raise ExportError(
                    f"Only some of this week's games are already in the sheet "
                    f"({sorted(existing_rows)}). Fix the sheet by hand first."
                )

            target_rows = [existing_rows[row[0]] for row in values]
        else:
            last_data_row = max(
                (i for i, row in enumerate(all_rows, start=1) if row and row[0]),
                default=0,
            )
            # One blank row after the previous week, and never above the first game row.
            start_row = max(last_data_row + 2, FIRST_GAME_ROW)
            target_rows = list(range(start_row, start_row + len(values)))

        cells = [
            Cell(row_number, col, value)
            for row_number, row in zip(target_rows, values)
            for col, value in enumerate(row, start=1)
        ]
        await sheets_call(ExportError, sheet.update_cells, cells)
        await ctx.send(EXPORT_SUCCESS_MESSAGE)

    @commands.command(name="results", aliases=["resultater"])
    @admin_only()
    async def results(self, ctx: commands.Context, week: int | None = None) -> None:
        """Scores a week's picks and posts the standings."""
        logger.info("Command !results run for week=%s", week)
        # See export() about cast().
        await self._results_impl(cast(_SendableContext, ctx), week)

    async def _results_impl(
        self, ctx: _SendableContext, week: int | None = None
    ) -> None:
        sheet = await sheets_call(ResultsError, get_sheet, PICKEM_SHEET_NAME)

        logger.debug("Fetching sheet: %s", sheet.title)

        league_tz = pytz.timezone(LEAGUE_TIMEZONE)
        now = datetime.now(league_tz)
        season = now.year if now.month >= 3 else now.year - 1
        logger.debug("Season: %s", season)

        url = SCOREBOARD_URL
        if week:
            # Playoffs, by ESPN week: 1 Wild Card, 2 Divisional, 3 Conference,
            # 4 Pro Bowl, 5 Super Bowl.
            if week > 18:
                playoff_week = week - 18
                url = (
                    f"{SCOREBOARD_URL}?dates={season}&seasontype=3"
                    f"&week={playoff_week}"
                )
            else:
                url = f"{SCOREBOARD_URL}?dates={season}&seasontype=2&week={week}"
        logger.debug("Fetching URL: %s", url)

        try:
            async with aiohttp.ClientSession(
                timeout=ClientTimeout(total=ESPN_API_TIMEOUT_SECONDS)
            ) as session:
                try:
                    async with session.get(url) as resp:
                        data = await resp.json()
                except asyncio.TimeoutError:
                    logger.warning(
                        "API timeout against ESPN, retrying in 5 seconds. URL=%s", url
                    )
                    await asyncio.sleep(ESPN_RETRY_DELAY_SECONDS)
                    # Retry once
                    async with session.get(url) as resp:
                        data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise APIFetchError(url, e) from e

        events = data.get("events", [])
        logger.debug("Number of events fetched: %s", len(events))

        if not events:
            raise NoEventsFoundError(week)

        game_results = {}
        for ev in events:
            try:
                comps = ev["competitions"][0]["competitors"]
                home = next(c for c in comps if c["homeAway"] == "home")
                away = next(c for c in comps if c["homeAway"] == "away")
                home_team = team_location.get(
                    home["team"]["displayName"],
                    home["team"]["displayName"].split()[-1],
                )
                away_team = team_location.get(
                    away["team"]["displayName"],
                    away["team"]["displayName"].split()[-1],
                )
                game_code = f"{away_team}@{home_team}"

                home_score = int(home["score"])
                away_score = int(away["score"])
            except (KeyError, IndexError, StopIteration, TypeError, ValueError) as e:
                raise ResultsError(
                    "Error parsing game data for " f"{ev.get('id', 'unknown')}"
                ) from e

            if home_score > away_score:
                game_results[game_code] = home_team
            elif away_score > home_score:
                game_results[game_code] = away_team
            else:
                game_results[game_code] = DRAW_SHEET_LABEL
        logger.debug("Game results: %s", game_results)

        managers = self.get_managers(sheet)
        logger.debug("Managers found: %s", managers)
        num_managers = len(managers)

        all_rows = await sheets_call(ResultsError, sheet.get_all_values)
        week_rows, previous_total_row = find_week_rows(
            all_rows, set(game_results), SEASON_TOTAL_SHEET_LABEL
        )
        if not week_rows:
            # No safe row to write totals to: a guessed row could overwrite another week.
            raise ResultsError(
                f"None of this week's games were found in the sheet "
                f"(expected e.g. {next(iter(game_results))}). Run export first?"
            )

        sheet_games = [game_code for game_code, _ in week_rows]
        row_mapping = {idx: row for idx, (_, row) in enumerate(week_rows)}

        logger.debug("Games in sheet: %s", sheet_games)
        logger.debug("Row mapping: %s", row_mapping)

        week_total_row = max(row_mapping.values(), default=3) + 1
        season_total_row = week_total_row + 1
        logger.debug(
            "Week total row: %s, season total row: %s", week_total_row, season_total_row
        )

        green = green_format()
        red = red_format()
        yellow = yellow_format()

        week_points = [0] * num_managers

        # Manager columns start at B.
        manager_ids = list(managers.keys())
        start_col = 2
        end_col = start_col + num_managers - 1
        start_row = min(row_mapping.values(), default=3)
        end_row = max(row_mapping.values(), default=3)

        range_notation = (
            f"{rowcol_to_a1(start_row, start_col)}:{rowcol_to_a1(end_row, end_col)}"
        )
        game_cell_range = await sheets_call(ResultsError, sheet.range, range_notation)

        cell_map = {(cell.row, cell.col): cell for cell in game_cell_range}

        cell_updates = []
        format_updates = []

        for idx, game_code in enumerate(sheet_games):
            row_idx = row_mapping[idx]
            correct_winner = game_results.get(game_code)
            logger.debug(
                "Processing game %s (correct winner: %s)", game_code, correct_winner
            )

            for pidx, _ in enumerate(manager_ids):
                col_idx = start_col + pidx
                cell_obj = cell_map.get((row_idx, col_idx))
                if cell_obj is None:
                    continue
                cell_value = cell_obj.value

                logger.debug(
                    "Game_code=%s, correct_winner=%s, teams=%s",
                    game_code,
                    correct_winner,
                    [(v.get("short"), v.get("name")) for v in teams.values()],
                )

                correct_answers = (
                    [correct_winner]
                    if correct_winner == DRAW_SHEET_LABEL
                    else [
                        v["short"]
                        for v in teams.values()
                        if (
                            correct_winner
                            and v["short"].lower() == correct_winner.lower()
                        )
                    ]
                )

                if not cell_value:
                    fmt = yellow
                elif cell_value in correct_answers:
                    fmt = green
                    week_points[pidx] += 1
                else:
                    fmt = red

                new_value = cell_value if cell_value else ""
                if cell_obj.value != new_value:
                    cell_obj.value = new_value
                    cell_updates.append(cell_obj)
                format_updates.append((row_idx, col_idx, fmt))

        logger.info("Weekly points: %s", week_points)

        # A weekly points row below the games, then a season total row.
        week_label_cell = await sheets_call(ResultsError, sheet.cell, week_total_row, 1)
        week_label_cell.value = WEEKLY_POINTS_SHEET_LABEL
        cell_updates.append(week_label_cell)

        for pidx, _ in enumerate(manager_ids):
            col_idx = start_col + pidx
            cell_obj = await sheets_call(
                ResultsError, sheet.cell, week_total_row, col_idx
            )
            points = week_points[pidx]
            if str(cell_obj.value) != str(points):
                cell_obj.value = str(points)
                cell_updates.append(cell_obj)

        season_label_cell = await sheets_call(
            ResultsError, sheet.cell, week_total_row + 1, 1
        )
        season_label_cell.value = SEASON_TOTAL_SHEET_LABEL
        cell_updates.append(season_label_cell)

        for pidx, _ in enumerate(manager_ids):
            col_idx = start_col + pidx
            previous_total = 0
            if previous_total_row:
                val = all_rows[previous_total_row - 1][col_idx - 1]
                previous_total = int(val) if val and str(val).isdigit() else 0

            new_total = previous_total + week_points[pidx]
            cell_obj = await sheets_call(
                ResultsError, sheet.cell, week_total_row + 1, col_idx
            )
            if str(cell_obj.value) != str(new_total):
                cell_obj.value = str(new_total)
                cell_updates.append(cell_obj)

        if cell_updates:
            await sheets_call(ResultsError, sheet.update_cells, cell_updates)

        # Cell colours, in one request.
        try:
            sheet_id = sheet.id
        except AttributeError as e:
            raise ResultsError(f"Failed to fetch sheetId: {e}") from e

        # Called batch_requests so it doesn't shadow the requests module.
        batch_requests = []
        for row_idx, col_idx, fmt in format_updates:
            batch_requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": row_idx - 1,
                            "endRowIndex": row_idx,
                            "startColumnIndex": col_idx - 1,
                            "endColumnIndex": col_idx,
                        },
                        "cell": {"userEnteredFormat": fmt},
                        "fields": (
                            "userEnteredFormat.backgroundColor,"
                            "userEnteredFormat.textFormat"
                        ),
                    }
                }
            )

        if batch_requests:
            await sheets_call(
                ResultsError,
                sheet.spreadsheet.batch_update,
                {"requests": batch_requests},
            )

        logger.info("Finished updating sheet, sending Discord message")

        # Standings message
        header_row = await sheets_call(ResultsError, sheet.row_values, 1)
        header_row = header_row[1 : 1 + num_managers]

        discord_msg = []
        for idx, name in enumerate(header_row, start=2):
            week_p = week_points[idx - 2]
            season_cell = await sheets_call(
                ResultsError, sheet.cell, season_total_row, idx
            )
            season_p_cell = season_cell.value
            season_p = (
                int(season_p_cell)
                if season_p_cell and str(season_p_cell).isdigit()
                else 0
            )
            discord_msg.append((name, week_p, season_p))

        week_label = str(week) if week else CURRENT_WEEK_LABEL
        discord_msg.sort(key=lambda x: x[1], reverse=True)
        lines = [f"```{RESULTS_HEADER_TEMPLATE.format(week=week_label)}"]
        for i, (name, week_p, _) in enumerate(discord_msg, start=1):
            lines.append(f"{i}. {name:<10} {week_p}")

        lines.append("")
        lines.append(SEASON_TOTAL_LABEL)
        discord_msg.sort(key=lambda x: x[2], reverse=True)
        for i, (name, _, season_p) in enumerate(discord_msg, start=1):
            lines.append(f"{i}. {name:<10} {season_p}")

        lines.append("```")
        await ctx.send("\n".join(lines))
        await ctx.send(RESULTS_UPDATED_MESSAGE.format(week=week_label))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Pickem(bot))
