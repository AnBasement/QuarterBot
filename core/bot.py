"""Bot startup, global events and error handling."""

import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

# Before importing data/: those modules read their settings at import time.
load_dotenv()

from core.keep_alive import keep_alive  # noqa: E402
from core.logging_config import setup_logging  # noqa: E402
from core.utils.global_cooldown import setup_global_cooldown  # noqa: E402
from core.errors import BotError  # noqa: E402
from data.channel_ids import ADMIN_CHANNEL_ID  # noqa: E402
from data.messages import (  # noqa: E402
    COMMAND_USAGE_MESSAGE,
    COOLDOWN_MESSAGE_TEMPLATE,
    PERMISSION_DENIED_MESSAGE,
)

logger = logging.getLogger(__name__)

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.messages = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

setup_global_cooldown(bot)

COGS = [
    "cogs.utility",  # ping, small commands
    "cogs.pickem",  # matches, export, results
    "cogs.ppr",  # updates and posts PPR
    "cogs.fantasy_reminders",  # general reminders for the fantasy league
    "cogs.responses",  # custom commands; must load last (see cogs/responses.py)
]


@bot.event
async def on_ready():
    """Logs that the bot is connected."""
    logger.info("Bot logged in as %s", bot.user)


async def notify_admin_channel(message: str) -> None:
    """Posts to the admin channel. A failed send is logged, never raised."""
    admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
    if not isinstance(admin_channel, discord.TextChannel):
        logger.warning("Admin channel not registered: %s", message)
        return
    try:
        await admin_channel.send(message)
    except Exception as exc:
        logger.warning("Failed to send admin warning to Discord: %s", exc)


@bot.event
async def on_command_error(ctx, error):
    """Replies to expected user mistakes; reports anything else to the admin channel."""
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(
            COOLDOWN_MESSAGE_TEMPLATE.format(
                user=ctx.author.mention, seconds=error.retry_after
            )
        )
        return

    if isinstance(error, commands.CheckFailure):
        await ctx.send(PERMISSION_DENIED_MESSAGE)
        return
    # Only raised for required arguments; no current command has one.
    if isinstance(error, commands.UserInputError) and ctx.command is not None:
        usage = f"{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}"
        await ctx.send(COMMAND_USAGE_MESSAGE.format(usage=usage.strip()))
        return

    if isinstance(error, BotError):
        error_msg = f"⚠️ BotError in `{ctx.command}`:\n```{error}```"
    else:
        error_msg = f"❌ Unexpected error in `{ctx.command}`:\n```{error}```"

    await notify_admin_channel(error_msg)

    logger.error(
        "Command error for %s (user=%s): %s",
        ctx.command,
        ctx.author,
        error,
        exc_info=(type(error), error, error.__traceback__),
    )


async def main():
    """Starts the keep-alive server, loads the cogs and runs the bot."""
    setup_logging()  # before anything logs
    keep_alive()
    async with bot:
        for cog in COGS:
            try:
                await bot.load_extension(cog)
                logger.info("Loaded cog: %s", cog)
            except commands.ExtensionNotFound as e:
                logger.error("Cog not found: %s (%s)", cog, e)
                await notify_admin_channel(f"[startup] Cog not found: {cog} ({e})")
            except commands.ExtensionFailed as e:
                logger.error("Error loading cog %s: %s", cog, e)
                await notify_admin_channel(f"[startup] Could not find cog {cog}: {e}")
            except Exception as e:
                # Broad on purpose: one broken cog shouldn't stop the others
                # or the bot from starting.
                logger.exception("Unexpected error loading cog %s: %s", cog, e)
                await notify_admin_channel(
                    f"[startup] Unexpected error loading cog {cog}: {e}"
                )

        if TOKEN is None:
            raise ValueError("TOKEN not defined in environment variables")
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
