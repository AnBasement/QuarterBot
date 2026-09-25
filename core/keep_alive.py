"""Small web server the host can ping to see that the bot is up."""

import os
from threading import Thread
from flask import Flask

app = Flask(__name__)


@app.route("/")
def home():
    """Health check."""
    return "Bot is running!"


def run():
    """Serves on the host's PORT (8080 if unset)."""
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))


def keep_alive():
    """Starts the web server in a background thread.

    daemon=True so the process still exits if the bot stops, letting the
    host restart it.
    """
    thread = Thread(target=run, daemon=True)
    thread.start()
