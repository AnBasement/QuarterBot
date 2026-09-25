"""Tests for pickem.py."""

from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta
import aiohttp
import discord
import pytest
import pytz
import gspread.exceptions
from gspread.exceptions import WorksheetNotFound

from cogs.pickem import Pickem, is_pickable_game
from core.errors import ExportError, NoEventsFoundError, SheetNotFoundError
from data.channel_ids import GAME_CHANNEL_ID
from data.teams import teams, get_team_emoji_by_name
from data.messages import THURSDAY_GAME_REMINDER_MESSAGE, SUNDAY_GAME_REMINDER_MESSAGE


def make_cog(**attrs):
    """A Pickem without running __init__, which would start the schedulers."""
    cog = Pickem.__new__(Pickem)
    cog.bot = MagicMock()
    cog.league_tz = pytz.timezone("Europe/Oslo")
    cog.last_reminder_week = None
    cog.last_reminder_sunday = None
    cog.last_posted_week = None
    cog.last_processed_week = None
    cog.state_loaded = False
    cog._state_dirty = False
    for key, value in attrs.items():
        setattr(cog, key, value)
    return cog


# Pure functions


class TestIsValidGameMessage:
    def test_valid_formats(self):
        assert Pickem.is_valid_game_message("Patriots @ Giants")
        assert Pickem.is_valid_game_message("New England Patriots @ New York Giants")
        assert Pickem.is_valid_game_message("Raiders @ 49ers")
        assert Pickem.is_valid_game_message("Patriots @ Giants <:_patriots:123456>")

    def test_accepts_unicode_fallback_emoji_for_every_team(self):
        """Without custom team emoji, game messages use Unicode ones and must
        still validate."""
        names = list(teams)
        for away, home in zip(names, names[1:] + names[:1]):
            msg = (
                f"{get_team_emoji_by_name(None, away)} {away} @ "
                f"{home} {get_team_emoji_by_name(None, home)}"
            )
            assert Pickem.is_valid_game_message(msg), msg

    def test_accepts_result_format(self):
        assert Pickem.is_valid_game_message("TeamA - TeamB: 24-17")

    def test_rejects_mentions(self):
        assert not Pickem.is_valid_game_message("Patriots @ Giants <@123456>")
        assert not Pickem.is_valid_game_message("@everyone Patriots @ Giants")
        assert not Pickem.is_valid_game_message("@here Patriots @ Giants")

    def test_rejects_wrong_format(self):
        assert not Pickem.is_valid_game_message("Patriots Giants")
        assert not Pickem.is_valid_game_message("Patriots vs Giants")
        assert not Pickem.is_valid_game_message("")


class TestFormatEvent:
    def test_from_espn_competitions_structure(self):
        cog = make_cog()
        ev = {
            "competitions": [
                {
                    "competitors": [
                        {"homeAway": "home", "team": {"displayName": "Giants"}},
                        {"homeAway": "away", "team": {"displayName": "Patriots"}},
                    ]
                }
            ]
        }
        assert "Patriots @ Giants" in cog._format_event(ev, None)


class TestGetManagers:
    def test_maps_discord_ids_to_columns(self):
        sheet = MagicMock()
        sheet.row_values.return_value = ["", "id1", "id2", ""]
        cog = make_cog()
        assert cog.get_managers(sheet) == {"id1": 1, "id2": 2}

    def test_empty_row_gives_empty_mapping(self):
        sheet = MagicMock()
        sheet.row_values.return_value = ["", "", "", ""]
        cog = make_cog()
        assert cog.get_managers(sheet) == {}


# Export


@pytest.mark.asyncio
async def test_export_raises_when_no_bot_messages(monkeypatch):
    monkeypatch.setattr("cogs.pickem.get_sheet", lambda name: MagicMock())

    cog = make_cog()
    ctx = MagicMock()
    ctx.channel.guild = None

    async def empty_history(*args, **kwargs):
        return
        yield  # make the function an (empty) async generator

    ctx.channel.history.return_value = empty_history()

    with pytest.raises(ExportError):
        await cog._export_impl(ctx)


@pytest.mark.asyncio
async def test_export_writes_valid_messages_to_sheet(monkeypatch):
    sheet = MagicMock()
    sheet.row_values.return_value = ["", "111", "222"]
    sheet.get_all_values.return_value = [["", "111", "222"], ["", "111", "222"]]
    monkeypatch.setattr("cogs.pickem.get_sheet", lambda name: sheet)

    now = datetime.now(pytz.timezone("Europe/Oslo"))
    bot_user = MagicMock()
    valid_msg = MagicMock(
        content="Patriots @ Giants", author=bot_user, created_at=now, reactions=[]
    )

    async def history(*args, **kwargs):
        yield valid_msg

    cog = make_cog()
    cog.bot.user = bot_user
    ctx = MagicMock()
    ctx.channel.history = history
    ctx.channel.guild = None
    ctx.send = AsyncMock()

    await cog._export_impl(ctx)

    assert sheet.update_cells.called
    ctx.send.assert_awaited_with("Game data exported to Sheets.")


# Reminder scheduler


@pytest.mark.asyncio
async def test_reminder_scheduler_thursday(monkeypatch):
    # spec= makes it pass reminder_scheduler()'s TextChannel check.
    channel = MagicMock(spec=discord.TextChannel)
    channel.sent = []

    async def _record_send(msg):
        channel.sent.append(msg)

    channel.send = AsyncMock(side_effect=_record_send)

    bot = MagicMock()
    bot.wait_until_ready = AsyncMock()
    bot.get_channel.return_value = channel

    cog = make_cog(bot=bot)

    sleep_calls = {"n": 0}

    async def fast_sleep(_):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            raise SystemExit()

    monkeypatch.setattr("cogs.pickem.asyncio.sleep", fast_sleep)

    fixed_now = cog.league_tz.localize(datetime(2024, 9, 5, 17, 50))  # Thursday
    from cogs import pickem as vt_mod

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

        @classmethod
        def fromisoformat(cls, s):
            return datetime.fromisoformat(s)

    monkeypatch.setattr(vt_mod, "datetime", FixedDateTime)

    # A real TNF-style kickoff: 2024-09-05 20:15 America/New_York (Thursday
    # ET), which is already 2024-09-06 02:15 Europe/Oslo (Friday local).
    # ISO date below is that same instant in UTC (EDT is UTC-4 in September).
    class DummyAiohttpResponse:
        async def json(self):
            return {"events": [{"date": "2024-09-06T00:15Z"}]}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

    class DummyAiohttpSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

        def get(self, url, *args, **kwargs):
            return DummyAiohttpResponse()

    monkeypatch.setattr(
        "cogs.pickem.aiohttp.ClientSession",
        lambda *a, **kw: DummyAiohttpSession(),
    )

    with pytest.raises(SystemExit):
        await cog.reminder_scheduler()

    expected = THURSDAY_GAME_REMINDER_MESSAGE.format(channel=GAME_CHANNEL_ID)
    assert any(expected == m for m in channel.sent)
    assert cog.last_reminder_week == fixed_now.isocalendar()[1]


@pytest.mark.asyncio
async def test_reminder_scheduler_sunday(monkeypatch):
    # spec= makes it pass reminder_scheduler()'s TextChannel check.
    channel = MagicMock(spec=discord.TextChannel)
    channel.sent = []

    async def _record_send(msg):
        channel.sent.append(msg)

    channel.send = AsyncMock(side_effect=_record_send)

    bot = MagicMock()
    bot.wait_until_ready = AsyncMock()
    bot.get_channel.return_value = channel

    cog = make_cog(bot=bot)

    sleep_calls = {"n": 0}

    async def fast_sleep(_):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            raise SystemExit()

    monkeypatch.setattr("cogs.pickem.asyncio.sleep", fast_sleep)

    fixed_now = cog.league_tz.localize(datetime(2024, 9, 8, 17, 55))
    from cogs import pickem as vt_mod

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

        @classmethod
        def fromisoformat(cls, s):
            return datetime.fromisoformat(s)

    monkeypatch.setattr(vt_mod, "datetime", FixedDateTime)

    class DummyAiohttpResponse:
        async def json(self):
            return {
                "events": [
                    {
                        "date": "2024-09-08T17:00:00Z",
                        "competitions": [
                            {
                                "competitors": [
                                    {
                                        "homeAway": "home",
                                        "team": {"displayName": "New York Giants"},
                                    },
                                    {
                                        "homeAway": "away",
                                        "team": {"displayName": "New England Patriots"},
                                    },
                                ]
                            }
                        ],
                    }
                ]
            }

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

    class DummyAiohttpSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

        def get(self, url, *args, **kwargs):
            return DummyAiohttpResponse()

    monkeypatch.setattr(
        "cogs.pickem.aiohttp.ClientSession",
        lambda *a, **kw: DummyAiohttpSession(),
    )

    with pytest.raises(SystemExit):
        await cog.reminder_scheduler()

    expected = SUNDAY_GAME_REMINDER_MESSAGE.format(channel=GAME_CHANNEL_ID)
    assert any(expected == m for m in channel.sent)
    assert cog.last_reminder_sunday is not None


# Reminder helpers


class TestCheckThursdayReminder:
    @pytest.mark.asyncio
    async def test_returns_false_when_not_thursday(self, monkeypatch):
        cog = make_cog()
        channel = MagicMock()
        # Monday: should not do anything, and never sleep
        now = cog.league_tz.localize(datetime(2024, 9, 2, 12, 0))
        sleep_calls = []
        monkeypatch.setattr(
            "cogs.pickem.asyncio.sleep",
            lambda s: sleep_calls.append(s),
        )
        result = await cog._check_thursday_reminder(channel, now, weekday=0)
        assert result is False
        assert sleep_calls == []

    @pytest.mark.asyncio
    async def test_runs_on_wednesday_for_timezones_behind_eastern(self, monkeypatch):
        """Behind US Eastern (Los Angeles) the reminder is at 15:15 Thursday, so
        checking must already start on Wednesday."""
        cog = make_cog(league_tz=pytz.timezone("America/Los_Angeles"))
        channel = MagicMock()
        channel.send = AsyncMock()
        now = cog.league_tz.localize(datetime(2024, 9, 4, 8, 0))  # Wednesday

        sleep_calls = []

        async def fake_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr("cogs.pickem.asyncio.sleep", fake_sleep)

        async def fetch_stub(target_weekday):
            assert target_weekday == 3
            # 20:15 in New York is 17:15 in Los Angeles: reminder at 15:15 Thursday.
            return [{"date": "2024-09-06T00:15Z"}]

        cog._fetch_events_for_nfl_weekday = fetch_stub

        result = await cog._check_thursday_reminder(channel, now, weekday=2)

        assert result is True
        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_time_already_passed(self, monkeypatch):
        cog = make_cog()
        channel = MagicMock()
        # Thursday, but the reminder time (kickoff 20:00 Oslo minus 2h, 18:00) has
        # passed by 19:00.
        now = cog.league_tz.localize(datetime(2024, 9, 5, 19, 0))
        sleep_calls = []
        monkeypatch.setattr(
            "cogs.pickem.asyncio.sleep",
            lambda s: sleep_calls.append(s),
        )

        async def fetch_stub(_target_weekday):
            return [{"date": "2024-09-05T18:00Z"}]  # kickoff 20:00 Oslo

        cog._fetch_events_for_nfl_weekday = fetch_stub

        result = await cog._check_thursday_reminder(channel, now, weekday=3)
        assert result is False
        assert sleep_calls == []
        channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_false_when_no_thursday_game_found(self):
        cog = make_cog()
        channel = MagicMock()
        now = cog.league_tz.localize(datetime(2024, 9, 5, 12, 0))

        async def empty_fetch(_target_weekday):
            return []

        cog._fetch_events_for_nfl_weekday = empty_fetch

        result = await cog._check_thursday_reminder(channel, now, weekday=3)
        assert result is False

    @pytest.mark.asyncio
    async def test_fetch_identifies_thursday_game_by_eastern_weekday_not_local(
        self, monkeypatch
    ):
        """A Thursday game in US Eastern time that's already Friday in Oslo is
        still the Thursday game; a Sunday game is not."""
        cog = make_cog()

        class DummyResponse:
            async def json(self):
                return {
                    "events": [
                        # 20:15 Thursday in New York is 02:15 Friday in Oslo.
                        {"date": "2024-09-06T00:15Z"},
                        # A Sunday game: not a Thursday game.
                        {"date": "2024-09-08T17:00:00Z"},
                    ]
                }

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

        class DummySession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

            def get(self, url, *args, **kwargs):
                return DummyResponse()

        monkeypatch.setattr(
            "cogs.pickem.aiohttp.ClientSession",
            lambda *a, **kw: DummySession(),
        )

        result = await cog._fetch_events_for_nfl_weekday(3)

        assert result == [{"date": "2024-09-06T00:15Z"}]

    @pytest.mark.asyncio
    async def test_reminder_time_clamped_out_of_quiet_hours(self, monkeypatch):
        """If the reminder time (kickoff minus the buffer) lands after midnight,
        it moves back to the start of quiet hours (22:00) the evening before.
        Oslo's TNF kickoff at 02:15 Friday would otherwise put it at 00:15."""

        cog = make_cog()
        channel = MagicMock()
        channel.send = AsyncMock()
        now = cog.league_tz.localize(datetime(2024, 9, 5, 17, 50))  # Thursday

        sleep_calls = []

        async def fake_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr("cogs.pickem.asyncio.sleep", fake_sleep)

        async def fetch_stub(target_weekday):
            assert target_weekday == 3
            # 20:15 Thursday in New York is 02:15 Friday in Oslo.
            return [{"date": "2024-09-06T00:15Z"}]

        cog._fetch_events_for_nfl_weekday = fetch_stub

        result = await cog._check_thursday_reminder(channel, now, weekday=3)

        assert result is True
        channel.send.assert_awaited_once()
        # Unclamped 00:15 Friday; clamped 22:00 Thursday, 4h10m after 17:50.
        assert sleep_calls[0] == pytest.approx(4 * 3600 + 10 * 60, abs=1)

    @pytest.mark.asyncio
    async def test_reminder_lands_before_kickoff_for_timezone_behind_eastern(
        self, monkeypatch
    ):
        """Behind US Eastern (Los Angeles), the reminder lands before kickoff."""
        cog = make_cog(league_tz=pytz.timezone("America/Los_Angeles"))
        channel = MagicMock()
        channel.send = AsyncMock()
        now = cog.league_tz.localize(datetime(2024, 9, 5, 12, 0))  # Thursday

        sleep_calls = []

        async def fake_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr("cogs.pickem.asyncio.sleep", fake_sleep)

        async def fetch_stub(target_weekday):
            assert target_weekday == 3
            # 20:15 in New York is 17:15 in Los Angeles, the same day.
            return [{"date": "2024-09-06T00:15Z"}]

        cog._fetch_events_for_nfl_weekday = fetch_stub

        result = await cog._check_thursday_reminder(channel, now, weekday=3)

        assert result is True
        channel.send.assert_awaited_once()
        # 15:15 Los Angeles (kickoff minus 2h), 3h15m after 12:00.
        assert sleep_calls[0] == pytest.approx(3 * 3600 + 15 * 60, abs=1)


class TestCheckSundayReminder:
    @pytest.mark.asyncio
    async def test_returns_false_when_not_sunday(self):
        cog = make_cog()
        channel = MagicMock()
        now = cog.league_tz.localize(datetime(2024, 9, 5, 12, 0))  # Thursday
        result = await cog._check_sunday_reminder(channel, now, weekday=3)
        assert result is False

    @pytest.mark.asyncio
    async def test_runs_on_saturday_for_timezones_far_behind_eastern(self, monkeypatch):
        """Far behind US Eastern (Honolulu), the Sunday reminder falls on
        Saturday evening, so checking must start on Saturday."""
        cog = make_cog(league_tz=pytz.timezone("Pacific/Honolulu"))
        channel = MagicMock()
        channel.send = AsyncMock()
        now = cog.league_tz.localize(datetime(2024, 9, 7, 8, 0))  # Saturday

        sleep_calls = []

        async def fake_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr("cogs.pickem.asyncio.sleep", fake_sleep)

        async def fetch_stub(target_weekday):
            assert target_weekday == 6
            # 2024-09-08 13:00 America/New_York == 2024-09-08 07:00
            # Pacific/Honolulu: natural reminder (kickoff minus 1h) = 06:00,
            # clamped back to 2024-09-07 22:00 (Saturday evening).
            return [{"date": "2024-09-08T17:00:00Z"}]

        cog._fetch_events_for_nfl_weekday = fetch_stub

        result = await cog._check_sunday_reminder(channel, now, weekday=5)

        assert result is True
        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reminder_time_clamped_out_of_quiet_hours(self, monkeypatch):
        """A late Sunday kickoff's reminder is moved out of quiet hours."""
        cog = make_cog()
        channel = MagicMock()
        channel.send = AsyncMock()
        now = cog.league_tz.localize(datetime(2024, 9, 8, 20, 0))  # Sunday

        sleep_calls = []

        async def fake_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr("cogs.pickem.asyncio.sleep", fake_sleep)

        async def fetch_stub(target_weekday):
            assert target_weekday == 6
            # Kickoff 2024-09-08 23:30 Europe/Oslo -> natural reminder
            # (kickoff minus 1h) = 22:30, itself inside quiet hours.
            return [{"date": "2024-09-08T21:30Z"}]

        cog._fetch_events_for_nfl_weekday = fetch_stub

        result = await cog._check_sunday_reminder(channel, now, weekday=6)

        assert result is True
        channel.send.assert_awaited_once()
        # Clamped reminder time should be 2024-09-08 22:00 Oslo, 2h after
        # "now" (20:00), not the unclamped 22:30 (2h30m after "now").
        assert sleep_calls[0] == pytest.approx(2 * 3600, abs=1)

    @pytest.mark.asyncio
    async def test_returns_true_when_fetch_signals_failure(self, monkeypatch):
        """A failed fetch (None) counts as handled, with no message. The backoff
        itself is tested below."""
        cog = make_cog()
        channel = MagicMock()
        now = cog.league_tz.localize(datetime(2024, 9, 8, 12, 0))  # Sunday

        async def failing_fetch(_target_weekday):
            return None  # how _fetch_events_for_nfl_weekday signals failure

        cog._fetch_events_for_nfl_weekday = failing_fetch

        result = await cog._check_sunday_reminder(channel, now, weekday=6)
        assert result is True
        channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_events_for_nfl_weekday_backs_off_300s_on_failure(
        self, monkeypatch
    ):
        """A failed ESPN call backs off 300 seconds, then returns None."""
        cog = make_cog()

        class FailingSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

            def get(self, url, *args, **kwargs):
                raise aiohttp.ClientConnectionError("network error")

        monkeypatch.setattr(
            "cogs.pickem.aiohttp.ClientSession",
            lambda *a, **kw: FailingSession(),
        )

        sleep_calls = []

        async def fake_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr("cogs.pickem.asyncio.sleep", fake_sleep)

        result = await cog._fetch_events_for_nfl_weekday(6)

        assert result is None
        assert sleep_calls == [300]

    @pytest.mark.asyncio
    async def test_returns_false_when_no_sunday_games_found(self):
        cog = make_cog()
        channel = MagicMock()
        now = cog.league_tz.localize(datetime(2024, 9, 8, 12, 0))

        async def empty_fetch(_target_weekday):
            return []

        cog._fetch_events_for_nfl_weekday = empty_fetch

        result = await cog._check_sunday_reminder(channel, now, weekday=6)
        assert result is False


class TestNextReminderWindowSeconds:
    def test_picks_the_closer_of_thursday_or_sunday(self):
        cog = make_cog()
        # Monday 12:00: the nearest window is Wednesday 08:00.
        now = cog.league_tz.localize(datetime(2024, 9, 2, 12, 0))
        seconds = cog._next_reminder_window_seconds(now, weekday=0)
        expected = cog.league_tz.localize(datetime(2024, 9, 4, 8, 0))
        assert seconds == pytest.approx((expected - now).total_seconds())

    def test_rolls_over_to_next_week_when_both_windows_passed(self):
        cog = make_cog()
        # Sunday 23:00: both have passed, so next Wednesday (before next Saturday).
        now = cog.league_tz.localize(datetime(2024, 9, 8, 23, 0))
        seconds = cog._next_reminder_window_seconds(now, weekday=6)
        expected = cog.league_tz.localize(datetime(2024, 9, 11, 8, 0))
        assert seconds == pytest.approx((expected - now).total_seconds())


class TestSeasonWindow:
    """_season_window() must attach the timezone with tz.localize(...), not
    datetime(..., tzinfo=tz). The latter is a known pytz pitfall that gives
    an unusable +00:53 offset (Oslo's historical "Local Mean Time") instead
    of the correct +01:00/+02:00."""

    def test_season_end_has_correct_winter_offset(self):
        """Season end (February) should have winter time (+01:00), never +00:53."""
        cog = make_cog()
        now = cog.league_tz.localize(datetime(2024, 10, 1, 12, 0))
        _, season_end, _ = cog._season_window(now)
        assert season_end is not None
        assert season_end.utcoffset() == timedelta(hours=1)

    def test_next_season_start_has_correct_summer_offset(self):
        """Season start (September) should have summer time (+02:00), never +00:53."""
        cog = make_cog()
        now = cog.league_tz.localize(datetime(2024, 3, 1, 12, 0))
        _, _, next_start = cog._season_window(now)
        assert next_start is not None
        assert next_start.utcoffset() == timedelta(hours=2)

    def test_mid_october_is_in_season(self):
        """The 2026 season ends the Tuesday after Super Bowl LXI (Feb 14, 2027)."""
        cog = make_cog()
        now = cog.league_tz.localize(datetime(2026, 10, 15, 12, 0))
        in_season, season_end, _ = cog._season_window(now)
        assert in_season is True
        assert season_end is not None
        assert season_end.replace(tzinfo=None) == datetime(2027, 2, 16, 23, 59, 59)

    def test_january_is_in_last_years_season(self):
        """January belongs to the season that started the previous September."""
        cog = make_cog()
        now = cog.league_tz.localize(datetime(2027, 1, 20, 12, 0))
        in_season, season_end, _ = cog._season_window(now)
        assert in_season is True
        assert season_end is not None
        assert season_end.replace(tzinfo=None) == datetime(2027, 2, 16, 23, 59, 59)

    def test_june_is_off_season(self):
        """In June the next season starts the Wednesday after Labor Day
        (Sep 7, 2026)."""
        cog = make_cog()
        now = cog.league_tz.localize(datetime(2026, 6, 15, 12, 0))
        in_season, _, next_start = cog._season_window(now)
        assert in_season is False
        assert next_start is not None
        assert next_start.replace(tzinfo=None) == datetime(2026, 9, 9, 0, 0)


# State persistence


class TestLoadState:
    @pytest.mark.asyncio
    async def test_loads_existing_values(self):
        cog = make_cog()
        state_ws = MagicMock()
        state_ws.get.return_value = [["3", "4"]]
        cog._get_state_sheet = AsyncMock(return_value=state_ws)

        await cog._load_state()

        assert cog.last_processed_week == 3
        assert cog.last_posted_week == 4
        assert cog.state_loaded is True

    @pytest.mark.asyncio
    async def test_preserves_zero_as_a_real_value(self):
        """The number 0 should be loaded as 0, not confused with 'no value'."""
        cog = make_cog()
        state_ws = MagicMock()
        state_ws.get.return_value = [["0", "1"]]
        cog._get_state_sheet = AsyncMock(return_value=state_ws)

        await cog._load_state()

        assert cog.last_processed_week == 0
        assert cog.last_posted_week == 1
        assert cog.state_loaded is True

    @pytest.mark.asyncio
    async def test_first_ever_run_with_no_data_is_not_an_error(self):
        cog = make_cog()
        state_ws = MagicMock()
        state_ws.get.return_value = []
        cog._get_state_sheet = AsyncMock(return_value=state_ws)

        await cog._load_state()

        assert cog.last_processed_week is None
        assert cog.last_posted_week is None
        assert cog.state_loaded is True

    @pytest.mark.asyncio
    async def test_corrupt_data_is_not_loaded_and_admin_notified(self):
        cog = make_cog()
        state_ws = MagicMock()
        state_ws.get.return_value = [["not-a-number", "4"]]
        cog._get_state_sheet = AsyncMock(return_value=state_ws)
        cog._notify_admin = AsyncMock()

        await cog._load_state()

        assert cog.state_loaded is False
        cog._notify_admin.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sheet_error_leaves_state_unloaded_for_retry(self):
        cog = make_cog()
        cog._get_state_sheet = AsyncMock(
            side_effect=gspread.exceptions.GSpreadException("network error")
        )
        cog._notify_admin = AsyncMock()

        await cog._load_state()

        assert cog.state_loaded is False
        cog._notify_admin.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_spreadsheet_leaves_state_unloaded_for_retry(
        self, monkeypatch
    ):
        """A wrong sheet name must not stop the auto-post scheduler for good."""
        monkeypatch.setattr(
            "cogs.pickem.get_sheet",
            MagicMock(side_effect=SheetNotFoundError("Pick'em", 0, "not found")),
        )
        cog = make_cog()
        cog._notify_admin = AsyncMock()

        await cog._load_state()

        assert cog.state_loaded is False
        cog._notify_admin.assert_awaited_once()


class TestSaveState:
    @pytest.mark.asyncio
    async def test_does_nothing_if_state_never_loaded(self):
        cog = make_cog(state_loaded=False)
        cog._get_state_sheet = AsyncMock()

        saved = await cog._save_state()

        assert saved is False
        cog._get_state_sheet.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_writes_values_and_clears_dirty_flag(self):
        state_ws = MagicMock()
        cog = make_cog(
            state_loaded=True,
            last_processed_week=0,
            last_posted_week=7,
            _state_dirty=True,
        )
        cog._get_state_sheet = AsyncMock(return_value=state_ws)

        saved = await cog._save_state()

        assert saved is True
        assert cog._state_dirty is False
        # gspread 6.x's update() takes (values, range_name), the values are
        # the first positional argument, not the second.
        written_values = state_ws.update.call_args.args[0]
        assert written_values == [[0, 7]]  # 0 should be written as 0, not ""

    @pytest.mark.asyncio
    async def test_failure_sets_dirty_flag_and_notifies_admin(self):
        cog = make_cog(state_loaded=True, last_processed_week=3, last_posted_week=3)
        cog._get_state_sheet = AsyncMock(
            side_effect=gspread.exceptions.GSpreadException("Sheets down")
        )
        cog._notify_admin = AsyncMock()

        saved = await cog._save_state()

        assert saved is False
        assert cog._state_dirty is True
        cog._notify_admin.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_spreadsheet_sets_dirty_flag_and_notifies_admin(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            "cogs.pickem.get_sheet",
            MagicMock(side_effect=SheetNotFoundError("Pick'em", 0, "not found")),
        )
        cog = make_cog(state_loaded=True, last_processed_week=3, last_posted_week=3)
        cog._notify_admin = AsyncMock()

        saved = await cog._save_state()

        assert saved is False
        assert cog._state_dirty is True
        cog._notify_admin.assert_awaited_once()


class TestProcessPreviousWeek:
    @pytest.mark.asyncio
    async def test_notifies_admin_when_export_step_fails(self):
        cog = make_cog(last_processed_week=2)
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        cog._fetch_week_events = AsyncMock(return_value=[{"id": "1"}])
        cog._export_impl = AsyncMock(side_effect=RuntimeError("Sheets down"))
        cog._results_impl = AsyncMock()
        cog._save_state = AsyncMock(return_value=True)
        cog._notify_admin = AsyncMock()

        result = await cog._process_previous_week(current_week=4, channel=channel)

        assert result is False
        cog._notify_admin.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_week_without_pickable_games_is_skipped_not_retried(self):
        """The Pro Bowl week has no games to pick. It must be marked processed,
        or it would retry forever and block the Super Bowl."""
        cog = make_cog(last_processed_week=21)
        channel = MagicMock(spec=discord.TextChannel)
        cog._fetch_week_events = AsyncMock(side_effect=NoEventsFoundError(22))
        cog._export_impl = AsyncMock()
        cog._results_impl = AsyncMock()
        cog._save_state = AsyncMock(return_value=True)

        result = await cog._process_previous_week(current_week=23, channel=channel)

        assert result is True
        assert cog.last_processed_week == 22
        cog._export_impl.assert_not_awaited()


class TestFirstRun:
    """A fresh install mid-season must not get stuck trying
    to process a week it never posted."""

    # Tuesday 2026-10-06, 21:00 Oslo: past the Tuesday 20:00 processing time.
    TUESDAY_EVENING = pytz.timezone("Europe/Oslo").localize(
        datetime(2026, 10, 6, 21, 0)
    )

    @pytest.mark.asyncio
    async def test_empty_state_starts_from_the_current_week(self):
        cog = make_cog(state_loaded=True, last_processed_week=None)
        cog._save_state = AsyncMock(return_value=True)

        await cog._start_from_current_week_if_first_run(5)

        assert cog.last_processed_week == 4
        cog._save_state.assert_awaited_once()

        assert not cog._should_process_previous_week(self.TUESDAY_EVENING, 5)

    @pytest.mark.asyncio
    async def test_first_run_in_week_one_uses_zero(self):
        cog = make_cog(state_loaded=True, last_processed_week=None)
        cog._save_state = AsyncMock(return_value=True)

        await cog._start_from_current_week_if_first_run(1)

        assert cog.last_processed_week == 0

    @pytest.mark.asyncio
    async def test_existing_state_is_left_alone(self):
        cog = make_cog(state_loaded=True, last_processed_week=3)
        cog._save_state = AsyncMock()

        await cog._start_from_current_week_if_first_run(5)

        assert cog.last_processed_week == 3
        cog._save_state.assert_not_awaited()


class TestSuperBowlProcessing:
    """Nothing comes after the Super Bowl, so it can't wait
    for a "next week" like every other week does."""

    OSLO = pytz.timezone("Europe/Oslo")
    # Super Bowl LX was Sunday 2026-02-08; Tuesday after, 21:00 Oslo.
    TUESDAY_AFTER = OSLO.localize(datetime(2026, 2, 10, 21, 0))

    @staticmethod
    def super_bowl(completed: bool) -> list[dict]:
        return [{"status": {"type": {"completed": completed}}}]

    @pytest.mark.asyncio
    async def test_ready_once_played_and_processing_time_has_come(self):
        cog = make_cog(last_processed_week=22)
        cog._fetch_week_events = AsyncMock(return_value=self.super_bowl(True))

        assert await cog._super_bowl_needs_processing(self.TUESDAY_AFTER)
        cog._fetch_week_events.assert_awaited_once_with(23)

    @pytest.mark.asyncio
    async def test_not_ready_before_the_game_is_over(self):
        cog = make_cog(last_processed_week=22)
        cog._fetch_week_events = AsyncMock(return_value=self.super_bowl(False))

        assert not await cog._super_bowl_needs_processing(self.TUESDAY_AFTER)

    @pytest.mark.asyncio
    async def test_not_ready_before_tuesday_evening(self):
        cog = make_cog(last_processed_week=22)
        cog._fetch_week_events = AsyncMock(return_value=self.super_bowl(True))
        monday = self.OSLO.localize(datetime(2026, 2, 9, 21, 0))

        assert not await cog._super_bowl_needs_processing(monday)

    @pytest.mark.asyncio
    async def test_not_again_once_processed(self):
        cog = make_cog(last_processed_week=23)
        cog._fetch_week_events = AsyncMock(return_value=self.super_bowl(True))

        assert not await cog._super_bowl_needs_processing(self.TUESDAY_AFTER)
        cog._fetch_week_events.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_regular_season_weeks_never_trigger_it(self):
        cog = make_cog(last_processed_week=5)
        cog._fetch_week_events = AsyncMock()

        assert not await cog._super_bowl_needs_processing(self.TUESDAY_AFTER)
        cog._fetch_week_events.assert_not_awaited()


class TestIsPickableGame:
    """The Pro Bowl must never be posted as a game to pick."""

    @staticmethod
    def event(away: str, home: str) -> dict:
        return {
            "competitions": [
                {
                    "competitors": [
                        {"homeAway": "home", "team": {"displayName": home}},
                        {"homeAway": "away", "team": {"displayName": away}},
                    ]
                }
            ]
        }

    def test_pro_bowl_is_not_pickable(self):
        # The real event ESPN returns for playoff week 4.
        assert not is_pickable_game(self.event("NFC", "AFC"))

    def test_real_game_is_pickable(self):
        assert is_pickable_game(self.event("Seattle Seahawks", "New England Patriots"))

    def test_unreadable_event_is_kept(self):
        assert is_pickable_game({"date": "2024-09-06T00:15Z"})


class TestNotifyAdmin:
    @pytest.mark.asyncio
    async def test_swallows_errors_from_discord_send(self):
        """A failed notification should never propagate the exception to the caller."""
        cog = make_cog()
        admin_channel = MagicMock()
        admin_channel.send = AsyncMock(side_effect=RuntimeError("Discord down"))
        cog._admin_channel = lambda: admin_channel

        await cog._notify_admin("test")  # should not raise

    @pytest.mark.asyncio
    async def test_does_nothing_without_admin_channel_configured(self):
        cog = make_cog()
        cog._admin_channel = lambda: None

        await cog._notify_admin("test")  # should not raise


class TestFlushPendingState:
    @pytest.mark.asyncio
    async def test_noop_when_nothing_pending(self):
        cog = make_cog(_state_dirty=False)
        cog._save_state = AsyncMock()

        await cog._flush_pending_state()

        cog._save_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_retries_failed_save_and_clears_dirty_only_on_success(self):
        """A failed save is retried next round, and the dirty flag clears only
        once it succeeds."""
        cog = make_cog(state_loaded=True, last_processed_week=7, last_posted_week=7)

        state_ws = MagicMock()
        state_ws.update.side_effect = [
            gspread.exceptions.GSpreadException("Sheets API down"),
            None,
        ]
        cog._get_state_sheet = AsyncMock(return_value=state_ws)
        cog._notify_admin = AsyncMock()

        # First save attempt (e.g. from _process_previous_week) fails
        first_attempt = await cog._save_state()
        assert first_attempt is False
        assert cog._state_dirty is True

        # Next scheduler round calls _flush_pending_state()
        await cog._flush_pending_state()

        assert cog._state_dirty is False
        assert state_ws.update.call_count == 2

    @pytest.mark.asyncio
    async def test_stays_dirty_if_retry_also_fails(self):
        cog = make_cog(state_loaded=True, _state_dirty=True)
        cog._get_state_sheet = AsyncMock(
            side_effect=gspread.exceptions.GSpreadException("still down")
        )
        cog._notify_admin = AsyncMock()

        await cog._flush_pending_state()

        assert cog._state_dirty is True


class TestGetStateSheet:
    @pytest.mark.asyncio
    async def test_creates_state_worksheet_when_missing(self, monkeypatch):
        base_sheet = MagicMock()
        spreadsheet = MagicMock()
        new_ws = MagicMock()
        base_sheet.spreadsheet = spreadsheet
        spreadsheet.worksheet.side_effect = WorksheetNotFound("State")
        spreadsheet.add_worksheet.return_value = new_ws
        monkeypatch.setattr("cogs.pickem.get_sheet", lambda name: base_sheet)

        cog = make_cog()
        result = await cog._get_state_sheet()

        assert result is new_ws
        new_ws.update.assert_called_once_with(
            [["last_processed_week", "last_posted_week"]], "A1:B1"
        )

    @pytest.mark.asyncio
    async def test_reuses_existing_state_worksheet(self, monkeypatch):
        base_sheet = MagicMock()
        spreadsheet = MagicMock()
        existing_ws = MagicMock()
        base_sheet.spreadsheet = spreadsheet
        spreadsheet.worksheet.return_value = existing_ws
        monkeypatch.setattr("cogs.pickem.get_sheet", lambda name: base_sheet)

        cog = make_cog()
        result = await cog._get_state_sheet()

        assert result is existing_ws
        spreadsheet.add_worksheet.assert_not_called()


# auto_post_scheduler


@pytest.mark.asyncio
async def test_scheduler_flushes_pending_state_before_guards(monkeypatch):
    """A failed save is retried at the start of each round, before the week
    checks."""
    cog = make_cog(
        state_loaded=True,
        last_processed_week=5,
        last_posted_week=5,
        _state_dirty=True,
    )
    cog.bot.wait_until_ready = AsyncMock()
    cog.bot.get_channel.return_value = MagicMock()

    flush_calls = []

    async def fake_flush():
        flush_calls.append(1)
        cog._state_dirty = False

    cog._flush_pending_state = fake_flush

    # Stop right after the flush; the rest of the round isn't tested here.
    from cogs import pickem as vt_mod

    class ExplodingDatetime:
        @classmethod
        def now(cls, tz=None):
            raise SystemExit()

    monkeypatch.setattr(vt_mod, "datetime", ExplodingDatetime)

    with pytest.raises(SystemExit):
        await cog.auto_post_scheduler()

    assert flush_calls == [1]
