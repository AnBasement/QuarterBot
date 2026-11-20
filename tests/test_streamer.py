"""Tests for streamer.py"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests

from core.errors import StreamerFetchError
from cogs.streamer import (
    SeasonData,
    Streamer,
    PoolPlayer,
    Start,
    StreamedStart,
    best_pickup,
    best_week,
    collect_season,
    fetch_pool,
    lineup_slot_counts,
    most_positive,
    overall_ranking,
    position_scores,
    replacement_points,
    streamed_starts,
    streamed_weeks,
    streamer_lines,
    usable_positions,
)


def test_a_run_longer_than_two_weeks_is_not_streaming():
    # Player 501 in weeks 1-2 (run of 2), 777 in week 3 (run of 1), 888 in
    # weeks 4-6 (run of 3): only the run of 3 is not streaming.
    starters = {1: 501, 2: 501, 3: 777, 4: 888, 5: 888, 6: 888}

    assert streamed_weeks(starters) == [1, 2, 3]


def test_the_last_run_is_counted():
    # Nothing comes after week 2 to close the run, so it is easy to forget it.
    assert streamed_weeks({1: 5, 2: 6}) == [1, 2]


def test_swapping_back_and_forth_is_streaming():
    # Two players, but every run is one week long.
    assert streamed_weeks({1: 5, 2: 6, 3: 5, 4: 6}) == [1, 2, 3, 4]


def test_a_missing_week_breaks_a_run():
    # Week 3 has no starter, so this is two runs of 2 weeks, not one run of 4.
    assert streamed_weeks({1: 5, 2: 5, 4: 5, 5: 5}) == [1, 2, 4, 5]


def test_no_weeks_returns_nothing():
    assert streamed_weeks({}) == []


# Which positions to use

NORMAL_LEAGUE = {"QB": 1, "RB": 2, "TE": 1, "K": 1, "D/ST": 1, "OP": 0, "BE": 6}


def test_a_normal_league_uses_all_four_positions():
    assert usable_positions(NORMAL_LEAGUE) == (["QB", "TE", "K", "D/ST"], [])


def test_a_position_the_league_does_not_start_is_skipped_without_a_reason():
    slots = {"QB": 1, "TE": 1, "D/ST": 1}  # no K key at all

    assert usable_positions(slots) == (["QB", "TE", "D/ST"], [])


def test_a_position_with_two_slots_is_left_out_with_a_reason():
    used, reasons = usable_positions({**NORMAL_LEAGUE, "TE": 2})

    assert used == ["QB", "K", "D/ST"]
    assert len(reasons) == 1
    assert reasons[0].startswith("TE:")


def test_qb_is_left_out_when_the_league_has_a_superflex_slot():
    used, reasons = usable_positions({**NORMAL_LEAGUE, "OP": 1})

    assert used == ["TE", "K", "D/ST"]
    assert len(reasons) == 1
    assert reasons[0].startswith("QB:")


def test_a_superflex_league_with_two_tight_ends_gets_two_reasons():
    used, reasons = usable_positions({**NORMAL_LEAGUE, "OP": 1, "TE": 2})

    assert used == ["K", "D/ST"]
    assert len(reasons) == 2


# The replacement points


def test_the_best_projected_are_chosen_even_if_a_lower_one_scored_more():
    pool = [
        PoolPlayer(1, 10.0, 4.0),
        PoolPlayer(2, 9.0, 6.0),
        PoolPlayer(3, 8.0, 8.0),
        PoolPlayer(4, 2.0, 40.0),  # a lucky low-projected player is ignored
    ]

    assert replacement_points(pool, set()) == 6.0  # (4 + 6 + 8) / 3


def test_a_rostered_player_is_skipped():
    pool = [PoolPlayer(1, 10.0, 30.0), PoolPlayer(2, 5.0, 5.0)]

    assert replacement_points(pool, {1}) == 5.0


def test_a_player_with_no_projection_is_skipped():
    # A player on a bye or ruled out: projection 0 and no points.
    pool = [PoolPlayer(1, 0.0, None), PoolPlayer(2, 5.0, 7.0)]

    assert replacement_points(pool, set()) == 7.0


def test_a_player_ruled_out_is_skipped_even_with_points_recorded():
    # Projection 0 means nobody could start him, whatever his points say.
    pool = [PoolPlayer(1, 0.0, 0.0), PoolPlayer(2, 5.0, 7.0)]

    assert replacement_points(pool, set()) == 7.0


def test_a_free_agent_who_scored_zero_is_used():
    # 0.0 points is a real result, not "missing". Don't use `if player.points`.
    pool = [PoolPlayer(1, 6.0, 0.0), PoolPlayer(2, 5.0, 4.0)]

    assert replacement_points(pool, set()) == 2.0


def test_fewer_than_three_free_agents_averages_those():
    pool = [PoolPlayer(1, 6.0, 10.0), PoolPlayer(2, 5.0, 4.0)]

    assert replacement_points(pool, set()) == 7.0


def test_no_free_agents_returns_none():
    assert replacement_points([], set()) is None
    assert replacement_points([PoolPlayer(1, 6.0, 10.0)], {1}) is None


# Scoring the streamed starts


def season(weeks, replacement):
    """rostered and pool for TE in the given weeks, where one free agent scored
    `replacement` points every week (the only one, so he is the replacement)."""
    rostered = {(week, "TE"): set() for week in weeks}
    pool = {(week, "TE"): [PoolPlayer(900, 10.0, replacement)] for week in weeks}
    return rostered, pool


def te_starts(team_id, players, points=10.0):
    """One manager's TE starts from week 1: players[0] in week 1, and so on."""
    return {
        (team_id, "TE"): {
            week: Start(player_id, f"Player {player_id}", points)
            for week, player_id in enumerate(players, start=1)
        }
    }


def test_a_streamed_start_is_points_minus_the_replacement():
    rostered, pool = season([1], replacement=6.0)
    starts = {(1, "TE"): {1: Start(5, "Kelce", 15.5)}}

    assert streamed_starts(starts, rostered, pool) == [
        StreamedStart(1, "TE", 1, "Kelce", 15.5, 9.5)
    ]


def test_a_long_run_makes_no_streamed_starts():
    rostered, pool = season([1, 2, 3], replacement=6.0)

    # The same player three weeks in a row is a permanent starter.
    assert streamed_starts(te_starts(1, [5, 5, 5]), rostered, pool) == []


def test_two_managers_are_compared_with_the_same_replacement():
    rostered, pool = season([1, 2], replacement=6.0)
    starts = {**te_starts(1, [5, 6], points=10.0), **te_starts(2, [7, 8], points=8.0)}

    overs = {
        (s.team_id, s.week): s.over for s in streamed_starts(starts, rostered, pool)
    }

    assert overs == {(1, 1): 4.0, (1, 2): 4.0, (2, 1): 2.0, (2, 2): 2.0}


def test_a_week_with_no_replacement_is_left_out():
    # Week 2 has no free agents at all, so there is nothing to compare with.
    rostered, pool = season([1], replacement=6.0)

    result = streamed_starts(te_starts(1, [5, 6]), rostered, pool)

    assert [s.week for s in result] == [1]


def over_list(count):
    """`count` streamed starts of 2.0 points over, for one manager."""
    return [StreamedStart(1, "TE", week, "P", 8.0, 2.0) for week in range(count)]


def test_six_streamed_starts_qualify():
    assert position_scores(over_list(6)) == {1: {"TE": (2.0, 6)}}


def test_five_streamed_starts_do_not_qualify():
    assert position_scores(over_list(5)) == {}


def test_the_score_is_the_average_and_the_count_comes_with_it():
    streamed = [
        StreamedStart(1, "K", week, "P", 0.0, over)
        for week, over in enumerate([1.0, 2.0, 3.0, 4.0, 5.0, 9.0])
    ]

    assert position_scores(streamed) == {1: {"K": (4.0, 6)}}


def test_positions_and_managers_are_scored_separately():
    streamed = over_list(6) + [
        StreamedStart(2, "K", week, "P", 0.0, -1.0) for week in range(6)
    ]

    assert position_scores(streamed) == {1: {"TE": (2.0, 6)}, 2: {"K": (-1.0, 6)}}


# The overall ranking


def test_the_higher_average_ranks_first():
    scores = {
        1: {"TE": (1.0, 6), "K": (3.0, 6)},  # average 2.0
        2: {"TE": (4.0, 6), "K": (1.0, 6)},  # average 2.5
    }

    assert [team_id for team_id, _, _ in overall_ranking(scores)] == [2, 1]


def test_the_overall_score_and_the_position_scores_come_with_the_rank():
    scores = {1: {"TE": (1.0, 6), "K": (3.0, 7)}}

    assert overall_ranking(scores) == [(1, 2.0, {"TE": (1.0, 6), "K": (3.0, 7)})]


def test_a_manager_in_only_one_position_is_left_out():
    scores = {1: {"TE": (9.0, 6)}, 2: {"TE": (1.0, 6), "K": (1.0, 6)}}

    assert [team_id for team_id, _, _ in overall_ranking(scores)] == [2]


def test_equal_as_shown_the_manager_with_more_positions_wins():
    # Both show +1.00, but 1 has the higher raw score (1.004). 2 streamed three
    # positions, so 2 is first. Without the rounding, 1 would win.
    scores = {
        1: {"TE": (1.004, 6), "K": (1.004, 6)},
        2: {"TE": (1.0, 6), "K": (1.0, 6), "D/ST": (1.0, 6)},
    }

    assert [team_id for team_id, _, _ in overall_ranking(scores)] == [2, 1]


def test_more_positions_do_not_beat_a_higher_score_that_shows_differently():
    # 1 shows +1.01 and 2 shows +1.00, so 1 is first even with fewer positions.
    scores = {
        1: {"TE": (1.01, 6), "K": (1.01, 6)},
        2: {"TE": (1.0, 6), "K": (1.0, 6), "D/ST": (1.0, 6)},
    }

    assert [team_id for team_id, _, _ in overall_ranking(scores)] == [1, 2]


def test_nobody_qualifying_gives_an_empty_ranking():
    assert overall_ranking({}) == []
    assert overall_ranking({1: {"TE": (1.0, 6)}}) == []


# The extras


def streamed(team_id, position, week, over):
    return StreamedStart(team_id, position, week, "P", 0.0, over)


def test_best_pickup_is_the_start_with_the_most_over():
    starts = [
        streamed(1, "TE", 1, 3.0),
        streamed(2, "K", 2, 9.5),
        streamed(1, "K", 3, -4.0),
    ]

    assert best_pickup(starts) == starts[1]
    assert best_pickup([]) is None


def test_most_positive_counts_only_starts_above_the_replacement():
    starts = [
        streamed(1, "TE", 1, 3.0),
        streamed(1, "TE", 2, 1.0),
        streamed(2, "K", 1, 5.0),
        streamed(2, "K", 2, 0.0),  # exactly on the replacement is not above it
        streamed(2, "K", 3, 0.0),
        streamed(2, "K", 4, -2.0),
    ]

    assert most_positive(starts) == (1, 2)


def test_most_positive_is_none_when_nothing_is_above():
    assert most_positive([streamed(1, "TE", 1, -1.0)]) is None
    assert most_positive([]) is None


def test_best_week_adds_up_the_positions_streamed_that_week():
    starts = [
        streamed(1, "TE", 3, 5.0),
        streamed(1, "K", 3, 6.0),  # week 3 for manager 1: 11.0 in total
        streamed(2, "TE", 4, 10.0),  # the best single start, but only 10.0
    ]

    assert best_week(starts) == (1, 3, 11.0)
    assert best_week([]) is None


# Reading the season from ESPN. The fakes below are shaped like espn_api's objects.


def lineup_player(player_id, position, slot, points=5.0):
    return SimpleNamespace(
        playerId=player_id,
        name=f"Player {player_id}",
        position=position,
        slot_position=slot,
        points=points,
    )


def matchup(home_lineup, away_lineup=None):
    """One box score. With no away lineup it is a bye: espn_api gives the empty
    side no team (None) and no players."""
    bye = away_lineup is None
    return SimpleNamespace(
        home_team=SimpleNamespace(team_id=1),
        home_lineup=list(home_lineup),
        away_team=None if bye else SimpleNamespace(team_id=2),
        away_lineup=[] if bye else list(away_lineup),
    )


def espn_player(player_id, *stats):
    """A player as ESPN's player list has them. stats: (week, source, total), and
    optionally a season (2026 if left out)."""
    return {
        "player": {
            "id": player_id,
            "stats": [
                {
                    "seasonId": season[0] if season else 2026,
                    "scoringPeriodId": week,
                    "statSourceId": source,
                    "appliedTotal": total,
                }
                for week, source, total, *season in stats
            ],
        }
    }


class FakeRequest:
    """Stands in for league.espn_request, and remembers what was asked."""

    def __init__(self, players=()):
        self.players = list(players)
        self.calls = []

    def league_get(self, params, headers):
        self.calls.append((params, json.loads(headers["x-fantasy-filter"])))
        return {"players": self.players}


def fake_league(boxes_by_week, request=None):
    return SimpleNamespace(
        year=2026,
        box_scores=lambda week: boxes_by_week[week],
        espn_request=request or FakeRequest(),
    )


def test_a_starter_is_recorded_with_the_team_week_and_points():
    league = fake_league({1: [matchup([lineup_player(7, "TE", "TE", points=12.5)])]})

    data = collect_season(league, ["TE"], 1)

    assert data.starts == {(1, "TE"): {1: Start(7, "Player 7", 12.5)}}


def test_both_sides_of_a_matchup_are_read():
    league = fake_league(
        {1: [matchup([lineup_player(7, "TE", "TE")], [lineup_player(8, "TE", "TE")])]}
    )

    data = collect_season(league, ["TE"], 1)

    assert set(data.starts) == {(1, "TE"), (2, "TE")}


def test_a_benched_player_is_not_a_start_but_is_rostered():
    lineup = [lineup_player(7, "TE", "BE"), lineup_player(8, "TE", "IR")]
    league = fake_league({1: [matchup(lineup)]})

    data = collect_season(league, ["TE"], 1)

    assert data.starts == {}
    assert data.rostered == {(1, "TE"): {7, 8}}


def test_a_flex_slot_player_counts_if_the_own_slot_is_empty():
    league = fake_league({1: [matchup([lineup_player(7, "TE", "RB/WR/TE")])]})

    data = collect_season(league, ["TE"], 1)

    assert data.starts[(1, "TE")][1].player_id == 7


def test_the_player_in_the_own_slot_is_preferred_over_flex():
    lineup = [lineup_player(7, "TE", "RB/WR/TE"), lineup_player(8, "TE", "TE")]
    league = fake_league({1: [matchup(lineup)]})

    data = collect_season(league, ["TE"], 1)

    assert data.starts[(1, "TE")][1].player_id == 8


def test_a_bye_side_adds_nothing():
    league = fake_league({1: [matchup([lineup_player(7, "TE", "TE")])]})

    data = collect_season(league, ["TE"], 1)

    assert set(data.starts) == {(1, "TE")}


def test_only_the_asked_for_positions_are_read():
    lineup = [lineup_player(7, "TE", "TE"), lineup_player(8, "K", "K")]
    league = fake_league({1: [matchup(lineup)]})

    data = collect_season(league, ["K"], 1)

    assert set(data.starts) == {(1, "K")}
    assert set(data.rostered) == {(1, "K")}


def test_every_week_up_to_the_last_one_is_read():
    boxes = {week: [matchup([lineup_player(week, "TE", "TE")])] for week in (1, 2, 3)}
    league = fake_league(boxes)

    data = collect_season(league, ["TE"], 3)

    assert sorted(data.starts[(1, "TE")]) == [1, 2, 3]
    assert sorted(data.pool) == [(1, "TE"), (2, "TE"), (3, "TE")]


def test_weeks_after_the_last_one_are_not_read():
    # Week 3 exists in ESPN's data but has not been fully played.
    boxes = {week: [matchup([lineup_player(week, "TE", "TE")])] for week in (1, 2, 3)}
    request = FakeRequest()
    league = fake_league(boxes, request)

    data = collect_season(league, ["TE"], 2)

    assert sorted(data.starts[(1, "TE")]) == [1, 2]
    assert sorted(data.pool) == [(1, "TE"), (2, "TE")]
    assert [params["scoringPeriodId"] for params, _ in request.calls] == [1, 2]


def test_the_pool_is_asked_for_the_right_position_and_week():
    request = FakeRequest()

    fetch_pool(fake_league({}, request), week=4, position="TE")

    params, filters = request.calls[0]
    assert params == {"view": "kona_player_info", "scoringPeriodId": 4}
    assert filters["players"]["filterSlotIds"] == {"value": [6]}
    assert "limit" not in filters["players"]


def test_pool_stats_come_from_the_asked_for_week_only():
    # The wanted stats come first and the unwanted ones after, so a missing
    # filter would let the unwanted ones win.
    request = FakeRequest(
        [
            espn_player(
                9,
                (2, 0, 7.5),  # week 2 points
                (2, 1, 6.25),  # week 2 projection
                (1, 0, 99.0),  # week 1: not wanted
                (1, 1, 98.0),
            )
        ]
    )

    pool = fetch_pool(fake_league({}, request), week=2, position="TE")

    assert pool == [PoolPlayer(9, 6.25, 7.5)]


def test_pool_stats_come_from_the_leagues_season_only():
    request = FakeRequest(
        [
            espn_player(
                9, (2, 0, 7.5), (2, 1, 6.25), (2, 0, 99.0, 2025), (2, 1, 98.0, 2025)
            )
        ]
    )

    pool = fetch_pool(fake_league({}, request), week=2, position="TE")

    assert pool == [PoolPlayer(9, 6.25, 7.5)]


def test_a_player_with_no_points_that_week_has_none():
    # On a bye ESPN has a projection of 0 but no real points entry.
    request = FakeRequest([espn_player(9, (2, 1, 0.0))])

    pool = fetch_pool(fake_league({}, request), week=2, position="TE")

    assert pool == [PoolPlayer(9, 0.0, None)]


def test_a_player_with_no_stats_at_all_is_still_listed():
    request = FakeRequest([{"player": {"id": 9}}])

    pool = fetch_pool(fake_league({}, request), week=2, position="TE")

    assert pool == [PoolPlayer(9, None, None)]


def test_the_pool_has_no_limit_so_no_free_agent_is_left_out():
    # A cap by ownership could cut off a free agent with a high projection.
    lineup = [lineup_player(player_id, "TE", "BE") for player_id in range(14)]
    request = FakeRequest()
    league = fake_league({1: [matchup(lineup)]}, request)

    collect_season(league, ["TE"], 1)

    assert "limit" not in request.calls[0][1]["players"]


def test_the_collected_pool_is_stored_by_week_and_position():
    request = FakeRequest([espn_player(9, (1, 0, 3.0), (1, 1, 4.0))])
    league = fake_league({1: [matchup([lineup_player(7, "TE", "TE")])]}, request)

    data = collect_season(league, ["TE"], 1)

    assert data.pool == {(1, "TE"): [PoolPlayer(9, 4.0, 3.0)]}


# The message


def starts_over(team_id, position, over, count=6):
    return [
        StreamedStart(team_id, position, week, "P", 0.0, over) for week in range(count)
    ]


def test_the_ranking_lines_show_the_overall_and_each_position():
    streamed = starts_over(1, "TE", 2.0) + starts_over(1, "K", 1.0)

    lines = streamer_lines(2026, 9, {1: "Aces"}, streamed)

    assert lines[1] == "1. Aces: +1.50 (TE +2.00, K +1.00)"


def test_the_message_has_a_header_and_the_extras():
    streamed = [
        StreamedStart(1, "TE", 3, "Kelce", 20.5, 9.5),
        *starts_over(1, "TE", 2.0),
        *starts_over(1, "K", 1.0),
    ]

    text = "\n".join(streamer_lines(2026, 9, {1: "Aces"}, streamed))

    assert text.startswith("**Best Streamer 2026, after week 9**")
    assert "Best pickup: Kelce (TE), Aces, week 3: 20.5 points, +9.5 over" in text
    assert "Most successful streams: Aces (13)" in text
    assert "Best streaming week: Aces, week 3," in text
    assert "per streamed start" not in text  # the header is just the title


def test_nobody_ranked_posts_the_no_ranking_message():
    lines = streamer_lines(2026, 9, {1: "Aces"}, starts_over(1, "TE", 2.0))

    assert "Nobody has streamed enough in two or more positions" in lines[1]


# The command


SLOT_IDS = {"QB": 0, "TE": 6, "D/ST": 16, "K": 17, "OP": 7}


def raw_settings(slots):
    """ESPN's mSettings answer for a league starting `slots` (position -> count).
    Like older seasons, it lists only the slots with a count above 0."""
    counts = {str(SLOT_IDS[position]): n for position, n in slots.items() if n}
    counts.update({"2": 2, "4": 2, "20": 7, "21": 1})  # RB, WR, bench, IR
    return {"settings": {"rosterSettings": {"lineupSlotCounts": counts}}}


def command_league(period=18, teams=1, slots=None, year=2026):
    return SimpleNamespace(
        year=year,
        scoringPeriodId=period,
        settings=SimpleNamespace(reg_season_count=15),
        espn_request=SimpleNamespace(
            league_get=lambda params: raw_settings(slots or {"TE": 1, "K": 1})
        ),
        teams=[
            SimpleNamespace(team_id=i, team_name=f"Team {i}")
            for i in range(1, teams + 1)
        ],
    )


def fake_ctx():
    ctx = MagicMock()
    ctx.send = AsyncMock()
    return ctx


async def run_streamer(
    league=None, season=None, league_error=None, season_error=None, year=None
):
    cog = Streamer(MagicMock())
    ctx = fake_ctx()
    with (
        patch(
            "cogs.streamer.get_league", return_value=league, side_effect=league_error
        ) as get_league,
        patch(
            "cogs.streamer.collect_season",
            return_value=season,
            side_effect=season_error,
        ) as collect,
    ):
        await cog.streamer.callback(cog, ctx, year)
    ctx.get_league = get_league  # so a test can check which season was asked for
    return ctx, collect


def sent(ctx):
    return [call.args[0] for call in ctx.send.call_args_list]


@pytest.mark.parametrize(
    "period, last_week",
    [
        (2, 1),  # week 2 is about to be played, so week 1 is finished
        (9, 8),
        (15, 14),  # the last regular-season week is still being played
        (16, 15),  # the regular season is over
        (18, 15),  # playoffs: still only the regular season counts
    ],
)
@pytest.mark.asyncio
async def test_only_the_finished_regular_season_weeks_are_read(period, last_week):
    ctx, collect = await run_streamer(
        command_league(period=period), season=SeasonData({}, {}, {})
    )

    assert collect.call_args.args[2] == last_week
    assert f"after week {last_week}**" in sent(ctx)[0]


@pytest.mark.asyncio
async def test_before_the_first_week_is_played_it_says_so_and_reads_nothing():
    ctx, collect = await run_streamer(command_league(period=1))

    assert sent(ctx) == ["No week has been played yet."]
    collect.assert_not_called()


@pytest.mark.asyncio
async def test_espn_failing_to_load_the_league_posts_nothing_else():
    ctx, collect = await run_streamer(
        league_error=requests.exceptions.ConnectionError("down")
    )

    assert len(sent(ctx)) == 1
    assert sent(ctx)[0].startswith("ESPN failed, nothing was posted")
    collect.assert_not_called()


@pytest.mark.asyncio
async def test_espn_failing_while_reading_the_season_posts_nothing_else():
    ctx, _ = await run_streamer(
        command_league(), season_error=requests.exceptions.ConnectionError("down")
    )

    assert len(sent(ctx)) == 1
    assert sent(ctx)[0].startswith("ESPN failed, nothing was posted")


@pytest.mark.asyncio
async def test_a_league_with_no_usable_positions_says_so():
    league = command_league(slots={"QB": 2, "TE": 2})

    ctx, collect = await run_streamer(league)

    assert sent(ctx) == ["None of QB, TE, K and D/ST can be used in this league."]
    collect.assert_not_called()


@pytest.mark.asyncio
async def test_positions_left_out_are_reported_after_the_award():
    league = command_league(slots={"TE": 2, "K": 1})

    ctx, _ = await run_streamer(league, season=SeasonData({}, {}, {}))

    assert sent(ctx)[-1].startswith("Left out of the award: TE:")


@pytest.mark.asyncio
async def test_the_award_is_posted_from_a_whole_season_of_data():
    weeks = range(1, 13)
    starts = {
        (1, position): {week: Start(week % 2, f"P{week % 2}", points) for week in weeks}
        for position, points in (("TE", 10.0), ("K", 8.0))
    }
    pool = {
        (week, position): [PoolPlayer(900, 10.0, replacement)]
        for week in weeks
        for position, replacement in (("TE", 6.0), ("K", 6.0))
    }

    ctx, _ = await run_streamer(command_league(), season=SeasonData(starts, {}, pool))

    # TE is 4 over (10 against 6) and K is 2 over (8 against 6): overall +3.
    assert "1. Team 1: +3.00 (TE +4.00, K +2.00)" in "\n".join(sent(ctx))


@pytest.mark.asyncio
async def test_a_long_award_is_split_into_messages_under_the_limit():
    teams = 16
    names = {i: f"A rather long team name number {i} " * 4 for i in range(1, teams + 1)}
    league = command_league(teams=teams)
    league.teams = [
        SimpleNamespace(team_id=i, team_name=name) for i, name in names.items()
    ]
    weeks = range(1, 13)
    starts = {
        (i, position): {week: Start(week % 2, "P", 10.0) for week in weeks}
        for i in names
        for position in ("TE", "K")
    }
    pool = {
        (week, position): [PoolPlayer(900, 10.0, 6.0)]
        for week in weeks
        for position in ("TE", "K")
    }

    ctx, _ = await run_streamer(league, season=SeasonData(starts, {}, pool))

    messages = sent(ctx)
    assert len(messages) > 1
    assert all(len(message) <= 2000 for message in messages)


@pytest.mark.asyncio
async def test_the_year_asked_for_is_the_season_that_is_loaded():
    ctx, _ = await run_streamer(
        command_league(), season=SeasonData({}, {}, {}), year=2024
    )

    ctx.get_league.assert_called_once_with(2024)


@pytest.mark.asyncio
async def test_no_year_loads_the_current_season():
    ctx, _ = await run_streamer(command_league(), season=SeasonData({}, {}, {}))

    ctx.get_league.assert_called_once_with(None)


@pytest.mark.asyncio
async def test_the_header_names_the_season_that_was_loaded():
    ctx, _ = await run_streamer(
        command_league(year=2024), season=SeasonData({}, {}, {}), year=2024
    )

    assert sent(ctx)[0].startswith("**Best Streamer 2024, after week 15**")


# ---- the season-end section ----


def award_starts():
    """Three managers, all qualified in TE and K: team 1 is +2, team 2 is +1,
    team 3 is -1 per streamed start."""
    starts = []
    for team_id, over in ((1, 2.0), (2, 1.0), (3, -1.0)):
        for position in ("TE", "K"):
            starts += [
                StreamedStart(team_id, position, week, f"P{team_id}", 10.0, over)
                for week in range(1, 7)
            ]
    return starts


async def season_end(league, streamed):
    cog = Streamer(MagicMock())
    with patch("cogs.streamer.streamed_for", AsyncMock(return_value=streamed)) as read:
        lines = await cog.season_end_section(league)
    return lines, read


@pytest.mark.asyncio
async def test_the_section_names_the_winner_then_places_two_and_three():
    lines, _ = await season_end(command_league(teams=3), award_starts())

    assert lines[0] == ""  # separates it from the PPR lines above
    assert lines[1] == "🏆 Streamer of the Year 2026: Team 1 (+2.00)"
    assert lines[2] == "2. Team 2 (+1.00)"
    assert lines[3] == "3. Team 3 (-1.00)"


@pytest.mark.asyncio
async def test_the_section_ends_with_the_best_pickup_and_the_pointer():
    lines, _ = await season_end(command_league(teams=3), award_starts())

    assert lines[4].startswith("Best pickup: P1")
    assert lines[-1] == "Full list: `!streamer`"
    assert len(lines) == 6


@pytest.mark.asyncio
async def test_two_managers_give_only_place_two():
    streamed = [s for s in award_starts() if s.team_id != 3]

    lines, _ = await season_end(command_league(teams=3), streamed)

    assert not any(line.startswith("3. ") for line in lines)
    assert any(line.startswith("2. ") for line in lines)


@pytest.mark.asyncio
async def test_the_whole_regular_season_is_read():
    _, read = await season_end(command_league(teams=3), award_starts())

    assert read.await_args.args[2] == 15


@pytest.mark.asyncio
async def test_nobody_ranked_gives_no_section():
    lines, _ = await season_end(command_league(teams=3), [])

    assert lines == []


@pytest.mark.asyncio
async def test_no_usable_positions_gives_no_section_and_reads_nothing():
    league = command_league(slots={"TE": 2, "K": 2})

    lines, read = await season_end(league, award_starts())

    assert lines == []
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_places_two_and_three_are_shown_with_four_ranked():
    streamed = award_starts() + [
        StreamedStart(4, position, week, "P4", 10.0, -2.0)
        for position in ("TE", "K")
        for week in range(1, 7)
    ]

    lines, _ = await season_end(command_league(teams=4), streamed)

    assert not any(line.startswith("4. ") for line in lines)


def test_slots_are_found_by_number_when_zero_count_slots_are_missing():
    """The 2020 answer from ESPN: only the slots in use are listed. A lookup by
    position in the list would put the kicker's 1 on the wrong name."""
    league = command_league(slots={"QB": 1, "TE": 1, "D/ST": 1, "K": 1})

    assert lineup_slot_counts(league) == {
        "QB": 1,
        "TE": 1,
        "D/ST": 1,
        "K": 1,
        "OP": 0,
    }


def test_settings_without_slot_counts_raise_a_fetch_error():
    league = command_league()
    league.espn_request = SimpleNamespace(league_get=lambda params: {"settings": {}})

    with pytest.raises(StreamerFetchError):
        lineup_slot_counts(league)


def test_a_player_list_without_players_raises_a_fetch_error():
    request = SimpleNamespace(league_get=lambda params, headers: {})

    with pytest.raises(StreamerFetchError):
        fetch_pool(fake_league({}, request), week=1, position="TE")


def test_a_player_without_an_id_raises_a_fetch_error():
    request = FakeRequest(players=[{"player": {"stats": []}}])

    with pytest.raises(StreamerFetchError):
        fetch_pool(fake_league({}, request), week=1, position="TE")


def test_a_position_the_league_does_not_list_counts_as_zero():
    league = command_league(slots={"TE": 1})

    assert lineup_slot_counts(league)["K"] == 0


@pytest.mark.asyncio
async def test_a_response_that_is_not_as_expected_posts_nothing_but_a_note():
    ctx, _ = await run_streamer(
        command_league(), season_error=StreamerFetchError("no players")
    )

    assert sent(ctx)[0].startswith("ESPN failed, nothing was posted")


@pytest.mark.asyncio
async def test_the_command_keeps_the_kicker_when_the_settings_list_is_short():
    ctx, collect = await run_streamer(
        command_league(slots={"TE": 1, "K": 1}), season=SeasonData({}, {}, {})
    )

    assert collect.call_args.args[1] == ["TE", "K"]
