"""!ppr: the managers' PPR ranking, with changes since the last snapshot."""

import asyncio
import logging
from typing import Any

import discord
import gspread
import gspread.exceptions
from gspread.utils import ValueRenderOption
from gspread.worksheet import Worksheet
import requests
from discord.ext import commands
from core.errors import PPRFetchError, PPRSnapshotError
from cogs.sheets import get_or_create_tab
from core.decorators import admin_only
from core.utils.espn_helpers import ESPN_ERRORS, get_league
from data.channel_ids import ADMIN_CHANNEL_ID
from data.config import (
    PICKEM_SHEET_NAME,
)
from data.messages import PPR_NO_DATA_MESSAGE, PPR_UPDATE_MESSAGE

logger = logging.getLogger(__name__)

HISTORY_TAB = "PPR history"
HISTORY_HEADER = ["season", "owner", "team", "ppr", "rank"]
DATA_TAB = "PPR"
DATA_HEADER = [
    "season",
    "owner",
    "manager",
    "team",
    "games",
    "wins",
    "losses",
    "ties",
    "ppg",
    "high",
    "low",
    "raw",
    "ppr",
]
# What a Google Sheets call can raise when Google is the problem.
SHEETS_ERRORS = (
    gspread.exceptions.GSpreadException,
    requests.exceptions.RequestException,
)
PLAYED = {"W", "L", "T"}  # ESPN tags unplayed weeks as "U"


def owner_id(team: Any) -> str:
    """The team's ESPN account ID (its first owner). It stays the same across
    seasons and team names, so it's how a manager is followed over the years."""
    if team.owners:
        return str(team.owners[0].get("id", f"team-{team.team_id}"))
    return f"team-{team.team_id}"


def owner_name(team: Any) -> str:
    """The first owner's full name, for the data tab."""
    if not team.owners:
        return ""
    owner = team.owners[0]
    return f"{owner.get('firstName', '')} {owner.get('lastName', '')}".strip()


def season_stats(team: Any, regular_weeks: int) -> dict[str, Any] | None:
    """A team's regular-season numbers from the weeks played so far, with its
    raw PPR score. None if it hasn't played yet."""
    weeks = [
        (score, outcome)
        for score, outcome in zip(
            team.scores[:regular_weeks], team.outcomes[:regular_weeks]
        )
        if outcome in PLAYED
    ]
    if not weeks:
        return None
    scores = [score for score, _ in weeks]
    outcomes = [outcome for _, outcome in weeks]
    games = len(weeks)
    wins, losses, ties = outcomes.count("W"), outcomes.count("L"), outcomes.count("T")
    ppg = sum(scores) / games
    high, low = max(scores), min(scores)
    win_pct = (wins + ties / 2) / games
    raw = (ppg * 6 + (high + low) * 2 + win_pct * 200 * 2) / 10
    return {
        "games": games,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "ppg": ppg,
        "high": high,
        "low": low,
        "raw": raw,
    }


def season_rows(league: Any) -> list[dict[str, Any]]:
    """One row per team that has played this season, with its PPR: its raw
    score divided by the league's average raw score."""
    rows = []
    for team in league.teams:
        stats = season_stats(team, league.settings.reg_season_count)
        if stats is None:
            continue
        rows.append(
            {
                "season": league.year,
                "owner": owner_id(team),
                "manager": owner_name(team),
                "team": team.team_name,
                **stats,
            }
        )
    if not rows:
        return []
    average = sum(row["raw"] for row in rows) / len(rows)
    for row in rows:
        row["ppr"] = row["raw"] / average
    return rows


def rank_change(old_rank: int, new_rank: int) -> str:
    """The arrow shown next to a team: "=", "⇧2" (up two places) or "⇩1"."""
    if old_rank == new_rank:
        return "="
    if old_rank > new_rank:
        return f"⇧{old_rank - new_rank}"
    return f"⇩{new_rank - old_rank}"


class PPR(commands.Cog):
    """PPR ranking command and snapshot history."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Finished seasons' rows by year, loaded from ESPN once.
        self.finished: dict[int, list[dict[str, Any]]] = {}

    async def _notify_admin(self, message: str) -> None:
        """Posts to the admin channel. A failed send is logged, never raised."""
        admin_channel = self.bot.get_channel(ADMIN_CHANNEL_ID)
        if not isinstance(admin_channel, discord.TextChannel):
            return
        try:
            await admin_channel.send(message)
        except Exception as exc:
            logger.exception("Could not send an admin warning in PPR: %s", exc)

    async def _league(self, year: int | None = None) -> Any:
        """The ESPN league for a season (ESPN_YEAR by default). Raises
        PPRFetchError if ESPN or the settings are the problem."""
        try:
            return await asyncio.to_thread(get_league, year)
        except ESPN_ERRORS as exc:
            raise PPRFetchError(
                "-", str(year or "current"), f"Couldn't load the ESPN league: {exc}"
            ) from exc

    async def _seasons(
        self,
    ) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
        """The current league, this season's rows, and every finished season's
        rows (loaded from ESPN the first time, remembered after that)."""
        league = await self._league()
        for year in league.previousSeasons:
            if year not in self.finished:
                self.finished[year] = season_rows(await self._league(year))
        finished = [
            row for year in sorted(self.finished) for row in self.finished[year]
        ]
        return league, season_rows(league), finished

    async def _history_tab(self) -> Worksheet:
        """The PPR history tab in the bot's spreadsheet, created if missing."""
        return await asyncio.to_thread(
            get_or_create_tab, PICKEM_SHEET_NAME, HISTORY_TAB, HISTORY_HEADER
        )

    async def _save_history(self, season: int, ranked: list[dict[str, Any]]) -> None:
        """Adds one row per manager (season, owner, team, PPR, rank) below the
        existing ones. `ranked` is this season's rows, best first."""
        tab = await self._history_tab()
        rows = [
            [season, row["owner"], row["team"], round(row["ppr"], 3), rank]
            for rank, row in enumerate(ranked, start=1)
        ]
        await asyncio.to_thread(tab.append_rows, rows)

    async def _last_history(self, season: int) -> dict[str, tuple[float, int]]:
        """Each manager's PPR and rank from the latest saved ranking of
        `season`, by owner ID. Empty if there's none yet."""
        tab = await self._history_tab()
        rows = await asyncio.to_thread(
            tab.get_all_values, value_render_option=ValueRenderOption.unformatted
        )
        last: dict[str, tuple[float, int]] = {}
        for row in rows[1:]:
            if str(row[0]) != str(season):
                continue
            try:
                last[row[1]] = (float(row[3]), int(row[4]))
            except (ValueError, IndexError):
                continue
        return last

    async def _export(self, rows: list[dict[str, Any]]) -> None:
        """Rewrites the PPR data tab with `rows`: every season's rows, one per
        manager per season, in DATA_HEADER's column order."""
        tab = await asyncio.to_thread(
            get_or_create_tab, PICKEM_SHEET_NAME, DATA_TAB, DATA_HEADER
        )
        values = [DATA_HEADER] + [[row[key] for key in DATA_HEADER] for row in rows]
        await asyncio.to_thread(tab.resize, rows=len(values), cols=len(DATA_HEADER))
        await asyncio.to_thread(tab.update, values, "A1")

    @commands.command(name="ppr")
    @admin_only()
    async def ppr(self, ctx: commands.Context) -> None:
        """Posts this season's PPR ranking, with changes since the last run, then
        saves a snapshot and the full data table to the bot's spreadsheet."""
        try:
            _, current, finished = await self._seasons()
        except PPRFetchError as exc:
            await self._notify_admin(f"[ppr] Couldn't calculate PPR: {exc}")
            raise
        if not current:
            await ctx.send(PPR_NO_DATA_MESSAGE)
            return

        season = current[0]["season"]
        ranked = sorted(current, key=lambda row: row["ppr"], reverse=True)
        try:
            last = await self._last_history(season)
        except SHEETS_ERRORS as exc:
            logger.warning("Could not read the PPR history: %s", exc)
            last = {}  # post without arrows rather than not at all

        lines = []
        for rank, row in enumerate(ranked, start=1):
            old = last.get(row["owner"])
            diff = row["ppr"] - old[0] if old else 0.0
            change = rank_change(old[1], rank) if old else "="
            lines.append(
                f"{rank}. {row['team']}: {row['ppr']:.3f} ({diff:+.3f}) {change}"
            )
        await ctx.send(PPR_UPDATE_MESSAGE.format(rankings="\n".join(lines)))

        try:
            await self._save_history(season, ranked)
            await self._export(finished + current)
        except SHEETS_ERRORS as exc:
            raise PPRSnapshotError(
                f"Could not save PPR to the spreadsheet: {exc}"
            ) from exc


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PPR(bot))
