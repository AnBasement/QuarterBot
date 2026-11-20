"""!streamer: the Best Streamer award, who got the most out of streaming D/ST,
K, TE and QB over the regular season."""

import asyncio
import json
from collections import Counter, defaultdict
from typing import NamedTuple

from discord.ext import commands
from espn_api.football import League

from core.errors import StreamerFetchError
from core.utils.discord_helpers import split_message
from core.utils.espn_helpers import ESPN_ERRORS, get_league
from data.messages import (
    STREAMER_AWARD_TEMPLATE,
    STREAMER_BEST_PICKUP_TEMPLATE,
    STREAMER_FULL_LIST_MESSAGE,
    STREAMER_BEST_WEEK_TEMPLATE,
    STREAMER_HEADER_TEMPLATE,
    STREAMER_MOST_POSITIVE_TEMPLATE,
    STREAMER_NO_RANKING_MESSAGE,
    STREAMER_NOT_STARTED_MESSAGE,
)

POSITIONS = {"QB": 0, "TE": 6, "K": 17, "D/ST": 16}  # position -> ESPN slot ID
OP_SLOT_ID = 7  # the superflex slot, which can hold a quarterback
MAX_RUN = 2  # a start in a run of this many weeks or fewer counts as streaming
REPLACEMENT_SIZE = 3  # how many best-projected free agents set the replacement
MIN_STREAMED_STARTS = 6  # streamed starts needed to qualify in a position
MIN_POSITIONS = 2  # positions a manager must qualify in to be ranked overall


class PoolPlayer(NamedTuple):
    """A player from ESPN's list for one position and week."""

    player_id: int
    projected: float | None  # ESPN's last projection before the player's game
    points: float | None  # what the player actually scored


class Start(NamedTuple):
    """A manager's starter at one position in one week."""

    player_id: int
    player: str
    points: float


class StreamedStart(NamedTuple):
    """A start in a short run, compared with the replacement that week."""

    team_id: int
    position: str
    week: int
    player: str
    points: float
    over: float  # points minus the replacement points that week


def lineup_slot_counts(league: League) -> dict[str, int]:
    """How many lineup slots the league starts for each position we care about
    (QB, TE, K, D/ST) and for OP (superflex). Blocking: one ESPN request.

    league.settings.position_slot_counts can't be used: espn_api pairs ESPN's
    counts with its own list of position names by order, which only works when
    ESPN lists every slot. For older seasons it leaves out the slots with a count
    of 0, so the names end up on the wrong counts (the kicker slot is lost). ESPN
    numbers its slots, so we look them up by number instead. Raises
    StreamerFetchError if ESPN's answer has no slot counts."""
    answer = league.espn_request.league_get(params={"view": "mSettings"})
    try:
        raw = answer["settings"]["rosterSettings"]["lineupSlotCounts"]
    except (KeyError, TypeError) as exc:
        raise StreamerFetchError(
            f"ESPN's settings had no lineup slot counts ({exc!r})"
        ) from exc
    slot_ids = {**POSITIONS, "OP": OP_SLOT_ID}
    return {position: raw.get(str(slot), 0) for position, slot in slot_ids.items()}


def usable_positions(slot_counts: dict[str, int]) -> tuple[list[str], list[str]]:
    """The positions the award can use in this league, and a reason for each one
    that is left out with a note. A position the league doesn't start is skipped
    without a reason. Only one starter per position per team is followed, so a
    position with two or more slots, or QB when there is a superflex slot, is
    left out."""
    used: list[str] = []
    reasons: list[str] = []
    for position in POSITIONS:
        count = slot_counts.get(position, 0)
        if count == 0:
            continue
        if count > 1:
            reasons.append(
                f"{position}: the league starts {count} per week, "
                "which isn't supported yet"
            )
        elif position == "QB" and slot_counts.get("OP", 0) > 0:
            reasons.append(
                "QB: the league has a superflex slot, which isn't supported yet"
            )
        else:
            used.append(position)
    return used, reasons


def streamed_weeks(player_by_week: dict[int, int]) -> list[int]:
    """The weeks in which a manager's starter belonged to a run of MAX_RUN weeks
    or fewer. Input: week -> player ID for one manager and one position. A run is
    the same player in consecutive weeks."""
    streamed: list[int] = []
    run: list[int] = []  # the weeks of the run we are in the middle of
    for week in sorted(player_by_week):
        continues = (
            run
            and week == run[-1] + 1
            and player_by_week[week] == player_by_week[run[-1]]
        )
        if continues:
            run.append(week)
        else:
            if len(run) <= MAX_RUN:
                streamed += run
            run = [week]
    if len(run) <= MAX_RUN:  # the last run is never closed by a new player
        streamed += run
    return streamed


def replacement_points(pool: list[PoolPlayer], rostered: set[int]) -> float | None:
    """The average points actually scored by the REPLACEMENT_SIZE best-projected
    free agents, or None if there are none. A free agent is not in `rostered`
    and has a projection above 0 (a player on a bye or ruled out has none:
    nobody could have started him)."""
    candidates = [
        (player.projected, player.points)
        for player in pool
        if player.player_id not in rostered
        and player.points is not None
        and player.projected is not None
        and player.projected > 0
    ]
    # Pick by projection, what a manager could see, not by what happened.
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    best = candidates[:REPLACEMENT_SIZE]
    if not best:
        return None
    return sum(points for _, points in best) / len(best)


def streamed_starts(
    starts: dict[tuple[int, str], dict[int, Start]],
    rostered: dict[tuple[int, str], set[int]],
    pool: dict[tuple[int, str], list[PoolPlayer]],
) -> list[StreamedStart]:
    """Every streamed start of the season, with its points over the replacement.

    starts: (team ID, position) -> week -> the starter. rostered and pool:
    (week, position) -> the player IDs on any roster that week, and ESPN's list of
    players. A week with no replacement (no free agents) is left out."""
    replacements: dict[tuple[int, str], float | None] = {}
    result: list[StreamedStart] = []
    for (team_id, position), by_week in starts.items():
        weeks = streamed_weeks(
            {week: start.player_id for week, start in by_week.items()}
        )
        for week in weeks:
            key = (week, position)
            if key not in replacements:  # the same number for every manager
                replacements[key] = replacement_points(
                    pool.get(key, []), rostered.get(key, set())
                )
            replacement = replacements[key]
            if replacement is None:
                continue
            start = by_week[week]
            result.append(
                StreamedStart(
                    team_id,
                    position,
                    week,
                    start.player,
                    start.points,
                    start.points - replacement,
                )
            )
    return result


def position_scores(
    streamed: list[StreamedStart],
) -> dict[int, dict[str, tuple[float, int]]]:
    """Each manager's score in the positions where they qualified: the average
    points over the replacement per streamed start, and how many starts that is.
    scores[team ID][position] = (average, count)."""
    groups: defaultdict[tuple[int, str], list[float]] = defaultdict(list)
    for start in streamed:
        groups[(start.team_id, start.position)].append(start.over)
    scores: dict[int, dict[str, tuple[float, int]]] = defaultdict(dict)
    for (team_id, position), overs in groups.items():
        if len(overs) >= MIN_STREAMED_STARTS:
            scores[team_id][position] = (sum(overs) / len(overs), len(overs))
    return dict(scores)


def overall_ranking(
    scores: dict[int, dict[str, tuple[float, int]]],
) -> list[tuple[int, float, dict[str, tuple[float, int]]]]:
    """Managers who qualified in MIN_POSITIONS or more positions, best first, as
    (team ID, overall score, their position scores). The overall score is the
    average of the position scores. Equal scores (as shown, to two decimals) are
    split by the number of positions, so streaming more positions only breaks a
    tie, it never adds points."""
    ranked = []
    for team_id, by_position in scores.items():
        if len(by_position) < MIN_POSITIONS:
            continue
        average = sum(score for score, _ in by_position.values()) / len(by_position)
        ranked.append((team_id, average, by_position))
    ranked.sort(key=lambda row: (round(row[1], 2), len(row[2])), reverse=True)
    return ranked


def best_pickup(streamed: list[StreamedStart]) -> StreamedStart | None:
    """The single streamed start with the most points over the replacement."""
    if not streamed:
        return None
    return max(streamed, key=lambda start: start.over)


def most_positive(streamed: list[StreamedStart]) -> tuple[int, int] | None:
    """The manager with the most streamed starts above the replacement, as
    (team ID, count), or None if there are none. On a tie, whoever got there
    first wins."""
    counts = Counter(start.team_id for start in streamed if start.over > 0)
    top = counts.most_common(1)
    return top[0] if top else None


def best_week(streamed: list[StreamedStart]) -> tuple[int, int, float] | None:
    """The manager and week with the most points over the replacement added up
    across all the positions streamed that week, as (team ID, week, total)."""
    totals: defaultdict[tuple[int, int], float] = defaultdict(float)
    for start in streamed:
        totals[(start.team_id, start.week)] += start.over
    if not totals:
        return None
    (team_id, week), total = max(totals.items(), key=lambda item: item[1])
    return team_id, week, total


class SeasonData(NamedTuple):
    """What the award needs from ESPN for a season."""

    starts: dict[tuple[int, str], dict[int, Start]]  # (team ID, position) -> week
    rostered: dict[tuple[int, str], set[int]]  # (week, position) -> player IDs
    pool: dict[tuple[int, str], list[PoolPlayer]]  # (week, position) -> ESPN's list


def fetch_pool(league: League, week: int, position: str) -> list[PoolPlayer]:
    """Every player at a position, with their points and the projection ESPN
    last stored for the week. Free or not: the caller removes the
    rostered ones. Blocking. Raises StreamerFetchError if ESPN's answer is not
    shaped as expected.

    league.free_agents() can't be used: it only lists players who are free now.
    The same request without that filter also answers for past weeks. No limit is
    set, so ESPN answers with all of them: a cap by ownership could leave out a
    free agent with a high projection."""
    filters = {
        "players": {
            "filterSlotIds": {"value": [POSITIONS[position]]},
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
        }
    }
    data = league.espn_request.league_get(
        params={"view": "kona_player_info", "scoringPeriodId": week},
        headers={"x-fantasy-filter": json.dumps(filters)},
    )
    pool = []
    try:
        for entry in data["players"]:
            info = entry["player"]
            points = projected = None
            for stat in info.get("stats", []):
                if stat.get("seasonId") != league.year:
                    continue
                if stat.get("scoringPeriodId") != week:
                    continue
                if stat.get("statSourceId") == 0:  # 0 = what happened
                    points = stat.get("appliedTotal")
                elif stat.get("statSourceId") == 1:  # 1 = ESPN's projection
                    projected = stat.get("appliedTotal")
            pool.append(PoolPlayer(info["id"], projected, points))
    except (KeyError, TypeError) as exc:
        raise StreamerFetchError(
            f"ESPN's player list for week {week} ({position}) was not as expected: "
            f"{exc!r}"
        ) from exc
    return pool


def collect_season(league: League, positions: list[str], last_week: int) -> SeasonData:
    """Everything the award needs from ESPN for weeks 1 to `last_week`. Blocking:
    call it through asyncio.to_thread()."""
    starts: dict[tuple[int, str], dict[int, Start]] = defaultdict(dict)
    rostered: dict[tuple[int, str], set[int]] = defaultdict(set)
    pool: dict[tuple[int, str], list[PoolPlayer]] = {}
    for week in range(1, last_week + 1):
        for box in league.box_scores(week):
            for team, lineup in (
                (box.home_team, box.home_lineup),
                (box.away_team, box.away_lineup),
            ):
                for player in lineup:
                    if player.position in positions:
                        rostered[(week, player.position)].add(player.playerId)
                for position in positions:
                    starters = [
                        player
                        for player in lineup
                        if player.position == position
                        and player.slot_position not in ("BE", "IR")
                    ]
                    # The player in the position's own slot, not one in a flex slot.
                    starters.sort(key=lambda player: player.slot_position != position)
                    if starters:
                        first = starters[0]
                        starts[(team.team_id, position)][week] = Start(
                            first.playerId, first.name, first.points
                        )
        for position in positions:
            pool[(week, position)] = fetch_pool(league, week, position)
    return SeasonData(dict(starts), dict(rostered), pool)


def streamer_lines(
    year: int, week: int, names: dict[int, str], streamed: list[StreamedStart]
) -> list[str]:
    """The award message after `week`, one string per line. names: team ID ->
    team name."""
    lines = [STREAMER_HEADER_TEMPLATE.format(year=year, week=week)]
    ranking = overall_ranking(position_scores(streamed))
    if not ranking:
        lines.append(STREAMER_NO_RANKING_MESSAGE)
    for rank, (team_id, overall, by_position) in enumerate(ranking, start=1):
        detail = ", ".join(
            f"{position} {by_position[position][0]:+.2f}"
            for position in POSITIONS
            if position in by_position
        )
        lines.append(f"{rank}. {names[team_id]}: {overall:+.2f} ({detail})")

    pickup = best_pickup(streamed)
    if pickup:
        lines.append(
            STREAMER_BEST_PICKUP_TEMPLATE.format(
                player=pickup.player,
                position=pickup.position,
                team=names[pickup.team_id],
                week=pickup.week,
                points=pickup.points,
                over=pickup.over,
            )
        )
    positive = most_positive(streamed)
    if positive:
        team_id, count = positive
        lines.append(
            STREAMER_MOST_POSITIVE_TEMPLATE.format(team=names[team_id], count=count)
        )
    week_best = best_week(streamed)
    if week_best:
        team_id, week, over = week_best
        lines.append(
            STREAMER_BEST_WEEK_TEMPLATE.format(
                team=names[team_id], week=week, over=over
            )
        )
    return lines


async def streamed_for(
    league: League, positions: list[str], last_week: int
) -> list[StreamedStart]:
    """Reads weeks 1 to last_week from ESPN and returns the streamed starts."""
    data = await asyncio.to_thread(collect_season, league, positions, last_week)
    return streamed_starts(data.starts, data.rostered, data.pool)


class Streamer(commands.Cog):
    """The Best Streamer award."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def season_end_section(self, league: League) -> list[str]:
        """The Streamer of the Year lines for the season-end recap, or [] when
        there is nothing to hand out. ESPN errors and StreamerFetchError are left to
        the caller."""
        positions, _ = usable_positions(
            await asyncio.to_thread(lineup_slot_counts, league)
        )
        if not positions:
            return []
        streamed = await streamed_for(
            league, positions, league.settings.reg_season_count
        )
        ranking = overall_ranking(position_scores(streamed))
        if not ranking:
            return []
        names = {team.team_id: team.team_name for team in league.teams}
        winner_id, winner_score, _ = ranking[0]
        lines = [
            "",
            STREAMER_AWARD_TEMPLATE.format(
                year=league.year, team=names[winner_id], score=winner_score
            ),
        ]
        for rank, (team_id, overall, _) in enumerate(ranking[1:3], start=2):
            lines.append(f"{rank}. {names[team_id]} ({overall:+.2f})")
        pickup = best_pickup(streamed)
        if pickup:
            lines.append(
                STREAMER_BEST_PICKUP_TEMPLATE.format(
                    player=pickup.player,
                    position=pickup.position,
                    team=names[pickup.team_id],
                    week=pickup.week,
                    points=pickup.points,
                    over=pickup.over,
                )
            )
        lines.append(STREAMER_FULL_LIST_MESSAGE)
        return lines

    @commands.command(name="streamer")
    async def streamer(self, ctx: commands.Context, year: int | None = None) -> None:
        """Posts the Best Streamer ranking for the season (this one by default),
        counting the weeks played so far. Reads ESPN only, stores nothing."""
        async with ctx.typing():  # shows "typing..." while ESPN is read
            try:
                league = await asyncio.to_thread(get_league, year)
                # scoringPeriodId is the week about to be played, so the one
                # before it is the last finished week.
                last_week = min(
                    league.settings.reg_season_count, league.scoringPeriodId - 1
                )
                if last_week < 1:
                    await ctx.send(STREAMER_NOT_STARTED_MESSAGE)
                    return
                positions, left_out = usable_positions(
                    await asyncio.to_thread(lineup_slot_counts, league)
                )
                if not positions:
                    await ctx.send(
                        "None of QB, TE, K and D/ST can be used in this league."
                    )
                    return
                streamed = await streamed_for(league, positions, last_week)
            except (*ESPN_ERRORS, StreamerFetchError) as exc:
                await ctx.send(f"ESPN failed, nothing was posted: {exc}")
                return

        names = {team.team_id: team.team_name for team in league.teams}
        text = "\n".join(streamer_lines(league.year, last_week, names, streamed))
        for chunk in split_message(text):
            await ctx.send(chunk)
        if left_out:
            await ctx.send("Left out of the award: " + "; ".join(left_out))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Streamer(bot))
