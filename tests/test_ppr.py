"""Tests for ppr.py"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import requests
from cogs.ppr import (
    PPR,
    owner_id,
    owner_name,
    rank_change,
    season_rows,
    season_stats,
)
from core.errors import ClientAuthorizationError, PPRFetchError
from data.config import parse_ppr_managers

DUMMY_MANAGERS = [
    {"team": "Alice", "ppr": 10.0},
    {"team": "Bob", "ppr": 8.5},
    {"team": "Carol", "ppr": 9.2},
]

# Same format _save_snapshot() writes: display name (from PPR_MANAGERS), PPR, rank.
DUMMY_HISTORY = [
    ["Aces", "9.5", "2"],
    ["Bombers", "8.0", "3"],
    ["Comets", "9.0", "1"],
]

DUMMY_PPR_MANAGERS = {
    "Alice": "Aces",
    "Bob": "Bombers",
    "Carol": "Comets",
}


@pytest.fixture(name="ppr_cog")
@patch("cogs.ppr.get_client")
def fixture_ppr_cog(mock_get_client):
    """Create a PPR cog with mocked Google Sheets client."""
    mock_bot = MagicMock()
    mock_sheet = MagicMock()
    dummy_client = MagicMock()
    dummy_client.open.return_value = mock_sheet
    mock_get_client.return_value = dummy_client

    ppr_cog = PPR(mock_bot)
    ppr_cog.sheet = mock_sheet
    return ppr_cog


@pytest.mark.asyncio
async def test_save_snapshot(monkeypatch, ppr_cog):
    monkeypatch.setattr("cogs.ppr.PPR_MANAGERS", DUMMY_PPR_MANAGERS)
    ws_mock = MagicMock()
    ws_mock.col_values.return_value = [""]  # empty column
    ws_mock.range.return_value = [MagicMock() for _ in range(len(DUMMY_MANAGERS) * 3)]
    ppr_cog.sheet.worksheet.return_value = ws_mock

    await ppr_cog._save_snapshot(DUMMY_MANAGERS)  # pylint: disable=protected-access
    ws_mock.update_cells.assert_called_once()


@pytest.mark.asyncio
async def test_ppr_command_logic(monkeypatch, ppr_cog):
    """Ranks by PPR, with the change and rank movement since the last snapshot."""
    monkeypatch.setattr(
        ppr_cog, "_get_managers", AsyncMock(return_value=DUMMY_MANAGERS)
    )
    monkeypatch.setattr("cogs.ppr.PPR_MANAGERS", DUMMY_PPR_MANAGERS)
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ws_mock = MagicMock()
    ws_mock.get_all_values.return_value = DUMMY_HISTORY
    ppr_cog.sheet.worksheet.return_value = ws_mock

    await ppr_cog.ppr.callback(ppr_cog, ctx)

    ctx.send.assert_called_once()
    sent_msg = ctx.send.call_args[0][0]
    # History rows are matched by display name.
    assert "1. Aces: 10.000 (+0.500) ⇧1" in sent_msg
    assert "2. Comets: 9.200 (+0.200) ⇩1" in sent_msg
    assert "3. Bombers: 8.500 (+0.500) =" in sent_msg


@pytest.mark.asyncio
async def test_get_managers_reads_the_espn_year_row(monkeypatch):
    """The season row comes from ESPN_YEAR."""
    monkeypatch.setenv("ESPN_YEAR", "2027")
    monkeypatch.setattr("cogs.ppr.PPR_MANAGERS", {"Alice": "Aces"})
    ws = MagicMock()
    ws.title = "Alice"
    ws.get_all_values.return_value = [
        ["2025", "1.1"],
        ["2026", "1.2"],
        ["2027", "1.3"],
    ]
    cog = PPR.__new__(PPR)
    cog.sheet = MagicMock()
    cog.sheet.worksheets.return_value = [ws]

    managers = await cog._get_managers()  # pylint: disable=protected-access

    assert managers == [{"team": "Alice", "ppr": 1.3}]
    cog.sheet.worksheets.assert_called_once()  # one trip to Google, not two


def test_ppr_admin_check_tolerates_spaces_in_admin_ids(monkeypatch):
    """Spaces in ADMIN_IDS ("111, 222") don't lock anyone out of !ppr."""
    import importlib
    import cogs.ppr

    monkeypatch.setenv("ADMIN_IDS", "111, 222")
    ppr_module = importlib.reload(cogs.ppr)  # checks read ADMIN_IDS at import
    ctx = MagicMock()
    ctx.author.id = 222

    assert all(check(ctx) for check in ppr_module.PPR.ppr.checks)


@pytest.mark.asyncio
async def test_snapshot_uses_configured_history_tab(monkeypatch):
    """Snapshots go to the PPR_HISTORY_SHEET_NAME tab."""
    monkeypatch.setattr("cogs.ppr.PPR_HISTORY_SHEET_NAME", "My PPR Tab")
    cog = PPR.__new__(PPR)
    cog.sheet = MagicMock()
    history_ws = cog.sheet.worksheet.return_value
    history_ws.col_values.return_value = []
    history_ws.range.return_value = [MagicMock(), MagicMock(), MagicMock()]

    await cog._save_snapshot(  # pylint: disable=protected-access
        [{"team": "Alice", "ppr": 1.2}]
    )

    cog.sheet.worksheet.assert_called_with("My PPR Tab")


class TestParsePprManagers:
    def test_tabs_with_team_names(self):
        assert parse_ppr_managers("Alice=Aces; Bob = Bombers") == {
            "Alice": "Aces",
            "Bob": "Bombers",
        }

    def test_team_name_is_optional(self):
        assert parse_ppr_managers("Alice;Bob=Bombers") == {
            "Alice": "Alice",
            "Bob": "Bombers",
        }

    def test_commas_stay_inside_team_names(self):
        assert parse_ppr_managers("Alice=Aces, Inc.") == {"Alice": "Aces, Inc."}

    def test_empty_and_stray_separators(self):
        assert parse_ppr_managers("") == {}
        assert parse_ppr_managers(";Alice=Aces;;") == {"Alice": "Aces"}


@pytest.mark.asyncio
async def test_ppr_reports_missing_config_instead_of_guessing(monkeypatch):
    """Without PPR_MANAGERS, !ppr says so instead of showing nothing."""
    monkeypatch.setenv("ESPN_YEAR", "2026")
    monkeypatch.setattr("cogs.ppr.PPR_MANAGERS", {})
    cog = PPR.__new__(PPR)
    cog.sheet = MagicMock()

    with pytest.raises(PPRFetchError, match="PPR_MANAGERS is not set"):
        await cog._get_managers()  # pylint: disable=protected-access


def test_cog_loads_while_google_is_unreachable():
    """A Google hiccup while the bot starts must not disable !ppr until the
    next restart: the spreadsheet is only opened when it's needed."""
    with patch(
        "cogs.ppr.get_client", side_effect=ClientAuthorizationError("Google down")
    ):
        cog = PPR(MagicMock())

    assert cog.sheet is None


@pytest.mark.asyncio
async def test_spreadsheet_is_opened_on_first_use_and_reused():
    client = MagicMock()
    with patch("cogs.ppr.get_client", return_value=client):
        cog = PPR(MagicMock())
        first = await cog._spreadsheet()  # pylint: disable=protected-access
        second = await cog._spreadsheet()  # pylint: disable=protected-access

    assert first is second is client.open.return_value
    client.open.assert_called_once()


@pytest.mark.asyncio
async def test_failed_open_is_tried_again():
    client = MagicMock()
    client.open.side_effect = [ClientAuthorizationError("Google down"), "sheet"]
    with patch("cogs.ppr.get_client", return_value=client):
        cog = PPR(MagicMock())
        with pytest.raises(ClientAuthorizationError):
            await cog._spreadsheet()  # pylint: disable=protected-access
        sheet = await cog._spreadsheet()  # pylint: disable=protected-access

    assert sheet == "sheet"


# Calculating PPR from ESPN data


def team(scores, outcomes, team_id=1, name="Aces", owners=None):
    """A team shaped like espn_api's: week-by-week scores and results."""
    if owners is None:
        owners = [{"id": f"{{OWNER-{team_id}}}", "firstName": "Ann", "lastName": "Lee"}]
    return SimpleNamespace(
        scores=scores,
        outcomes=outcomes,
        team_id=team_id,
        team_name=name,
        owners=owners,
    )


def league(teams, regular_weeks=3, year=2025):
    return SimpleNamespace(
        teams=teams,
        year=year,
        settings=SimpleNamespace(reg_season_count=regular_weeks),
    )


def test_raw_score_matches_a_hand_calculation():
    # Points per game 100, highest 120 + lowest 80, won 2 of 3:
    # (100 * 6 + (120 + 80) * 2 + (2/3) * 200 * 2) / 10 = 126.667
    stats = season_stats(team([100, 120, 80], ["W", "L", "W"]), 3)

    assert stats is not None
    assert (stats["games"], stats["wins"], stats["losses"]) == (3, 2, 1)
    assert (stats["ppg"], stats["high"], stats["low"]) == (100, 120, 80)
    assert stats["raw"] == pytest.approx(126.6667, abs=1e-4)


def test_only_played_weeks_count():
    """ESPN lists future weeks as "U" with 0.0: counting them would make
    everyone's lowest game 0 in September."""
    stats = season_stats(team([100, 120, 0.0, 0.0], ["W", "L", "U", "U"]), 4)

    assert stats is not None
    assert stats["games"] == 2
    assert stats["low"] == 100


def test_playoff_weeks_are_left_out():
    """PPR is regular season only: week 4 here is a playoff week."""
    stats = season_stats(team([100, 120, 80, 200], ["W", "L", "W", "W"]), 3)

    assert stats is not None
    assert stats["games"] == 3
    assert stats["high"] == 120


def test_a_tie_counts_as_half_a_win():
    won_and_tied = season_stats(team([100, 100], ["W", "T"]), 2)
    won_one_of_two = season_stats(team([100, 100], ["W", "L"]), 2)

    assert won_and_tied is not None and won_one_of_two is not None
    # Win% 0.75 vs 0.5: the raw scores differ by 0.25 * 200 * 2 / 10 = 10.
    assert won_and_tied["raw"] - won_one_of_two["raw"] == pytest.approx(10)


def test_no_stats_before_the_first_game():
    assert season_stats(team([0.0, 0.0], ["U", "U"]), 2) is None


def test_ppr_is_raw_score_divided_by_the_league_average():
    good = team([150, 150, 150], ["W", "W", "W"], team_id=1, name="Good")
    bad = team([90, 90, 90], ["L", "L", "L"], team_id=2, name="Bad")

    rows = season_rows(league([good, bad]))

    by_team = {row["team"]: row for row in rows}
    average = (by_team["Good"]["raw"] + by_team["Bad"]["raw"]) / 2
    assert by_team["Good"]["ppr"] == pytest.approx(by_team["Good"]["raw"] / average)
    # Two teams: the league average sits exactly between them.
    assert by_team["Good"]["ppr"] + by_team["Bad"]["ppr"] == pytest.approx(2.0)


def test_season_rows_carry_the_season_owner_and_team():
    rows = season_rows(league([team([100, 100, 100], ["W", "L", "W"])], year=2021))

    assert rows[0]["season"] == 2021
    assert rows[0]["owner"] == "{OWNER-1}"
    assert rows[0]["manager"] == "Ann Lee"
    assert rows[0]["team"] == "Aces"
    assert rows[0]["ppr"] == pytest.approx(1.0)  # alone, so exactly average


def test_a_team_that_hasnt_played_is_left_out():
    played = team([100, 100, 100], ["W", "L", "W"], team_id=1)
    not_yet = team([0.0, 0.0, 0.0], ["U", "U", "U"], team_id=2)

    rows = season_rows(league([played, not_yet]))

    assert [row["owner"] for row in rows] == ["{OWNER-1}"]


def test_no_rows_before_anyone_has_played():
    assert season_rows(league([team([0.0], ["U"])], regular_weeks=1)) == []


def test_owner_id_falls_back_to_the_team_id():
    assert owner_id(team([], [], team_id=7, owners=[])) == "team-7"


def test_owner_name_is_first_and_last_name():
    assert owner_name(team([], [])) == "Ann Lee"
    assert owner_name(team([], [], owners=[])) == ""


@pytest.mark.parametrize(
    "old, new, arrow",
    [(3, 3, "="), (3, 1, "⇧2"), (1, 2, "⇩1")],
)
def test_rank_change(old, new, arrow):
    assert rank_change(old, new) == arrow


# Loading the seasons


def fake_espn(monkeypatch, current=2026, previous=(2024, 2025), error=None):
    """Replaces get_league with a fake ESPN league per season. Returns the list
    of seasons asked for (None means "the current season")."""
    asked: list[int | None] = []

    def get_league(year=None):
        asked.append(year)
        if error is not None:
            raise error
        season = current if year is None else year
        fake = league([team([100, 120, 80], ["W", "L", "W"])], year=season)
        fake.previousSeasons = list(previous) if year is None else []
        return fake

    monkeypatch.setattr("cogs.ppr.get_league", get_league)
    return asked


@pytest.mark.asyncio
async def test_seasons_returns_this_season_and_the_finished_ones(monkeypatch):
    fake_espn(monkeypatch, current=2026, previous=(2024, 2025))
    cog = PPR(MagicMock())

    league_now, current, finished = await cog._seasons()

    assert league_now.year == 2026
    assert [row["season"] for row in current] == [2026]
    assert [row["season"] for row in finished] == [2024, 2025]  # oldest first


@pytest.mark.asyncio
async def test_finished_seasons_are_loaded_once(monkeypatch):
    """They never change, so after the first !ppr only the current season is
    fetched. (After a restart they're simply loaded again.)"""
    asked = fake_espn(monkeypatch, previous=(2024, 2025))
    cog = PPR(MagicMock())

    await cog._seasons()
    first = list(asked)
    await cog._seasons()

    assert sorted(first, key=str) == sorted([None, 2024, 2025], key=str)
    assert asked[len(first) :] == [None]  # the second call: current season only


@pytest.mark.asyncio
async def test_a_new_finished_season_is_loaded_when_it_appears(monkeypatch):
    """When ESPN_YEAR moves on, last season shows up in previousSeasons."""
    asked = fake_espn(monkeypatch, current=2026, previous=(2025,))
    cog = PPR(MagicMock())
    await cog._seasons()

    asked = fake_espn(monkeypatch, current=2027, previous=(2025, 2026))
    _, _, finished = await cog._seasons()

    assert 2026 in asked and 2025 not in asked
    assert [row["season"] for row in finished] == [2025, 2026]


@pytest.mark.asyncio
async def test_espn_problems_become_ppr_fetch_errors(monkeypatch):
    fake_espn(monkeypatch, error=requests.exceptions.ConnectionError("ESPN down"))
    cog = PPR(MagicMock())

    with pytest.raises(PPRFetchError):
        await cog._seasons()


# The history tab


class FakeTab:
    """An in-memory tab that really stores rows, like a header-only new tab."""

    def __init__(self, rows=None):
        self.rows = (
            rows if rows is not None else [["season", "owner", "team", "ppr", "rank"]]
        )
        self.read_with: dict = {}

    def append_rows(self, rows):
        self.rows.extend(rows)

    def get_all_values(self, **kwargs):
        self.read_with = kwargs
        return [list(row) for row in self.rows]


def cog_with_history(rows=None):
    tab = FakeTab(rows)
    cog = PPR(MagicMock())
    cog._history_tab = AsyncMock(return_value=tab)
    return cog, tab


def ranked_row(owner, team_name, ppr):
    return {"owner": owner, "team": team_name, "ppr": ppr}


@pytest.mark.asyncio
async def test_save_history_appends_one_row_per_manager_with_rank():
    cog, tab = cog_with_history()

    await cog._save_history(
        2026, [ranked_row("{A}", "Aces", 1.15432), ranked_row("{B}", "Bombers", 0.9)]
    )

    assert tab.rows[1:] == [
        [2026, "{A}", "Aces", 1.154, 1],
        [2026, "{B}", "Bombers", 0.9, 2],
    ]


@pytest.mark.asyncio
async def test_last_history_is_the_latest_run_of_the_season():
    cog, _ = cog_with_history()
    await cog._save_history(2026, [ranked_row("{A}", "Aces", 1.0)])
    await cog._save_history(
        2026, [ranked_row("{B}", "Bombers", 1.2), ranked_row("{A}", "Aces", 1.1)]
    )

    last = await cog._last_history(2026)

    assert last == {"{A}": (1.1, 2), "{B}": (1.2, 1)}


@pytest.mark.asyncio
async def test_last_history_ignores_other_seasons():
    """A new season starts without arrows."""
    cog, _ = cog_with_history()
    await cog._save_history(2025, [ranked_row("{A}", "Aces", 1.3)])

    assert await cog._last_history(2026) == {}


@pytest.mark.asyncio
async def test_last_history_skips_broken_rows():
    """Someone may edit the tab by hand: a bad row is skipped, not a crash."""
    cog, _ = cog_with_history(
        [
            ["season", "owner", "team", "ppr", "rank"],
            [2026, "{A}", "Aces", "not a number", 1],
            [2026, "{B}"],
            [2026, "{C}", "Comets", 0.95, 3],
        ]
    )

    assert await cog._last_history(2026) == {"{C}": (0.95, 3)}


@pytest.mark.asyncio
async def test_last_history_is_empty_for_a_new_tab():
    cog, _ = cog_with_history()

    assert await cog._last_history(2026) == {}


@pytest.mark.asyncio
async def test_last_history_asks_for_plain_numbers():
    """A spreadsheet set to Norwegian would otherwise return 1.134 as "1,134"."""
    cog, tab = cog_with_history()

    await cog._last_history(2026)

    assert tab.read_with.get("value_render_option") == "UNFORMATTED_VALUE"
