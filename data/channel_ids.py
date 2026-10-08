"""Discord channel IDs the bot posts to, read from the environment."""

import os


def _optional_channel_id(name: str) -> int | None:
    """Reads a channel ID as an int, or None if it's not set (feature off)."""
    value = os.getenv(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise ValueError(
            f"Environment variable {name} must be a numeric channel ID"
        ) from None


def _required_channel_id(name: str) -> int:
    """Reads a channel ID as an int (discord.py can't find a channel by a string ID)."""
    channel_id = _optional_channel_id(name)
    if channel_id is None:
        raise ValueError(f"Required environment variable {name} is not set")
    return channel_id


REMINDER_CHANNEL_ID = _required_channel_id("REMINDER_CHANNEL_ID")
GAME_CHANNEL_ID = _required_channel_id("GAME_CHANNEL_ID")  # pick'em games
ADMIN_CHANNEL_ID = _required_channel_id("ADMIN_CHANNEL_ID")  # errors and warnings
# Optional: league moves (adds, drops, trades). None switches the feature off.
TRANSACTIONS_CHANNEL_ID = _optional_channel_id("TRANSACTIONS_CHANNEL_ID")
# Optional: NFL injury and status reports. None switches the feature off.
PLAYER_UPDATES_CHANNEL_ID = _optional_channel_id("PLAYER_UPDATES_CHANNEL_ID")
