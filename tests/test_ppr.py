"""Tests for ppr.py"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import gspread.exceptions
import pytest
import requests
from cogs.ppr import (
    DATA_HEADER,
    PPR,
    career_lines,
    career_stats,
    ranking_lines,
    owner_id,
    owner_name,
    rank_change,
    season_rows,
    season_stats,
)
from core.errors import PPRFetchError, PPRSnapshotError
from data.messages import PPR_NO_DATA_MESSAGE


def test_ppr_admin_check_tolerates_spaces_in_admin_ids(monkeypatch):
    """Spaces in ADMIN_IDS ("111, 222") don't lock anyone out of !ppr."""
    import importlib
    import cogs.ppr

    monkeypatch.setenv("ADMIN_IDS", "111, 222")
    ppr_module = importlib.reload(cogs.ppr)  # checks read ADMIN_IDS at import
    ctx = MagicMock()
    ctx.author.id = 222

    assert all(check(ctx) for check in ppr_module.PPR.ppr.checks)


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
async def test_a_loaded_league_is_reused_not_fetched_again(monkeypatch):
    """The recap has already loaded this season's league: only the finished
    seasons the cog hasn't seen yet should be fetched."""
    asked = fake_espn(monkeypatch)
    already_loaded = league([team([100, 120, 80], ["W", "L", "W"])], year=2026)
    already_loaded.previousSeasons = [2025]
    cog = PPR(MagicMock())

    returned, current, finished = await cog._seasons(already_loaded)

    assert None not in asked  # the current season wasn't fetched
    assert asked == [2025]
    assert returned is already_loaded
    assert [row["season"] for row in current] == [2026]
    assert [row["season"] for row in finished] == [2025]


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


# The data tab


class FakeGridTab:
    """A tab with a fixed size, like Google's: writing past the last row
    fails, and resize() grows or shrinks it (dropping rows below)."""

    def __init__(self, rows=2):
        self.row_count = rows
        self.cells: list[list] = []

    def resize(self, rows=None, cols=None):
        self.row_count = rows
        self.cells = self.cells[:rows]

    def update(self, values, range_name):
        assert range_name == "A1"
        if len(values) > self.row_count:
            raise gspread.exceptions.APIError(MagicMock())  # "exceeds grid limits"
        self.cells = [list(row) for row in values] + self.cells[len(values) :]


def data_row(season, owner, ppr):
    row = {key: 0 for key in DATA_HEADER}
    row.update(season=season, owner=owner, manager="Ann Lee", team="Aces", ppr=ppr)
    return row


@pytest.mark.asyncio
async def test_export_writes_the_header_and_every_row_in_column_order(monkeypatch):
    tab = FakeGridTab(rows=2)  # what get_or_create_tab() creates
    monkeypatch.setattr("cogs.ppr.get_or_create_tab", lambda *args: tab)
    rows = [
        data_row(2024, "{A}", 1.1),
        data_row(2025, "{A}", 0.9),
        data_row(2026, "{A}", 1.0),
    ]

    await PPR(MagicMock())._export(rows)

    assert tab.cells[0] == DATA_HEADER
    assert len(tab.cells) == 4
    by_column = dict(zip(DATA_HEADER, tab.cells[2]))
    assert by_column["season"] == 2025
    assert by_column["owner"] == "{A}"
    assert by_column["ppr"] == 0.9


@pytest.mark.asyncio
async def test_export_leaves_no_old_rows_behind(monkeypatch):
    """Fewer rows than last time (e.g. a manager's rows removed): the tab
    shrinks, so the old extra rows don't linger at the bottom."""
    tab = FakeGridTab(rows=2)
    monkeypatch.setattr("cogs.ppr.get_or_create_tab", lambda *args: tab)
    cog = PPR(MagicMock())
    await cog._export([data_row(2025, "{A}", 1.0), data_row(2025, "{B}", 1.0)])

    await cog._export([data_row(2025, "{A}", 1.0)])

    assert len(tab.cells) == 2  # header + one row
    assert tab.row_count == 2


# The !ppr command


def current_row(owner, team_name, ppr, season=2026):
    return {"season": season, "owner": owner, "team": team_name, "ppr": ppr}


def cog_for_ppr(current, finished=(), last=None):
    """A PPR cog with ESPN and the spreadsheet faked, and a fake ctx."""
    cog = PPR(MagicMock())
    cog._seasons = AsyncMock(return_value=(MagicMock(), current, list(finished)))
    cog._last_history = AsyncMock(return_value=last or {})
    cog._save_history = AsyncMock()
    cog._export = AsyncMock()
    cog._notify_admin = AsyncMock()
    ctx = MagicMock()
    ctx.send = AsyncMock()
    return cog, ctx


@pytest.mark.asyncio
async def test_ppr_posts_the_ranking_with_changes_since_last_time():
    current = [
        current_row("{B}", "Bombers", 0.95),
        current_row("{A}", "Aces", 1.10),
        current_row("{C}", "Comets", 1.02),
    ]
    last = {"{A}": (1.05, 2), "{B}": (0.95, 3), "{C}": (1.04, 1)}
    cog, ctx = cog_for_ppr(current, last=last)

    await cog.ppr.callback(cog, ctx)

    sent = ctx.send.call_args.args[0]
    assert "1. Aces: 1.100 (+0.050) ⇧1" in sent
    assert "2. Comets: 1.020 (-0.020) ⇩1" in sent
    assert "3. Bombers: 0.950 (+0.000) =" in sent


@pytest.mark.asyncio
async def test_a_manager_without_history_shows_no_change():
    """The first run of a season (or of the new tab) has nothing to compare."""
    cog, ctx = cog_for_ppr([current_row("{A}", "Aces", 1.0)], last={})

    await cog.ppr.callback(cog, ctx)

    assert "1. Aces: 1.000 (+0.000) =" in ctx.send.call_args.args[0]


@pytest.mark.asyncio
async def test_ppr_saves_the_history_and_the_data_tab_after_posting():
    finished = [current_row("{A}", "Aces", 0.9, season=2025)]
    current = [current_row("{B}", "Bombers", 0.95), current_row("{A}", "Aces", 1.1)]
    cog, ctx = cog_for_ppr(current, finished=finished)
    order = []
    ctx.send.side_effect = lambda *a: order.append("post")
    cog._save_history.side_effect = lambda *a: order.append("history")

    await cog.ppr.callback(cog, ctx)

    assert order == ["post", "history"]
    season, ranked = cog._save_history.await_args.args
    assert season == 2026
    assert [row["owner"] for row in ranked] == ["{A}", "{B}"]  # best first
    cog._export.assert_awaited_once_with(finished + current)


@pytest.mark.asyncio
async def test_before_the_first_game_ppr_says_there_is_no_data():
    cog, ctx = cog_for_ppr([])

    await cog.ppr.callback(cog, ctx)

    ctx.send.assert_awaited_once_with(PPR_NO_DATA_MESSAGE)
    cog._save_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_espn_failing_tells_the_admin_and_raises():
    cog, ctx = cog_for_ppr([])
    cog._seasons = AsyncMock(side_effect=PPRFetchError("-", "2026", "ESPN down"))

    with pytest.raises(PPRFetchError):
        await cog.ppr.callback(cog, ctx)

    cog._notify_admin.assert_awaited_once()
    ctx.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unreadable_history_still_posts_the_ranking():
    """Arrows are nice to have: the ranking goes out without them."""
    cog, ctx = cog_for_ppr([current_row("{A}", "Aces", 1.0)])
    cog._last_history = AsyncMock(
        side_effect=requests.exceptions.ConnectionError("Google down")
    )

    await cog.ppr.callback(cog, ctx)

    assert "1. Aces: 1.000 (+0.000) =" in ctx.send.call_args.args[0]


@pytest.mark.asyncio
async def test_a_failed_save_raises_after_posting():
    cog, ctx = cog_for_ppr([current_row("{A}", "Aces", 1.0)])
    cog._save_history = AsyncMock(
        side_effect=requests.exceptions.ConnectionError("Google down")
    )

    with pytest.raises(PPRSnapshotError):
        await cog.ppr.callback(cog, ctx)

    ctx.send.assert_awaited_once()  # the ranking already went out


# Shared by !ppr and the recap


def test_ranking_lines_show_the_change_since_the_last_ranking():
    ranked = [current_row("{A}", "Aces", 1.1), current_row("{B}", "Bombers", 0.9)]
    last = {"{A}": (1.05, 2), "{B}": (0.95, 1)}

    assert ranking_lines(ranked, last) == [
        "1. Aces: 1.100 (+0.050) ⇧1",
        "2. Bombers: 0.900 (-0.050) ⇩1",
    ]


def test_ranking_lines_without_history_show_no_change():
    assert ranking_lines([current_row("{A}", "Aces", 1.0)], {}) == [
        "1. Aces: 1.000 (+0.000) ="
    ]


@pytest.mark.asyncio
async def test_last_history_or_empty_returns_the_history():
    cog = PPR(MagicMock())
    cog._last_history = AsyncMock(return_value={"{A}": (1.0, 1)})

    assert await cog._last_history_or_empty(2026) == {"{A}": (1.0, 1)}


@pytest.mark.asyncio
async def test_last_history_or_empty_is_empty_when_google_fails():
    cog = PPR(MagicMock())
    cog._last_history = AsyncMock(
        side_effect=requests.exceptions.ConnectionError("Google down")
    )

    assert await cog._last_history_or_empty(2026) == {}


@pytest.mark.asyncio
async def test_save_writes_the_history_and_the_data_tab():
    cog = PPR(MagicMock())
    cog._save_history = AsyncMock()
    cog._export = AsyncMock()
    ranked = [current_row("{A}", "Aces", 1.0)]
    rows = ranked + [current_row("{A}", "Aces", 0.9, season=2025)]

    await cog._save(2026, ranked, rows)

    cog._save_history.assert_awaited_once_with(2026, ranked)
    cog._export.assert_awaited_once_with(rows)


@pytest.mark.asyncio
async def test_save_turns_google_errors_into_ppr_snapshot_errors():
    cog = PPR(MagicMock())
    cog._save_history = AsyncMock(
        side_effect=requests.exceptions.ConnectionError("Google down")
    )
    cog._export = AsyncMock()

    with pytest.raises(PPRSnapshotError):
        await cog._save(2026, [], [])


# Career PPR


def season(owner, year, ppr, games=3):
    return {"owner": owner, "season": year, "ppr": ppr, "games": games}


def test_career_stats_average_seasons_and_value():
    finished = [
        season("{A}", 2024, 1.2),
        season("{A}", 2025, 0.9),
        season("{B}", 2025, 1.0),
    ]

    average, seasons, value = career_stats(finished, "{A}")

    assert average == pytest.approx(1.05)
    assert seasons == 2
    assert value == pytest.approx(0.1)  # +0.2 and -0.1


def test_career_stats_is_none_without_a_finished_season():
    assert career_stats([season("{A}", 2025, 1.0)], "{B}") is None


def test_career_lines_compare_with_the_career_before_the_season():
    # Before 2025: Aces 1.10 (1st), Bombers 0.90 (2nd). In 2025: Aces 1.00,
    # Bombers 1.30, and Comets join with 1.30.
    before = [season("{A}", 2024, 1.1), season("{B}", 2024, 0.9)]
    after = before + [
        season("{A}", 2025, 1.0),
        season("{B}", 2025, 1.3),
        season("{C}", 2025, 1.3),
    ]
    managers = [("{A}", "Aces"), ("{B}", "Bombers"), ("{C}", "Comets")]

    assert career_lines(managers, before, after) == [
        "1. Comets: 1.300 over 1 season",
        "   Career value: +0.300",
        "2. Bombers: 1.100 (+0.200) = over 2 seasons",
        "   Career value: +0.200 (+0.300 this season)",
        "3. Aces: 1.050 (-0.050) ⇩2 over 2 seasons",
        "   Career value: +0.100 (+0.000 this season)",
    ]


def test_career_arrows_rank_the_old_career_by_average_not_value():
    """Aces: one great season (higher average). Bombers: three good ones
    (higher total value). Ranked by average, nobody moved this season."""
    before = [season("{A}", 2024, 1.3)] + [
        season("{B}", year, 1.15) for year in (2022, 2023, 2024)
    ]
    after = before + [season("{A}", 2025, 1.3), season("{B}", 2025, 1.15)]
    managers = [("{A}", "Aces"), ("{B}", "Bombers")]

    lines = career_lines(managers, before, after)

    assert lines[0].startswith("1. Aces: 1.300 (+0.000) =")
    assert lines[2].startswith("2. Bombers: 1.150 (+0.000) =")


def test_career_lines_leave_out_a_manager_without_finished_seasons():
    after = [season("{A}", 2025, 1.0)]

    lines = career_lines([("{A}", "Aces"), ("{B}", "Bombers")], [], after)

    assert not any("Bombers" in line for line in lines)


def season_end_league(regular_weeks=3, year=2025, scoring_period=None):
    """A league whose teams are Aces ({OWNER-1}) and Bombers ({OWNER-2}). By
    default ESPN is past the regular season, as in a real season-end recap."""
    if scoring_period is None:
        scoring_period = regular_weeks + 3
    return SimpleNamespace(
        year=year,
        scoringPeriodId=scoring_period,
        teams=[
            team([], [], team_id=1, name="Aces"),
            team([], [], team_id=2, name="Bombers"),
        ],
        settings=SimpleNamespace(reg_season_count=regular_weeks),
    )


def cog_with_seasons(current, finished):
    cog = PPR(MagicMock())
    cog._seasons = AsyncMock(return_value=(MagicMock(), current, finished))
    cog._last_history = AsyncMock(return_value={})
    cog._save = AsyncMock()
    return cog


@pytest.mark.asyncio
async def test_season_end_shows_final_ppr_without_arrows_then_career():
    current = [
        {**season("{OWNER-1}", 2025, 1.1), "team": "Aces"},
        {**season("{OWNER-2}", 2025, 0.9), "team": "Bombers"},
    ]
    finished = [season("{OWNER-1}", 2024, 0.9), season("{OWNER-2}", 2024, 1.1)]
    cog = cog_with_seasons(current, finished)

    lines = await cog.season_end_section(season_end_league())

    assert lines[:4] == [
        "",
        "**Final PPR 2025:**",
        "1. Aces: 1.100",
        "2. Bombers: 0.900",
    ]
    assert lines[4:6] == ["", "**Career PPR:**"]
    # The season just ended counts: both have two seasons now, averaging 1.000.
    assert "over 2 seasons" in lines[6] and "over 2 seasons" in lines[8]


@pytest.mark.asyncio
async def test_season_end_career_waits_until_the_regular_season_is_over():
    """Called mid-season, the season isn't part of anyone's career yet."""
    current = [{**season("{OWNER-1}", 2025, 1.1, games=2), "team": "Aces"}]
    finished = [season("{OWNER-1}", 2024, 0.9)]
    cog = cog_with_seasons(current, finished)

    lines = await cog.season_end_section(season_end_league(scoring_period=3))

    career = lines[lines.index("**Career PPR:**") + 1]
    assert career.startswith("1. Aces: 0.900")  # 2024 only


@pytest.mark.asyncio
async def test_season_end_counts_a_season_with_a_bye():
    """A team with fewer games (a bye, a voided week) still gets the season
    counted once ESPN is past the regular season."""
    current = [
        {**season("{OWNER-1}", 2025, 1.1, games=3), "team": "Aces"},
        {**season("{OWNER-2}", 2025, 0.9, games=2), "team": "Bombers"},
    ]
    finished = [season("{OWNER-1}", 2024, 0.9), season("{OWNER-2}", 2024, 1.1)]
    cog = cog_with_seasons(current, finished)

    lines = await cog.season_end_section(season_end_league())

    career = lines[lines.index("**Career PPR:**") + 1 :]
    assert sum("over 2 seasons" in line for line in career) == 2


@pytest.mark.asyncio
async def test_season_end_passes_the_recaps_league_on():
    cog = cog_with_seasons([], [])
    league_obj = season_end_league()

    assert await cog.season_end_section(league_obj) == []
    cog._seasons.assert_awaited_once_with(league_obj)


# The weekly recap section


@pytest.mark.asyncio
async def test_recap_section_is_a_heading_and_the_ranking():
    current = [current_row("{B}", "Bombers", 0.9), current_row("{A}", "Aces", 1.1)]
    finished = [current_row("{A}", "Aces", 1.0, season=2025)]
    cog = cog_with_seasons(current, finished)
    cog._last_history = AsyncMock(return_value={"{A}": (1.0, 2), "{B}": (1.0, 1)})
    league_obj = MagicMock()

    lines = await cog.recap_section(league_obj, 5)

    assert lines == [
        "",
        "**PPR after week 5:**",
        "1. Aces: 1.100 (+0.100) ⇧1",
        "2. Bombers: 0.900 (-0.100) ⇩1",
    ]
    cog._seasons.assert_awaited_once_with(league_obj)


@pytest.mark.asyncio
async def test_recap_section_saves_the_ranking():
    """So next week's recap compares with this one."""
    current = [current_row("{B}", "Bombers", 0.9), current_row("{A}", "Aces", 1.1)]
    finished = [current_row("{A}", "Aces", 1.0, season=2025)]
    cog = cog_with_seasons(current, finished)

    await cog.recap_section(MagicMock(), 5)

    season_saved, ranked, rows = cog._save.await_args.args
    assert season_saved == 2026
    assert [row["owner"] for row in ranked] == ["{A}", "{B}"]
    assert rows == finished + current


@pytest.mark.asyncio
async def test_recap_section_is_empty_before_the_first_game():
    cog = cog_with_seasons([], [])

    assert await cog.recap_section(MagicMock(), 1) == []
    cog._save.assert_not_awaited()
