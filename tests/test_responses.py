"""Tests for cogs/responses.py, custom commands from custom_commands.json."""

import json
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands

from cogs import responses
from cogs.responses import load_custom_commands


def write(tmp_path, data):
    path = tmp_path / "custom_commands.json"
    text = data if isinstance(data, str) else json.dumps(data)
    path.write_text(text, encoding="utf-8")
    return path


def make_bot():
    return commands.Bot(command_prefix="!", intents=discord.Intents.none())


def test_missing_file_means_no_commands(tmp_path):
    assert load_custom_commands(tmp_path / "custom_commands.json") == {}


def test_broken_json_means_no_commands_not_a_crash(tmp_path):
    assert load_custom_commands(write(tmp_path, "{not json")) == {}


def test_invalid_entries_are_skipped_and_leading_bang_allowed(tmp_path):
    path = write(
        tmp_path,
        {
            "doink": "DOINK",
            "!hello": "Hi",
            "has space": "x",
            "empty": "   ",
            "too long": "x" * 2001,
        },
    )
    assert load_custom_commands(path) == {"doink": "DOINK", "hello": "Hi"}


@pytest.mark.asyncio
async def test_each_entry_becomes_a_command_that_replies(tmp_path, monkeypatch):
    monkeypatch.setattr(
        responses, "CUSTOM_COMMANDS_FILE", write(tmp_path, {"doink": "DOINK"})
    )
    bot = make_bot()

    await responses.setup(bot)

    ctx = MagicMock()
    ctx.send = AsyncMock()
    await bot.get_command("doink").callback(ctx)
    ctx.send.assert_awaited_once_with("DOINK")


@pytest.mark.asyncio
async def test_custom_command_cannot_replace_a_built_in_one(tmp_path, monkeypatch):
    monkeypatch.setattr(
        responses,
        "CUSTOM_COMMANDS_FILE",
        write(tmp_path, {"export": "hijacked", "doink": "DOINK"}),
    )
    bot = make_bot()

    @bot.command(name="export")
    async def export(ctx):
        pass

    await responses.setup(bot)

    assert bot.get_command("export").callback is export.callback
    assert bot.get_command("doink") is not None
