"""Discord channel IDs the bot posts to, read from the environment."""

import os


def _required_channel_id(name: str) -> int:
    """Reads a channel ID as an int (discord.py can't find a channel by a string ID)."""
    value = os.getenv(name)
    if value is None:
        raise ValueError(f"Required environment variable {name} is not set")
    try:
        return int(value)
    except ValueError:
        raise ValueError(
            f"Environment variable {name} must be a numeric channel ID"
        ) from None


REMINDER_CHANNEL_ID = _required_channel_id("REMINDER_CHANNEL_ID")
GAME_CHANNEL_ID = _required_channel_id("GAME_CHANNEL_ID")  # pick'em games
ADMIN_CHANNEL_ID = _required_channel_id("ADMIN_CHANNEL_ID")  # errors and warnings
