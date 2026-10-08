"""!ppr: the managers' PPR ranking, with changes since the last snapshot."""

import asyncio
import logging
import os
from typing import Any, Dict, List

import discord
import gspread
import gspread.exceptions
from gspread.utils import rowcol_to_a1, ValueRenderOption
from gspread.worksheet import Worksheet
import requests
from discord.ext import commands
from core.errors import PPRFetchError, PPRSnapshotError
from cogs.sheets import get_client, get_or_create_tab
from core.decorators import admin_only
from core.utils.espn_helpers import ESPN_ERRORS, get_league
from data.channel_ids import ADMIN_CHANNEL_ID
from data.config import (
    LEAGUE_SHEET_NAME,
    PPR_HISTORY_SHEET_NAME,
    PPR_MANAGERS,
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


def _display_name(tab_title: str) -> str:
    """The team name PPR_MANAGERS gives a tab (ignoring case and spaces), else the tab title."""
    by_normalized = {tab.strip().lower(): team for tab, team in PPR_MANAGERS.items()}
    return by_normalized.get(tab_title.strip().lower(), tab_title)


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
        # Opened on first use (see _spreadsheet()), so a Google hiccup while the
        # bot starts doesn't disable !ppr until the next restart.
        self.sheet: gspread.Spreadsheet | None = None
        # Finished seasons' rows by year, loaded from ESPN once.
        self.finished: dict[int, list[dict[str, Any]]] = {}

    async def _spreadsheet(self) -> gspread.Spreadsheet:
        """The league spreadsheet, opened on first use and reused after that.

        Logging in and opening both wait for Google, so they run in a thread.
        A failed open raises and is tried again on the next call.
        """
        if self.sheet is None:
            self.sheet = await asyncio.to_thread(
                lambda: get_client().open(LEAGUE_SHEET_NAME)
            )
            logger.info("PPR Cog: Connected to Google Sheets")
        return self.sheet

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

    async def _get_managers(self, season: str | None = None) -> List[Dict[str, Any]]:
        """Reads each manager tab's PPR for a season (ESPN_YEAR by default).

        The season is looked up in column A, its PPR read from column B.
        Returns [{"team": tab title, "ppr": float}, ...].
        """
        if season is None:
            season = os.getenv("ESPN_YEAR", "").strip()
            if not season:
                raise PPRFetchError(
                    "-", "?", "ESPN_YEAR is not set, can't tell which season to show"
                )
        if not PPR_MANAGERS:
            raise PPRFetchError(
                "-", season, "PPR_MANAGERS is not set, no manager tabs to read"
            )
        # Case and surrounding spaces don't matter when matching tab names.
        target_names_normalized = {name.strip().lower(): name for name in PPR_MANAGERS}

        managers = []
        logger.info("Fetching PPR data for season %s", season)
        spreadsheet = await self._spreadsheet()
        worksheets = await asyncio.to_thread(spreadsheet.worksheets)
        logger.info("Found sheet: %s", [ws.title for ws in worksheets])

        for ws in worksheets:
            ws_title_norm = ws.title.strip().lower()
            if ws_title_norm not in target_names_normalized:
                continue

            logger.debug("Processing sheet: %s", ws.title)
            try:
                rows = await asyncio.wait_for(
                    asyncio.to_thread(ws.get_all_values), timeout=10
                )
                target_row = None
                for i, row in enumerate(rows, start=1):
                    if row and row[0].strip() == season:
                        target_row = i
                        break

                if target_row:
                    try:
                        ppr_value = float(rows[target_row - 1][1])  # B = index 1
                        managers.append({"team": ws.title, "ppr": ppr_value})
                        logger.debug("PPR for %s: %s", ws.title, ppr_value)
                    except ValueError as e:
                        raise PPRFetchError(
                            ws.title,
                            season,
                            f"Invalid PPR value in row {target_row}: {str(e)}",
                        ) from e
                else:
                    raise PPRFetchError(
                        ws.title, season, f"Found no row for season {season}"
                    )

            except PPRFetchError:
                raise
            except asyncio.TimeoutError:
                raise PPRFetchError(
                    ws.title, season, "Timeout while reading sheet"
                ) from None
            except (
                gspread.exceptions.GSpreadException,
                requests.exceptions.RequestException,
            ) as e:
                raise PPRFetchError(
                    ws.title, season, f"Error while reading sheet: {str(e)}"
                ) from e

        logger.info("Fetched PPR data for %s managers", len(managers))
        return managers

    async def _save_snapshot(self, managers: List[Dict[str, Any]]) -> None:
        """Appends team, PPR and rank rows to the history tab, in the given order."""
        spreadsheet = await self._spreadsheet()
        try:
            history_ws = await asyncio.to_thread(
                spreadsheet.worksheet, PPR_HISTORY_SHEET_NAME
            )
            logger.debug("Found existing PPR history sheet")
        except gspread.exceptions.WorksheetNotFound:
            logger.info("Creating new PPR history sheet")
            history_ws = await asyncio.to_thread(
                spreadsheet.add_worksheet,
                title=PPR_HISTORY_SHEET_NAME,
                rows=1000,
                cols=10,
            )

        rows_to_add = []
        for rank, manager in enumerate(managers, start=1):
            display_name = _display_name(manager["team"])
            rows_to_add.append([display_name, manager["ppr"], rank])

        if not rows_to_add:
            logger.warning("No PPR data to save in snapshot")
            return

        try:
            all_rows_col_a = await asyncio.wait_for(
                asyncio.to_thread(history_ws.col_values, 1), timeout=10
            )
            start_row = len(all_rows_col_a) + 1
            num_rows = len(rows_to_add)
            num_cols = len(rows_to_add[0])

            end_row = start_row + num_rows - 1
            range_notation = f"A{start_row}:{rowcol_to_a1(end_row, num_cols)}"
            cell_range = await asyncio.wait_for(
                asyncio.to_thread(history_ws.range, range_notation), timeout=10
            )
            flat_values = [val for row in rows_to_add for val in row]

            for cell_obj, val in zip(cell_range, flat_values):
                cell_obj.value = val

            await asyncio.wait_for(
                asyncio.to_thread(history_ws.update_cells, cell_range), timeout=10
            )
            logger.info("Saved snapshot with %s PPR values", num_rows)

        except asyncio.TimeoutError as e:
            raise PPRSnapshotError("Timeout while saving PPR snapshot") from e
        except (
            gspread.exceptions.GSpreadException,
            requests.exceptions.RequestException,
        ) as e:
            raise PPRSnapshotError(f"Could not save PPR snapshot: {str(e)}") from e

    @commands.command(name="ppr")
    @admin_only()
    async def ppr(self, ctx: commands.Context) -> None:
        """Posts the PPR ranking with changes since the last snapshot, then saves a new one."""
        try:
            managers = await self._get_managers()
            managers_sorted = sorted(managers, key=lambda x: x["ppr"], reverse=True)
            spreadsheet = await self._spreadsheet()
            try:
                history_ws = await asyncio.to_thread(
                    spreadsheet.worksheet, PPR_HISTORY_SHEET_NAME
                )
                rows = await asyncio.wait_for(
                    asyncio.to_thread(history_ws.get_all_values), timeout=10
                )
                logger.debug("Fetched %s historical PPR values", len(rows))
            except (
                gspread.exceptions.WorksheetNotFound,
                gspread.exceptions.GSpreadException,
                requests.exceptions.RequestException,
                asyncio.TimeoutError,
            ) as e:
                logger.warning("Could not open PPR history: %s", e)
                rows = []
        except PPRFetchError as e:
            logger.exception("Error fetching PPR data: %s", str(e))
            await self._notify_admin(f"[ppr] Error fetching PPR data: {e}")
            raise

        last_snapshot = {}
        last_ranks = {}
        for row in rows:
            if len(row) < 3:
                continue
            team, ppr_str, rank_str = row
            try:
                ppr_val = float(ppr_str)
                rank_val = int(rank_str)
            except ValueError:
                logger.debug("Invalid row in history: %s", row)
                continue
            last_snapshot[team] = ppr_val
            last_ranks[team] = rank_val

        for rank, manager in enumerate(managers_sorted, start=1):
            team = _display_name(manager["team"])
            old_ppr = last_snapshot.get(team)
            old_rank = last_ranks.get(team)
            manager["rank"] = rank
            manager["diff"] = manager["ppr"] - old_ppr if old_ppr is not None else 0.0
            if old_rank is not None:
                if old_rank == rank:
                    manager["rank_change"] = "="
                elif old_rank > rank:
                    manager["rank_change"] = f"⇧{old_rank - rank}"
                else:
                    manager["rank_change"] = f"⇩{rank - old_rank}"
            else:
                manager["rank_change"] = "="

        msg_lines = []
        for manager in managers_sorted:
            diff_str = f"{manager['diff']:+.3f}"
            team_name = _display_name(manager["team"])
            line = (
                f"{manager['rank']}. {team_name}: {manager['ppr']:.3f} "
                f"({diff_str}) {manager['rank_change']}"
            )
            msg_lines.append(line)
            logger.debug(line)

        if not msg_lines:
            await ctx.send(PPR_NO_DATA_MESSAGE)
            return

        msg = "\n".join(msg_lines)
        await ctx.send(PPR_UPDATE_MESSAGE.format(rankings=msg))
        await self._save_snapshot(managers_sorted)
        logger.debug("Snapshot saved.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PPR(bot))
