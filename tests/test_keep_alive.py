"""Tests for core/keep_alive.py."""

import math
from unittest.mock import MagicMock, patch

from core import keep_alive


def test_web_server_thread_does_not_keep_a_dead_bot_alive():
    """A non-daemon thread would keep the process alive after the bot stops,
    hiding the crash from the host."""
    with patch.object(keep_alive, "Thread") as thread_cls:
        keep_alive.keep_alive(MagicMock())

    assert thread_cls.call_args.kwargs.get("daemon") is True


def test_web_server_listens_on_the_hosts_port(monkeypatch):
    """Hosts like Heroku assign the port through PORT and expect the app
    to listen there."""
    monkeypatch.setenv("PORT", "12345")
    with patch.object(keep_alive.app, "run") as app_run:
        keep_alive.run()

    assert app_run.call_args.kwargs["port"] == 12345


def test_web_server_defaults_to_8080_without_port(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    with patch.object(keep_alive.app, "run") as app_run:
        keep_alive.run()

    assert app_run.call_args.kwargs["port"] == 8080


def connected_bot():
    """A fake bot that's logged in with a working connection."""
    bot = MagicMock()
    bot.is_ready.return_value = True
    bot.is_closed.return_value = False
    bot.latency = 0.05
    return bot


def get_health(monkeypatch, bot):
    """Visits the health check page with `bot` registered."""
    monkeypatch.setattr(keep_alive, "_bot", bot)
    return keep_alive.app.test_client().get("/")


def test_health_check_ok_when_connected(monkeypatch):
    response = get_health(monkeypatch, connected_bot())

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "Bot is running!"


def test_health_check_fails_before_login(monkeypatch):
    bot = connected_bot()
    bot.is_ready.return_value = False

    assert get_health(monkeypatch, bot).status_code == 503


def test_health_check_fails_when_connection_closed(monkeypatch):
    bot = connected_bot()
    bot.is_closed.return_value = True

    assert get_health(monkeypatch, bot).status_code == 503


def test_health_check_fails_without_heartbeat(monkeypatch):
    """discord.py reports nan (no connection) or inf (no heartbeat answer yet)."""
    for latency in (math.nan, math.inf):
        bot = connected_bot()
        bot.latency = latency

        assert get_health(monkeypatch, bot).status_code == 503


def test_health_check_fails_without_a_bot(monkeypatch):
    assert get_health(monkeypatch, None).status_code == 503
