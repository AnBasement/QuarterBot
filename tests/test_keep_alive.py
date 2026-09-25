"""Tests for core/keep_alive.py."""

from unittest.mock import patch

from core import keep_alive


def test_web_server_thread_does_not_keep_a_dead_bot_alive():
    """A non-daemon thread would keep the process alive after the bot stops,
    hiding the crash from the host."""
    with patch.object(keep_alive, "Thread") as thread_cls:
        keep_alive.keep_alive()

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
