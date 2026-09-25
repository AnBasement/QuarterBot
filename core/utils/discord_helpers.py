"""Shared discord.py helpers."""

import discord


def get_text_channel(
    bot: discord.Client, channel_id: int
) -> discord.TextChannel | None:
    """Returns the channel if it exists and is a text channel, else None."""
    channel = bot.get_channel(channel_id)
    return channel if isinstance(channel, discord.TextChannel) else None


DISCORD_MESSAGE_LIMIT = 2000  # characters; Discord rejects longer messages


def split_message(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Splits text into messages within Discord's limit, breaking between lines.

    A single line longer than the limit is cut.
    """
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
