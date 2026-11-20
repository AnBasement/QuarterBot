"""Small web server the host can ping to see that the bot is up."""

import math
import os
from threading import Thread

from discord.ext import commands
from flask import Flask

app = Flask(__name__)

# The bot the health check reports on, set by keep_alive().
_bot: commands.Bot | None = None


def bot_is_connected(bot: commands.Bot) -> bool:
    """Whether the bot is logged in to Discord with a working connection.

    discord.py reports the latency as nan or inf when there's no connection.
    """
    return bot.is_ready() and not bot.is_closed() and math.isfinite(bot.latency)


@app.route("/")
def home() -> tuple[str, int]:
    """Health check: 200 while connected to Discord, 503 otherwise."""
    if _bot is None or not bot_is_connected(_bot):
        return "Bot is not connected to Discord", 503
    return "Bot is running!", 200


def run() -> None:
    """Serves on the host's PORT (8080 if unset)."""
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))


def keep_alive(bot: commands.Bot) -> None:
    """Starts the web server in a background thread, reporting on `bot`.

    daemon=True so the process still exits if the bot stops, letting the
    host restart it.
    """
    global _bot
    _bot = bot
    thread = Thread(target=run, daemon=True)
    thread.start()
