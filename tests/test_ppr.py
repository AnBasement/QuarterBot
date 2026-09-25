"""Tests for ppr.py"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from cogs.ppr import PPR
from core.errors import PPRFetchError
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
