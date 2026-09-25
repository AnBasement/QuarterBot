"""Tests for global cooldown."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands

from core.bot import on_command_error
from core.utils.global_cooldown import setup_global_cooldown
from data.messages import COOLDOWN_MESSAGE_TEMPLATE


@pytest.mark.asyncio
async def test_user_cooldown_routes_to_on_command_error_and_sends_retry_message():
    """A second command within the cooldown gets the retry message."""
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    setup_global_cooldown(bot, rate=1, per=60)

    check = bot._checks[0]
    author = MagicMock()
    author.id = 42
    author.mention = "<@42>"

    message = MagicMock()
    message.author = author
    message.content = "!ping"
    message.guild = None

    ctx = MagicMock()
    ctx.author = author
    ctx.message = message
    ctx.send = AsyncMock()

    assert await check(ctx) is True

    with pytest.raises(commands.CommandOnCooldown) as error:
        await check(ctx)

    assert error.value.type is commands.BucketType.user
    assert error.value.retry_after > 0

    await on_command_error(ctx, error.value)

    ctx.send.assert_awaited_once()
    sent_message = ctx.send.await_args.args[0]
    expected = COOLDOWN_MESSAGE_TEMPLATE.format(
        user=ctx.author.mention, seconds=error.value.retry_after
    )
    assert sent_message == expected
