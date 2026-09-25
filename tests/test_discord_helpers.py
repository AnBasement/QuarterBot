"""Tests for core/utils/discord_helpers.py."""

from core.utils.discord_helpers import split_message


def test_short_text_is_one_message():
    assert split_message("a\nb") == ["a\nb"]


def test_long_text_splits_between_lines_under_the_limit():
    """A 12-14 team digest can pass 2,000 characters."""
    lines = [f"- Team {i:02d} with {100 + i}.00 points" for i in range(200)]
    text = "\n".join(lines)

    chunks = split_message(text)

    assert len(chunks) > 1
    assert all(len(c) <= 2000 for c in chunks)
    assert "\n".join(chunks) == text  # nothing lost, no line cut in half


def test_single_overlong_line_is_hard_cut():
    chunks = split_message("x" * 4500)

    assert [len(c) for c in chunks] == [2000, 2000, 500]
