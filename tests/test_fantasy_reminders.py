"""Tests for fantasy_reminders.py"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime
import pytest
import pytz
import requests

from cogs.fantasy_reminders import FantasyReminders, setup
from data.messages import WAIVER_REMINDER_MESSAGE


def _close_instead_of_scheduling(coro):
    """Stands in for loop.create_task. The cog's background loops never run
    in these tests, so close them rather than leave them un-awaited (which
    fills the test output with "coroutine was never awaited" warnings)."""
    if asyncio.iscoroutine(coro):
        coro.close()


@pytest.fixture(name="mock_bot")
def fixture_mock_bot():
    bot = Mock()
    bot.loop = Mock()
    bot.loop.create_task = Mock(side_effect=_close_instead_of_scheduling)
    bot.wait_until_ready = AsyncMock()
    bot.get_channel = Mock()
    bot.add_cog = AsyncMock()
    return bot


@pytest.fixture(name="mock_channel")
def fixture_mock_channel():
    channel = Mock()
    channel.send = AsyncMock()
    return channel


class TestFantasyReminders:

    def test_init(self, mock_bot):
        with patch.object(FantasyReminders, "reminder_scheduler", return_value=None):
            cog = FantasyReminders(mock_bot)

            assert cog.bot == mock_bot
            assert cog.league_tz.zone == "Europe/Oslo"
            assert cog.last_waiver_week is None

    def test_player_kickoff_normalizes_naive_datetime(self, mock_bot):
        with patch.object(FantasyReminders, "reminder_scheduler", return_value=None):
            cog = FantasyReminders(mock_bot)

        player = Mock()
        player.game_date = datetime(2024, 1, 1, 18, 0, 0)

        kickoff = cog._player_kickoff(player)

        assert kickoff is not None
        assert kickoff.tzinfo is not None
        assert kickoff.utcoffset() is not None

    def test_inactive_alert_send_time_normal_kickoff_unchanged(self, mock_bot):
        with patch.object(FantasyReminders, "reminder_scheduler", return_value=None):
            cog = FantasyReminders(mock_bot)

        tz = pytz.timezone("Europe/Oslo")
        kickoff = tz.localize(datetime(2024, 9, 8, 13, 0))

        send_time = cog._inactive_alert_send_time(kickoff)

        assert send_time == tz.localize(datetime(2024, 9, 8, 12, 0))

    def test_inactive_alert_send_time_early_morning_kickoff_clamped_to_previous_evening(
        self, mock_bot
    ):
        """E.g. an international game: warn the evening before, not at night."""
        with patch.object(FantasyReminders, "reminder_scheduler", return_value=None):
            cog = FantasyReminders(mock_bot)

        tz = pytz.timezone("Europe/Oslo")
        kickoff = tz.localize(datetime(2024, 9, 8, 2, 15))

        send_time = cog._inactive_alert_send_time(kickoff)

        assert send_time == tz.localize(datetime(2024, 9, 7, 22, 0))
        assert send_time < kickoff

    @pytest.mark.asyncio
    async def test_setup_function(self, mock_bot):
        await setup(mock_bot)
        mock_bot.add_cog.assert_called_once()

    @pytest.mark.asyncio
    async def test_final_week_standings_banner_uses_league_name_and_actual_year(
        self, mock_bot, mock_channel
    ):
        """The banner uses LEAGUE_NAME and the league's actual year."""
        team = Mock()
        team.team_name = "Test Team"
        team.wins = 10
        team.losses = 4
        team.points_for = 1234.56
        team.streak_length = 0
        team.streak_type = ""

        box = Mock()
        box.home_team = team
        box.away_team = team
        box.home_score = 100.0
        box.away_score = 90.0
        box.home_lineup = []
        box.away_lineup = []
        box.home_projected = 95.0
        box.away_projected = 95.0

        league = Mock()
        league.scoringPeriodId = 18  # Tuesday after FANTASY_FINAL_WEEK (17)
        league.year = 2031
        league.teams = [team]
        league.standings.return_value = [team]
        league.box_scores.return_value = [box]

        with patch.object(FantasyReminders, "reminder_scheduler", return_value=None):
            cog = FantasyReminders(mock_bot)

        await cog.build_matchup_digest(mock_channel, league)

        mock_channel.send.assert_called_once()
        sent_message = mock_channel.send.call_args.args[0]
        assert "**The League 2031**" in sent_message


def make_digest_team(name: str, wins: int) -> Mock:
    team = Mock()
    team.team_name = name
    team.wins = wins
    team.losses = 14 - wins
    team.points_for = 1000.0
    team.streak_length = 0
    team.streak_type = ""
    return team


def make_digest_league(scoring_period: int, teams: list) -> Mock:
    """A league as espn_api reports it on a Tuesday: scoringPeriodId is the
    week about to be played, and current_week is capped at the final
    scoring period (17)."""
    box = Mock()
    box.home_team, box.away_team = teams[0], teams[-1]
    box.home_score, box.away_score = 100.0, 90.0
    box.home_lineup = box.away_lineup = []
    box.home_projected = box.away_projected = 95.0
    league = Mock()
    league.scoringPeriodId = scoring_period
    league.current_week = min(scoring_period, 17)
    league.year = 2031
    league.teams = teams
    league.standings.return_value = teams
    league.box_scores.return_value = [box]
    return league


async def run_digest(mock_bot, mock_channel, league) -> None:
    with patch.object(FantasyReminders, "reminder_scheduler", return_value=None):
        cog = FantasyReminders(mock_bot)
    await cog.build_matchup_digest(mock_channel, league)


class TestDigestSeasonEnd:
    """The season-end digest goes out after the final, once."""

    @pytest.mark.asyncio
    async def test_tuesday_before_the_final_previews_it_instead_of_ending_season(
        self, mock_bot, mock_channel
    ):
        league = make_digest_league(17, [make_digest_team("A", 10)])

        await run_digest(mock_bot, mock_channel, league)

        sent = mock_channel.send.call_args.args[0]
        assert "Next week's games (Week 17)" in sent
        assert "**The League 2031**" not in sent

    @pytest.mark.asyncio
    async def test_nothing_posted_once_the_final_has_been_recapped(
        self, mock_bot, mock_channel
    ):
        league = make_digest_league(19, [make_digest_team("A", 10)])

        await run_digest(mock_bot, mock_channel, league)

        mock_channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_final_standings_follow_playoff_result_not_wins(
        self, mock_bot, mock_channel
    ):
        champion = make_digest_team("Champion", 8)
        best_record = make_digest_team("Best Record", 12)
        # league.standings() is espn_api's playoff-aware order.
        league = make_digest_league(18, [champion, best_record])

        await run_digest(mock_bot, mock_channel, league)

        sent = mock_channel.send.call_args.args[0]
        assert "🥇 1. Champion" in sent


class TestTuesdayMessagesSeasonGate:
    """The waiver reminder (and digest) only during the season."""

    async def send(self, mock_bot, mock_channel, scoring_period: int) -> list[str]:
        league = make_digest_league(scoring_period, [make_digest_team("A", 10)])
        with patch.object(FantasyReminders, "reminder_scheduler", return_value=None):
            cog = FantasyReminders(mock_bot)
        with patch("cogs.fantasy_reminders.get_league", return_value=league):
            await cog._send_tuesday_messages(mock_channel)
        return [c.args[0] for c in mock_channel.send.call_args_list]

    @pytest.mark.asyncio
    async def test_in_season_sends_waiver_reminder_and_digest(
        self, mock_bot, mock_channel
    ):
        sent = await self.send(mock_bot, mock_channel, 5)
        assert sent[0] == WAIVER_REMINDER_MESSAGE
        assert "Weekly recap (Week 4)" in sent[1]

    @pytest.mark.asyncio
    async def test_offseason_sends_nothing(self, mock_bot, mock_channel):
        assert await self.send(mock_bot, mock_channel, 19) == []

    @pytest.mark.asyncio
    async def test_tuesday_after_final_sends_final_digest_but_no_waivers(
        self, mock_bot, mock_channel
    ):
        sent = await self.send(mock_bot, mock_channel, 18)
        assert WAIVER_REMINDER_MESSAGE not in sent
        assert "**The League 2031**" in sent[0]

    @pytest.mark.asyncio
    async def test_before_week_one_sends_waivers_but_no_empty_recap(
        self, mock_bot, mock_channel
    ):
        assert await self.send(mock_bot, mock_channel, 1) == [WAIVER_REMINDER_MESSAGE]

    @pytest.mark.asyncio
    async def test_espn_failure_only_notifies_admin(self, mock_bot, mock_channel):
        with patch.object(FantasyReminders, "reminder_scheduler", return_value=None):
            cog = FantasyReminders(mock_bot)
        cog._notify_admin = AsyncMock()
        with patch(
            "cogs.fantasy_reminders.get_league",
            side_effect=requests.exceptions.ConnectionError("ESPN down"),
        ):
            await cog._send_tuesday_messages(mock_channel)

        mock_channel.send.assert_not_called()
        cog._notify_admin.assert_awaited_once()


@pytest.mark.asyncio
async def test_slow_espn_does_not_freeze_the_bot(mock_bot, mock_channel):
    """A slow ESPN call must not block other work while it waits."""
    import asyncio
    import time

    league = make_digest_league(5, [make_digest_team("A", 10)])
    espn_done_at: list[float] = []

    def slow_get_league():
        time.sleep(0.3)
        espn_done_at.append(time.monotonic())
        return league

    ticked_at: list[float] = []

    async def other_work():
        await asyncio.sleep(0.05)
        ticked_at.append(time.monotonic())

    with patch.object(FantasyReminders, "reminder_scheduler", return_value=None):
        cog = FantasyReminders(mock_bot)
    with patch("cogs.fantasy_reminders.get_league", side_effect=slow_get_league):
        await asyncio.gather(cog._send_tuesday_messages(mock_channel), other_work())

    assert ticked_at[0] < espn_done_at[0]


class _StopLoop(BaseException):
    """Escapes the scheduler's broad `except Exception` to end the test."""


async def run_one_tuesday_iteration(
    mock_bot, mock_channel, hour, minute, last_waiver_week=None
):
    """Runs reminder_scheduler at a given Tuesday time (Oslo) until its first
    long sleep, and returns the mocked _send_tuesday_messages."""
    import asyncio as real_asyncio

    now = pytz.timezone("Europe/Oslo").localize(datetime(2026, 9, 29, hour, minute))
    assert now.weekday() == 1  # Tuesday

    async def fake_sleep(seconds):
        raise _StopLoop

    with patch.object(FantasyReminders, "reminder_scheduler", return_value=None):
        cog = FantasyReminders(mock_bot)
    cog._send_tuesday_messages = AsyncMock()
    cog.last_waiver_week = last_waiver_week
    with (
        patch("cogs.fantasy_reminders.get_text_channel", return_value=mock_channel),
        patch("cogs.fantasy_reminders.datetime") as mock_datetime,
        patch.object(real_asyncio, "sleep", fake_sleep),
    ):
        mock_datetime.now.return_value = now
        with pytest.raises(_StopLoop):
            await cog.reminder_scheduler()
    return cog._send_tuesday_messages


class TestTuesdayRestart:
    """A restart late on a Tuesday must not re-send."""

    @pytest.mark.asyncio
    async def test_sends_shortly_after_18(self, mock_bot, mock_channel):
        send = await run_one_tuesday_iteration(mock_bot, mock_channel, 18, 30)
        send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_duplicate_when_already_sent_this_week(
        self, mock_bot, mock_channel
    ):
        # 2026-09-29 is in ISO week 40.
        send = await run_one_tuesday_iteration(
            mock_bot, mock_channel, 18, 30, last_waiver_week=40
        )
        send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_restart_late_evening_does_not_resend(self, mock_bot, mock_channel):
        send = await run_one_tuesday_iteration(mock_bot, mock_channel, 22, 0)
        send.assert_not_awaited()


@pytest.mark.asyncio
async def test_big_league_digest_is_split_under_discords_limit(mock_bot, mock_channel):
    """Discord rejects messages over 2,000 characters."""
    teams = [
        make_digest_team(f"A Rather Long Fantasy Team Name {i:02d}", 7)
        for i in range(14)
    ]
    league = make_digest_league(18, teams)  # final digest, with full standings
    boxes = []
    for home, away in zip(teams[::2], teams[1::2]):
        box = Mock()
        box.home_team, box.away_team = home, away
        box.home_score, box.away_score = 100.0, 90.0
        box.home_lineup = box.away_lineup = []
        box.home_projected = box.away_projected = 95.0
        boxes.append(box)
    league.box_scores.return_value = boxes

    await run_digest(mock_bot, mock_channel, league)

    sent = [c.args[0] for c in mock_channel.send.call_args_list]
    assert len("\n".join(sent)) > 2000  # the case this test is about
    assert len(sent) > 1
    assert all(len(m) <= 2000 for m in sent)


@pytest.mark.asyncio
async def test_inactive_check_failures_notify_admin_once_then_on_recovery(
    mock_bot, mock_channel
):
    """A lasting failure must not post to the admin channel on every retry."""
    import asyncio as real_asyncio

    league = make_digest_league(5, [make_digest_team("A", 10)])
    league.teams = []  # nothing to alert about once it works
    # Fails 3 times, then works.
    outcomes = [RuntimeError("ESPN 401")] * 3 + [league]
    sleeps = 0

    async def fake_sleep(seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 4:
            raise _StopLoop

    with patch.object(FantasyReminders, "reminder_scheduler", return_value=None):
        cog = FantasyReminders(mock_bot)
    cog._notify_admin = AsyncMock()
    with (
        patch("cogs.fantasy_reminders.load_discord_ids", return_value={}),
        patch("cogs.fantasy_reminders.get_text_channel", return_value=mock_channel),
        patch("cogs.fantasy_reminders.get_league", side_effect=outcomes),
        patch.object(real_asyncio, "sleep", fake_sleep),
    ):
        with pytest.raises(_StopLoop):
            await cog.inactive_alert_scheduler()

    messages = [c.args[0] for c in cog._notify_admin.await_args_list]
    assert len(messages) == 2
    assert "Error checking" in messages[0]
    assert "working again" in messages[1]


def make_injured_team(team_id: int, name: str) -> Mock:
    """A team with one starter who is Out, with no kickoff time known."""
    player = Mock(lineupSlot="QB", injuryStatus="OUT", playerId=1)
    player.name = "Injured Starter"
    return Mock(team_id=team_id, team_name=name, roster=[player])


async def run_inactive_check_once(mock_bot, mock_channel, load_error):
    """Runs one round of inactive_alert_scheduler with loading
    discord_ids.json failing, and returns the cog."""
    import asyncio as real_asyncio

    league = Mock(teams=[make_injured_team(1, "Aces")])

    async def fake_sleep(seconds):
        raise _StopLoop

    with patch.object(FantasyReminders, "reminder_scheduler", return_value=None):
        cog = FantasyReminders(mock_bot)
    cog._notify_admin = AsyncMock()
    cog._player_kickoff = Mock(return_value=None)
    with (
        patch("cogs.fantasy_reminders.load_discord_ids", side_effect=load_error),
        patch("cogs.fantasy_reminders.get_text_channel", return_value=mock_channel),
        patch("cogs.fantasy_reminders.get_league", return_value=league),
        patch.object(real_asyncio, "sleep", fake_sleep),
    ):
        with pytest.raises(_StopLoop):
            await cog.inactive_alert_scheduler()
    return cog


@pytest.mark.asyncio
async def test_missing_discord_ids_file_falls_back_to_everyone(mock_bot, mock_channel):
    """Without discord_ids.json, injury warnings still go out, as the
    general @everyone message."""
    cog = await run_inactive_check_once(
        mock_bot, mock_channel, FileNotFoundError("discord_ids.json")
    )

    sent = [c.args[0] for c in mock_channel.send.await_args_list]
    assert any("@everyone" in m and "Injured Starter" in m for m in sent)
    cog._notify_admin.assert_not_awaited()  # the file is optional


@pytest.mark.asyncio
async def test_broken_discord_ids_file_falls_back_and_tells_admin(
    mock_bot, mock_channel
):
    """A broken discord_ids.json falls back the same way, and the admin
    channel hears about it so the file gets fixed."""
    cog = await run_inactive_check_once(
        mock_bot, mock_channel, ValueError("Could not read JSON")
    )

    sent = [c.args[0] for c in mock_channel.send.await_args_list]
    assert any("@everyone" in m and "Injured Starter" in m for m in sent)
    cog._notify_admin.assert_awaited_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
