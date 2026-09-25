"""!ppr: the managers' PPR ranking, with changes since the last snapshot."""

import asyncio
import logging
import os
from typing import Any, Dict, List

import discord
import gspread
import gspread.exceptions
from gspread.utils import rowcol_to_a1
import requests
from discord.ext import commands
from core.errors import (
    PPRFetchError,
    PPRSnapshotError,
    MissingCredentialsError,
    ClientAuthorizationError,
)
from cogs.sheets import get_client
from core.decorators import admin_only
from data.channel_ids import ADMIN_CHANNEL_ID
from data.config import (
    LEAGUE_SHEET_NAME,
    PPR_HISTORY_SHEET_NAME,
    PPR_MANAGERS,
)
from data.messages import PPR_NO_DATA_MESSAGE, PPR_UPDATE_MESSAGE

logger = logging.getLogger(__name__)


def _display_name(tab_title: str) -> str:
    """The team name PPR_MANAGERS gives a tab (ignoring case and spaces), else the tab title."""
    by_normalized = {tab.strip().lower(): team for tab, team in PPR_MANAGERS.items()}
    return by_normalized.get(tab_title.strip().lower(), tab_title)


class PPR(commands.Cog):
    """PPR ranking command and snapshot history."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        try:
            self.sheet = get_client().open(LEAGUE_SHEET_NAME)
            logger.info("PPR Cog: Connected to Google Sheets")
        except (
            MissingCredentialsError,
            ClientAuthorizationError,
            gspread.exceptions.GSpreadException,
            requests.exceptions.RequestException,
        ) as e:
            logger.error("PPR Cog: Could not connect to Google Sheets: %s", e)
            asyncio.get_running_loop().create_task(
                self._notify_admin(
                    f"[ppr] PPR Cog: Could not connect to Google Sheets: {e}"
                )
            )
            raise

    async def _notify_admin(self, message: str) -> None:
        """Posts to the admin channel. A failed send is logged, never raised."""
        admin_channel = self.bot.get_channel(ADMIN_CHANNEL_ID)
        if not isinstance(admin_channel, discord.TextChannel):
            return
        try:
            await admin_channel.send(message)
        except Exception as exc:
            logger.exception("Could not send an admin warning in PPR: %s", exc)

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
        ws_titles = [ws.title for ws in self.sheet.worksheets()]
        logger.info("Found sheet: %s", ws_titles)

        for ws in self.sheet.worksheets():
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
        try:
            history_ws = self.sheet.worksheet(PPR_HISTORY_SHEET_NAME)
            logger.debug("Found existing PPR history sheet")
        except gspread.exceptions.WorksheetNotFound:
            logger.info("Creating new PPR history sheet")
            history_ws = self.sheet.add_worksheet(
                title=PPR_HISTORY_SHEET_NAME, rows=1000, cols=10
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
            try:
                history_ws = self.sheet.worksheet(PPR_HISTORY_SHEET_NAME)
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
