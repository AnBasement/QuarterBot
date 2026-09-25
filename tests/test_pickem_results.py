"""!export and !results against FakeSheet, an in-memory worksheet.

Unlike a MagicMock it keeps what's written, so a test can run export and
then results on the same sheet and check the cells.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import discord
import gspread.exceptions
import pytest
import pytz
from gspread.cell import Cell
from gspread.utils import a1_to_rowcol

from core.errors import ExportError, ResultsError
from data.teams import get_draw_emoji, get_team_emoji_by_name
from tests.test_pickem import make_cog


class FakeSheet:
    """Minimal gspread Worksheet stand-in backed by a list of rows (1-based
    row/col in the API, like gspread)."""

    def __init__(self, rows: list[list[str]]):
        self.grid = [list(r) for r in rows]
        self.title = "Fake"
        self.id = 0
        self.spreadsheet = MagicMock()

    def _get(self, row: int, col: int) -> str:
        if row <= len(self.grid) and col <= len(self.grid[row - 1]):
            return self.grid[row - 1][col - 1]
        return ""

    def _set(self, row: int, col: int, value) -> None:
        while len(self.grid) < row:
            self.grid.append([])
        cells = self.grid[row - 1]
        while len(cells) < col:
            cells.append("")
        cells[col - 1] = value

    def row_values(self, row: int) -> list[str]:
        return list(self.grid[row - 1]) if row <= len(self.grid) else []

    def col_values(self, col: int) -> list[str]:
        values = [self._get(r, col) for r in range(1, len(self.grid) + 1)]
        while values and values[-1] == "":
            values.pop()
        return values

    def get_all_values(self) -> list[list[str]]:
        width = max((len(r) for r in self.grid), default=0)
        return [r + [""] * (width - len(r)) for r in self.grid]

    def cell(self, row: int, col: int) -> Cell:
        return Cell(row, col, self._get(row, col))

    def range(self, notation: str) -> list[Cell]:
        start, end = notation.split(":")
        r1, c1 = a1_to_rowcol(start)
        r2, c2 = a1_to_rowcol(end)
        return [
            Cell(r, c, self._get(r, c))
            for r in range(r1, r2 + 1)
            for c in range(c1, c2 + 1)
        ]

    def update_cells(self, cells: list[Cell]) -> None:
        for cell in cells:
            self._set(cell.row, cell.col, cell.value)


def espn_game(away: str, away_score: int, home: str, home_score: int) -> dict:
    return {
        "id": "1",
        "competitions": [
            {
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {"displayName": home},
                        "score": str(home_score),
                    },
                    {
                        "homeAway": "away",
                        "team": {"displayName": away},
                        "score": str(away_score),
                    },
                ]
            }
        ],
    }


def patch_espn(monkeypatch, events: list[dict]) -> None:
    class DummyResponse:
        async def json(self):
            return {"events": events}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

    class DummySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        def get(self, url, *args, **kwargs):
            return DummyResponse()

    monkeypatch.setattr(
        "cogs.pickem.aiohttp.ClientSession", lambda *a, **kw: DummySession()
    )


def make_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.channel.guild = None
    ctx.send = AsyncMock()
    return ctx


HEADER_ROWS = [["Name", "Alice", "Bob"], ["Discord ID", "111", "222"]]


@pytest.mark.asyncio
async def test_season_total_builds_on_rows_with_configured_label(monkeypatch):
    """Existing season-total rows with a custom label (e.g. Norwegian
    "Sesongpoeng") are still found and added to."""
    monkeypatch.setattr("cogs.pickem.SEASON_TOTAL_SHEET_LABEL", "Sesongpoeng")
    monkeypatch.setattr("cogs.pickem.WEEKLY_POINTS_SHEET_LABEL", "Ukespoeng")
    sheet = FakeSheet(
        HEADER_ROWS
        + [
            ["Bills@Jets", "Jets", "Bills"],
            ["Ukespoeng", "1", "0"],
            ["Sesongpoeng", "5", "3"],
            [],
            ["Patriots@Giants", "Giants", "Patriots"],
        ]
    )
    monkeypatch.setattr("cogs.pickem.get_sheet", lambda name: sheet)
    patch_espn(
        monkeypatch,
        [espn_game("New England Patriots", 17, "New York Giants", 24)],
    )

    await make_cog()._results_impl(make_ctx(), 2)

    assert sheet.row_values(8) == ["Ukespoeng", "1", "0"]
    assert sheet.row_values(9) == ["Sesongpoeng", "6", "3"]


@pytest.mark.asyncio
async def test_tie_pick_written_by_export_scores_in_results(monkeypatch):
    """A correct tie pick written by export scores a point in results."""
    sheet = FakeSheet(HEADER_ROWS)
    monkeypatch.setattr("cogs.pickem.get_sheet", lambda name: sheet)

    bot_user = MagicMock()
    alice = MagicMock(id=111)

    async def alice_only():
        yield alice

    draw_reaction = MagicMock(emoji=get_draw_emoji(None))
    draw_reaction.users = alice_only
    game_msg = MagicMock(
        content="New England Patriots @ New York Giants",
        author=bot_user,
        created_at=datetime.now(pytz.timezone("Europe/Oslo")),
        reactions=[draw_reaction],
    )

    async def history(*args, **kwargs):
        yield game_msg

    cog = make_cog()
    cog.bot.user = bot_user
    ctx = make_ctx()
    ctx.channel.history = history

    await cog._export_impl(ctx)
    patch_espn(
        monkeypatch,
        [espn_game("New England Patriots", 20, "New York Giants", 20)],
    )
    await cog._results_impl(ctx, 1)

    weekly_row = next(r for r in sheet.grid if r and r[0] == "Weekly points")
    assert weekly_row[1:3] == ["1", "0"]


@pytest.mark.asyncio
async def test_export_parses_messages_with_unicode_fallback_emoji(monkeypatch):
    """With Unicode emoji, export still produces the "Away@Home" code and maps
    the reaction to a team."""
    sheet = FakeSheet(HEADER_ROWS)
    monkeypatch.setattr("cogs.pickem.get_sheet", lambda name: sheet)

    away, home = "New England Patriots", "Buffalo Bills"
    bot_user = MagicMock()
    alice = MagicMock(id=111)

    async def alice_only():
        yield alice

    pick = MagicMock(emoji=get_team_emoji_by_name(None, home))
    pick.users = alice_only
    game_msg = MagicMock(
        content=(
            f"{get_team_emoji_by_name(None, away)} {away} @ "
            f"{home} {get_team_emoji_by_name(None, home)}"
        ),
        author=bot_user,
        created_at=datetime.now(pytz.timezone("Europe/Oslo")),
        reactions=[pick],
    )

    async def history(*args, **kwargs):
        yield game_msg

    cog = make_cog()
    cog.bot.user = bot_user
    ctx = make_ctx()
    ctx.channel.history = history

    await cog._export_impl(ctx)

    assert ["Patriots@Bills", "Bills", ""] in sheet.grid


def two_week_sheet() -> FakeSheet:
    """Week 1 fully scored, week 2 exported but not yet scored. Week 1 has
    the same "Patriots@Giants" game code as week 2 (a rematch)."""
    return FakeSheet(
        HEADER_ROWS
        + [
            ["Patriots@Giants", "Patriots", "Patriots"],
            ["Weekly points", "0", "0"],
            ["Season total", "5", "3"],
            [],
            ["Patriots@Giants", "Giants", "Patriots"],
        ]
    )


@pytest.mark.asyncio
async def test_rerunning_results_does_not_double_count(monkeypatch):
    """A second run for the same week (manual, or a retry) gives the same totals."""
    sheet = two_week_sheet()
    monkeypatch.setattr("cogs.pickem.get_sheet", lambda name: sheet)
    patch_espn(
        monkeypatch, [espn_game("New England Patriots", 17, "New York Giants", 24)]
    )

    cog = make_cog()
    await cog._results_impl(make_ctx(), 2)
    await cog._results_impl(make_ctx(), 2)

    assert sheet.row_values(8) == ["Weekly points", "1", "0"]
    assert sheet.row_values(9) == ["Season total", "6", "3"]


@pytest.mark.asyncio
async def test_rematch_in_an_earlier_week_is_left_alone(monkeypatch):
    """The same game code in an earlier, already-scored week
    must not be re-scored or counted towards this week's points."""
    sheet = two_week_sheet()
    monkeypatch.setattr("cogs.pickem.get_sheet", lambda name: sheet)
    patch_espn(
        monkeypatch, [espn_game("New England Patriots", 17, "New York Giants", 24)]
    )

    await make_cog()._results_impl(make_ctx(), 2)

    formatted_rows = {
        req["repeatCell"]["range"]["startRowIndex"] + 1
        for call in sheet.spreadsheet.batch_update.call_args_list
        for req in call.args[0]["requests"]
    }
    assert formatted_rows == {7}
    assert sheet.row_values(8) == ["Weekly points", "1", "0"]


@pytest.mark.asyncio
async def test_results_before_export_raises_instead_of_overwriting(monkeypatch):
    """With none of this week's games in the sheet there's no safe row for the
    totals: raise, and leave the sheet untouched."""
    sheet = two_week_sheet()
    before = [list(r) for r in sheet.grid]
    monkeypatch.setattr("cogs.pickem.get_sheet", lambda name: sheet)
    patch_espn(monkeypatch, [espn_game("Buffalo Bills", 10, "Miami Dolphins", 20)])

    with pytest.raises(ResultsError):
        await make_cog()._results_impl(make_ctx(), 3)

    assert sheet.grid == before


def export_ctx(cog, picks: dict[int, str]) -> MagicMock:
    """A ctx whose channel history holds one bot-posted game message
    (Patriots @ Giants), with one reaction per {discord_id: team_name}."""
    bot_user = MagicMock()
    cog.bot.user = bot_user
    reactions = []
    for discord_id, team in picks.items():

        async def users(discord_id=discord_id):
            yield MagicMock(id=discord_id)

        reaction = MagicMock(emoji=get_team_emoji_by_name(None, team))
        reaction.users = users
        reactions.append(reaction)
    game_msg = MagicMock(
        content="New England Patriots @ New York Giants",
        author=bot_user,
        created_at=datetime.now(pytz.timezone("Europe/Oslo")),
        reactions=reactions,
    )

    async def history(*args, **kwargs):
        yield game_msg

    ctx = make_ctx()
    ctx.channel.history = history
    return ctx


@pytest.mark.asyncio
async def test_rerunning_export_updates_rows_instead_of_appending(monkeypatch):
    """A second export of the same week updates its rows instead of appending
    them again."""
    sheet = FakeSheet(HEADER_ROWS)
    monkeypatch.setattr("cogs.pickem.get_sheet", lambda name: sheet)
    cog = make_cog()

    await cog._export_impl(export_ctx(cog, {111: "New York Giants"}))
    await cog._export_impl(
        export_ctx(cog, {111: "New York Giants", 222: "New England Patriots"})
    )

    game_rows = [r for r in sheet.grid if r and r[0] == "Patriots@Giants"]
    assert game_rows == [["Patriots@Giants", "Giants", "Patriots"]]


@pytest.mark.asyncio
async def test_export_on_fresh_sheet_never_overwrites_the_id_row(monkeypatch):
    """With A1 and A2 blank, export must not write over row 2 (the Discord IDs)."""
    sheet = FakeSheet([["", "Alice", "Bob"], ["", "111", "222"]])
    monkeypatch.setattr("cogs.pickem.get_sheet", lambda name: sheet)
    cog = make_cog()

    await cog._export_impl(export_ctx(cog, {111: "New York Giants"}))

    assert sheet.row_values(2) == ["", "111", "222"]
    assert sheet.row_values(3) == ["Patriots@Giants", "Giants", ""]


@pytest.mark.asyncio
async def test_export_sheets_error_raises_instead_of_returning(monkeypatch):
    """A Sheets error while writing raises ExportError instead of returning."""
    sheet = FakeSheet(HEADER_ROWS)
    sheet.update_cells = MagicMock(
        side_effect=gspread.exceptions.GSpreadException("quota exceeded")
    )
    monkeypatch.setattr("cogs.pickem.get_sheet", lambda name: sheet)
    cog = make_cog()

    with pytest.raises(ExportError):
        await cog._export_impl(export_ctx(cog, {111: "New York Giants"}))


@pytest.mark.asyncio
async def test_results_timeout_raises_instead_of_returning(monkeypatch):
    """Same for results: a timeout raises ResultsError."""
    sheet = two_week_sheet()
    monkeypatch.setattr("cogs.pickem.get_sheet", lambda name: sheet)
    monkeypatch.setattr("cogs.pickem.SHEETS_API_TIMEOUT_SECONDS", 0.01)
    patch_espn(
        monkeypatch, [espn_game("New England Patriots", 17, "New York Giants", 24)]
    )

    def slow_update(cells):
        import time

        time.sleep(0.2)

    sheet.update_cells = slow_update

    with pytest.raises(ResultsError):
        await make_cog()._results_impl(make_ctx(), 2)


@pytest.mark.asyncio
async def test_failed_week_is_not_marked_processed(monkeypatch):
    """End to end: a failed Sheets write means processing reports failure and
    leaves last_processed_week alone."""
    sheet = FakeSheet(HEADER_ROWS)
    sheet.update_cells = MagicMock(
        side_effect=gspread.exceptions.GSpreadException("quota exceeded")
    )
    monkeypatch.setattr("cogs.pickem.get_sheet", lambda name: sheet)
    cog = make_cog(last_processed_week=1, state_loaded=True)
    cog._notify_admin = AsyncMock()
    # Results would succeed; only export fails.
    cog._results_impl = AsyncMock()
    cog._fetch_week_events = AsyncMock(return_value=[{"id": "1"}])
    channel = MagicMock(spec=discord.TextChannel)
    channel.history = export_ctx(cog, {111: "New York Giants"}).channel.history
    channel.guild = None
    channel.send = AsyncMock()

    ok = await cog._process_previous_week(3, channel)

    assert ok is False
    assert cog.last_processed_week == 1


@pytest.mark.asyncio
async def test_results_work_past_column_z(monkeypatch):
    """Column 27 and up (AA, AB, ...) must work, for 26+ managers."""
    n = 30
    sheet = FakeSheet(
        [
            ["Name"] + [f"M{i}" for i in range(n)],
            ["Discord ID"] + [str(1000 + i) for i in range(n)],
            ["Patriots@Giants"] + ["Giants"] * n,
        ]
    )
    monkeypatch.setattr("cogs.pickem.get_sheet", lambda name: sheet)
    patch_espn(
        monkeypatch, [espn_game("New England Patriots", 17, "New York Giants", 24)]
    )

    await make_cog()._results_impl(make_ctx(), 1)

    assert sheet.row_values(4) == ["Weekly points"] + ["1"] * n


def reactions_ctx(cog, reactions: list[tuple[str, int]]) -> MagicMock:
    """Like export_ctx, but with raw (emoji, discord_id) reactions in the
    order Discord lists them, so a test can add non-team emoji."""
    bot_user = MagicMock()
    cog.bot.user = bot_user
    discord_reactions = []
    for emoji, discord_id in reactions:

        async def users(discord_id=discord_id):
            yield MagicMock(id=discord_id)

        reaction = MagicMock(emoji=emoji)
        reaction.users = users
        discord_reactions.append(reaction)
    game_msg = MagicMock(
        content="New England Patriots @ New York Giants",
        author=bot_user,
        created_at=datetime.now(pytz.timezone("Europe/Oslo")),
        reactions=discord_reactions,
    )

    async def history(*args, **kwargs):
        yield game_msg

    ctx = make_ctx()
    ctx.channel.history = history
    return ctx


@pytest.mark.asyncio
async def test_non_team_emoji_does_not_erase_a_pick(monkeypatch):
    """A joke emoji added after a real pick must not blank that pick."""
    sheet = FakeSheet(HEADER_ROWS)
    monkeypatch.setattr("cogs.pickem.get_sheet", lambda name: sheet)
    cog = make_cog()
    giants = get_team_emoji_by_name(None, "New York Giants")

    await cog._export_impl(reactions_ctx(cog, [(giants, 111), ("👍", 111)]))

    assert sheet.row_values(4) == ["Patriots@Giants", "Giants", ""]


@pytest.mark.asyncio
async def test_team_not_in_the_game_is_ignored(monkeypatch):
    """A reaction for a team that isn't playing in that game isn't a pick,
    and doesn't replace a real one."""
    sheet = FakeSheet(HEADER_ROWS)
    monkeypatch.setattr("cogs.pickem.get_sheet", lambda name: sheet)
    cog = make_cog()
    giants = get_team_emoji_by_name(None, "New York Giants")
    niners = get_team_emoji_by_name(None, "San Francisco 49ers")

    await cog._export_impl(
        reactions_ctx(cog, [(giants, 111), (niners, 111), (niners, 222)])
    )

    assert sheet.row_values(4) == ["Patriots@Giants", "Giants", ""]
