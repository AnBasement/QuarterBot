"""Posts a new version's CHANGELOG notes to CHANGELOG_CHANNEL_ID, once."""

import asyncio
import logging
import re
from pathlib import Path

import discord
import gspread.exceptions
import requests
from discord.ext import commands
from gspread.worksheet import Worksheet

from cogs.sheets import get_or_create_tab
from core.utils.discord_helpers import get_text_channel, split_message
from data.channel_ids import ADMIN_CHANNEL_ID, CHANGELOG_CHANNEL_ID
from data.config import PICKEM_SHEET_NAME
from data.messages import RELEASE_ANNOUNCEMENT_TEMPLATE

logger = logging.getLogger(__name__)

# CHANGELOG.md in the project's root folder (one up from cogs/).
CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
STATE_TAB = "Announced version"

# What a Google Sheets call can raise when Google is the problem.
SHEETS_ERRORS = (
    gspread.exceptions.GSpreadException,
    requests.exceptions.RequestException,
)

# A released version's heading, e.g. "## [1.2.0] - 11-10-2026".
RELEASE_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\]")


def latest_release(changelog: str) -> tuple[str, str] | None:
    """The newest released version in the CHANGELOG and its notes: the lines
    under its heading, up to the next "## " heading. [Unreleased] is skipped.
    None if nothing has been released yet."""
    lines = changelog.splitlines()
    for index, line in enumerate(lines):
        found = RELEASE_HEADING.match(line)
        if found is None:
            continue
        notes = []
        for following in lines[index + 1 :]:
            if following.startswith("## "):
                break
            notes.append(following)
        return found.group(1), "\n".join(notes).strip()
    return None


class Announcements(commands.Cog):
    """Posts the release notes for the latest version the first time the
    bot runs it."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.task: asyncio.Task[None] | None = None
        if CHANGELOG_CHANNEL_ID is None:
            logger.info("CHANGELOG_CHANNEL_ID not set; release announcements off.")
        else:
            self.task = self.bot.loop.create_task(self.announce())

    async def cog_unload(self) -> None:
        """Stops the background task."""
        if self.task is not None:
            self.task.cancel()

    async def _notify_admin(self, message: str) -> None:
        """Posts to the admin channel. A failed send is logged, never raised."""
        admin_channel = get_text_channel(self.bot, ADMIN_CHANNEL_ID)
        if admin_channel is None:
            return
        try:
            await admin_channel.send(message)
        except Exception as exc:
            # Broad on purpose: a failed notification must never stop the caller.
            logger.warning("Failed to send admin warning in Announcements: %s", exc)

    async def _state_tab(self) -> Worksheet:
        """The Announced version tab of the pick'em spreadsheet, created if missing."""
        return await asyncio.to_thread(
            get_or_create_tab, PICKEM_SHEET_NAME, STATE_TAB, ["version"]
        )

    async def _load_announced(self) -> str | None:
        """The last announced version, or None if none has been yet."""
        tab = await self._state_tab()
        values = await asyncio.to_thread(tab.get, "A2")
        raw = values[0][0] if values and values[0] else ""
        return raw if raw else None

    async def _save_announced(self, version: str) -> None:
        """Writes the latest announced version to the spreadsheet."""
        tab = await self._state_tab()
        await asyncio.to_thread(tab.update, [[str(version)]], "A2")

    async def announce_once(self) -> None:
        """Posts the newest release's notes if they haven't been posted yet.
        Raises if the file, Google or Discord fails; nothing is saved then, so
        the next start tries again."""
        channel_id = CHANGELOG_CHANNEL_ID
        if channel_id is None:
            return
        channel = get_text_channel(self.bot, channel_id)
        if channel is None:
            logger.warning("Changelog channel %s not found.", channel_id)
            return
        text = await asyncio.to_thread(CHANGELOG.read_text, encoding="utf-8")
        release = latest_release(text)
        if release is None:
            return
        version, notes = release
        if version == await self._load_announced():
            return
        message = RELEASE_ANNOUNCEMENT_TEMPLATE.format(version=version, notes=notes)
        for chunk in split_message(message):
            await channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())
        await self._save_announced(version)

    async def announce(self) -> None:
        """Waits for Discord, then announces once. Runs once per start."""
        await self.bot.wait_until_ready()
        try:
            await self.announce_once()
        except (OSError, discord.HTTPException, *SHEETS_ERRORS) as exc:
            logger.error("Release announcement failed: %s", exc)
            await self._notify_admin(
                f"[announcements] Couldn't announce the new version: {exc}. "
                "It's tried again the next time the bot starts."
            )


async def setup(bot: commands.Bot) -> None:
    """Loads the cog (called by discord.py's load_extension)."""
    await bot.add_cog(Announcements(bot))
