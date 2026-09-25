"""Tests for core/bot.py's startup."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_settings_from_dotenv_file_are_seen_by_data_modules(tmp_path):
    """data/ modules read settings at import time, so .env must load first.

    Runs in a fresh process, since conftest.py has already set the channel IDs
    in this one."""
    (tmp_path / ".env").write_text(
        "REMINDER_CHANNEL_ID=1\nGAME_CHANNEL_ID=2\nADMIN_CHANNEL_ID=3\n"
        "GAMES_POSTED_MESSAGE=from-dotenv\n"
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "REMINDER_CHANNEL_ID",
            "GAME_CHANNEL_ID",
            "ADMIN_CHANNEL_ID",
            "GAMES_POSTED_MESSAGE",
        }
    }
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import core.bot; from data.messages import GAMES_POSTED_MESSAGE as m; print(m)",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "from-dotenv"


class TestOnCommandError:
    """Expected user mistakes get a reply, not an admin alert."""

    @staticmethod
    def ctx():
        from unittest.mock import AsyncMock, MagicMock

        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.prefix = "!"
        ctx.command.qualified_name = "games"
        ctx.command.signature = "[week]"
        return ctx

    @pytest.mark.asyncio
    async def test_non_admin_gets_reply_and_admin_is_not_alerted(self, monkeypatch):
        from unittest.mock import AsyncMock
        from discord.ext import commands
        import core.bot
        from data.messages import PERMISSION_DENIED_MESSAGE

        notify = AsyncMock()
        monkeypatch.setattr(core.bot, "notify_admin_channel", notify)
        ctx = self.ctx()

        await core.bot.on_command_error(ctx, commands.CheckFailure("not admin"))

        ctx.send.assert_awaited_once_with(PERMISSION_DENIED_MESSAGE)
        notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_bad_argument_gets_usage_hint(self, monkeypatch):
        from unittest.mock import AsyncMock
        from discord.ext import commands
        import core.bot

        notify = AsyncMock()
        monkeypatch.setattr(core.bot, "notify_admin_channel", notify)
        ctx = self.ctx()

        await core.bot.on_command_error(ctx, commands.BadArgument("abc"))

        ctx.send.assert_awaited_once_with("Couldn't read that. Usage: `!games [week]`")
        notify.assert_not_awaited()

    def test_pickem_has_no_listener_of_its_own(self):
        """A cog's on_command_error listener would fire for every command, on
        top of core/bot.py's handler."""
        from cogs.pickem import Pickem

        assert "on_command_error" not in dict(Pickem.__cog_listeners__)
