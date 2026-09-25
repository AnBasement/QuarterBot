"""Custom commands from custom_commands.json.

Each entry becomes a command with a fixed reply: {"doink": "DOINK"} gives
!doink. The file is optional; see custom_commands.example.json.
"""

import json
import logging
from pathlib import Path

from discord.ext import commands

logger = logging.getLogger(__name__)

# In the root folder, where hosts like Render put uploaded secret files.
CUSTOM_COMMANDS_FILE = Path("custom_commands.json")
DISCORD_MESSAGE_LIMIT = 2000


def load_custom_commands(path: Path) -> dict[str, str]:
    """Reads {command name: reply}. A missing or broken file is logged, never raised."""
    if not path.exists():
        logger.info("No %s found, no custom commands loaded", path)
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Could not read %s, loading no custom commands: %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        logger.error('%s must look like {"name": "reply", ...}', path)
        return {}

    valid: dict[str, str] = {}
    for name, message in raw.items():
        name = name.strip().lstrip("!")
        if (
            not name
            or " " in name
            or not isinstance(message, str)
            or not message.strip()
            or len(message) > DISCORD_MESSAGE_LIMIT
        ):
            logger.warning("Skipping invalid custom command %r in %s", name, path)
            continue
        valid[name] = message
    return valid


def make_response_command(name: str, message: str) -> commands.Command:
    """Builds a command that replies with a fixed message."""

    async def respond(ctx: commands.Context) -> None:
        await ctx.send(message)

    return commands.Command(respond, name=name, help="Custom command.")


async def setup(bot: commands.Bot) -> None:
    """Registers one command per entry, skipping names already taken.

    Loaded last in core/bot.py so every built-in command already exists.
    """
    for name, message in load_custom_commands(CUSTOM_COMMANDS_FILE).items():
        try:
            bot.add_command(make_response_command(name, message))
        except commands.CommandRegistrationError:
            logger.warning(
                "Custom command !%s clashes with an existing command, skipped", name
            )
