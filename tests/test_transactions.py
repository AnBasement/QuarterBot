"""Tests for cogs/transactions.py."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs import transactions
from cogs.transactions import (
    CHECK_SECONDS,
    ERROR_BACKOFF_SECONDS,
    Transactions,
    format_activity,
    new_activities,
)

ACES = SimpleNamespace(team_name="Aces")
BOMBERS = SimpleNamespace(team_name="Bombers")


def player(name):
    return SimpleNamespace(name=name)


def activity(date, *actions):
    return SimpleNamespace(date=date, actions=list(actions))


# Formatting


def test_free_agent_add():
    move = activity(1, (ACES, "FA ADDED", player("Jaxson Dart"), 0))

    assert format_activity(move) == "➕ Aces added Jaxson Dart"


def test_waiver_claim():
    move = activity(1, (ACES, "WAIVER ADDED", player("Jaxson Dart"), 0))

    assert format_activity(move) == "➕ Aces claimed Jaxson Dart off waivers"


def test_drop():
    move = activity(1, (ACES, "DROPPED", player("Tyler Allgeier"), 0))

    assert format_activity(move) == "➖ Aces dropped Tyler Allgeier"


def test_add_and_drop_are_one_message():
    move = activity(
        1,
        (ACES, "FA ADDED", player("Jaxson Dart"), 0),
        (ACES, "DROPPED", player("Tyler Allgeier"), 0),
    )

    assert format_activity(move) == (
        "➕ Aces added Jaxson Dart\n➖ Aces dropped Tyler Allgeier"
    )


def test_trade_lists_what_each_team_receives_once():
    allen, hill, bijan = player("Josh Allen"), player("Tyreek Hill"), player("Bijan")
    move = activity(
        1,
        (ACES, "TRADE_SENT", allen, 0),
        (BOMBERS, "TRADE_RECEIVED", allen, 0),
        (ACES, "TRADE_SENT", hill, 0),
        (BOMBERS, "TRADE_RECEIVED", hill, 0),
        (BOMBERS, "TRADE_SENT", bijan, 0),
        (ACES, "TRADE_RECEIVED", bijan, 0),
    )

    assert format_activity(move) == (
        "🔁 Trade:\nBombers receives Josh Allen, Tyreek Hill\nAces receives Bijan"
    )


def test_unknown_move_posts_nothing():
    move = activity(1, (ACES, "UNKNOWN", player("Someone"), 0))

    assert format_activity(move) is None


def test_player_known_only_by_id():
    """espn_api falls back to the bare player ID when it can't find a name."""
    move = activity(1, (ACES, "DROPPED", 4241478, 0))

    assert format_activity(move) == "➖ Aces dropped 4241478"


def test_new_activities_are_newer_ones_oldest_first():
    at_100, at_300, at_200 = activity(100), activity(300), activity(200)

    assert new_activities([at_100, at_300, at_200], 150) == [at_200, at_300]


# One check


def make_cog(monkeypatch, activities, last_date):
    """A Transactions cog with ESPN, the spreadsheet and Discord faked."""
    monkeypatch.setattr(transactions, "TRANSACTIONS_CHANNEL_ID", 444)
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    monkeypatch.setattr(transactions, "get_text_channel", lambda bot, cid: channel)

    cog = Transactions.__new__(Transactions)  # skips __init__: no background task
    cog.bot = MagicMock()
    cog.league = MagicMock()
    cog.league.recent_activity.return_value = activities
    cog.last_date = None
    cog._failing = False
    cog._load_last_date = AsyncMock(return_value=last_date)
    cog._save_last_date = AsyncMock()
    return cog, channel


def drop(date, name):
    return activity(date, (ACES, "DROPPED", player(name), 0))


@pytest.mark.asyncio
async def test_first_check_posts_nothing_and_saves_starting_point(monkeypatch):
    cog, channel = make_cog(monkeypatch, [drop(100, "A"), drop(200, "B")], None)

    await cog.check_once()

    channel.send.assert_not_awaited()
    cog._save_last_date.assert_awaited_once_with(200)


@pytest.mark.asyncio
async def test_new_moves_are_posted_oldest_first_and_saved(monkeypatch):
    moves = [drop(300, "Newest"), drop(200, "Newer"), drop(100, "Old")]
    cog, channel = make_cog(monkeypatch, moves, 150)

    await cog.check_once()

    sent = [call.args[0] for call in channel.send.await_args_list]
    assert sent == ["➖ Aces dropped Newer", "➖ Aces dropped Newest"]
    cog._save_last_date.assert_awaited_once_with(300)


@pytest.mark.asyncio
async def test_nothing_new_saves_nothing(monkeypatch):
    cog, channel = make_cog(monkeypatch, [drop(100, "Old")], 100)

    await cog.check_once()

    channel.send.assert_not_awaited()
    cog._save_last_date.assert_not_awaited()


@pytest.mark.asyncio
async def test_progress_is_saved_when_discord_fails_halfway(monkeypatch):
    """Moves that were posted must not be posted again after a restart."""
    cog, channel = make_cog(monkeypatch, [drop(200, "First"), drop(300, "Second")], 100)
    channel.send.side_effect = [None, discord.HTTPException(MagicMock(), "down")]

    with pytest.raises(discord.HTTPException):
        await cog.check_once()

    cog._save_last_date.assert_awaited_once_with(200)


# Failures and the admin channel


@pytest.mark.asyncio
async def test_failure_tells_admin_once_then_recovery(monkeypatch):
    cog, _ = make_cog(monkeypatch, [], 100)
    cog._notify_admin = AsyncMock()
    cog.check_once = AsyncMock(side_effect=RuntimeError("ESPN down"))

    first = await cog._run_check()
    await cog._run_check()

    assert first == ERROR_BACKOFF_SECONDS
    cog._notify_admin.assert_awaited_once()

    cog.check_once = AsyncMock()
    assert await cog._run_check() == CHECK_SECONDS
    assert cog._notify_admin.await_count == 2  # "works again"


@pytest.mark.asyncio
async def test_failure_forgets_the_league(monkeypatch):
    """Expired ESPN cookies, for example: the league is fetched fresh next time."""
    cog, _ = make_cog(monkeypatch, [], 100)
    cog._notify_admin = AsyncMock()
    cog.check_once = AsyncMock(side_effect=RuntimeError("ESPN down"))

    await cog._run_check()

    assert cog.league is None


# Switched on or off


def test_feature_off_without_channel(monkeypatch):
    monkeypatch.setattr(transactions, "TRANSACTIONS_CHANNEL_ID", None)
    bot = MagicMock()

    cog = Transactions(bot)

    bot.loop.create_task.assert_not_called()
    assert cog.task is None


def test_feature_on_with_channel(monkeypatch):
    monkeypatch.setattr(transactions, "TRANSACTIONS_CHANNEL_ID", 444)
    bot = MagicMock()
    # Close the coroutine instead of running it, so Python doesn't warn about it.
    bot.loop.create_task.side_effect = lambda coroutine: coroutine.close()

    Transactions(bot)

    bot.loop.create_task.assert_called_once()
