"""Tests for data/messages.py."""

import re
from pathlib import Path

import data.messages as messages

ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"


def test_env_example_shows_the_real_defaults():
    """People copy .env.example's examples, so they must match the real
    defaults exactly (line breaks written as \\n)."""
    shown = dict(
        re.findall(r"^# ([A-Z_]+)=(.*)$", ENV_EXAMPLE.read_text(), re.MULTILINE)
    )
    names = [n for n in dir(messages) if n.isupper()]

    for name in names:
        assert name in shown, f"{name} missing from .env.example"
        assert shown[name] == getattr(messages, name).replace("\n", "\\n"), name


def test_backslash_n_in_a_message_becomes_a_line_break(monkeypatch):
    monkeypatch.setenv("SOME_MESSAGE", "line one\\nline two")

    assert messages._message("SOME_MESSAGE", "") == "line one\nline two"
