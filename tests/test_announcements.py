"""Tests for cogs/announcements.py."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs import announcements
from cogs.announcements import Announcements, latest_release

CHANGELOG = """# Changelog

Some text about the format.

## [Unreleased]

### Added
- Something not released yet

## [1.2.0] - 11-10-2026

### Added
- Player updates

### Fixed
- A bug

## [1.1.0] - 05-10-2026

### Added
- The pick lock
"""


def test_latest_release_is_the_newest_version_and_its_notes():
    version, notes = latest_release(CHANGELOG)

    assert version == "1.2.0"
    assert notes == "### Added\n- Player updates\n\n### Fixed\n- A bug"


def test_unreleased_changes_are_left_out():
    _, notes = latest_release(CHANGELOG)

    assert "not released yet" not in notes


def test_notes_stop_at_the_next_version():
    _, notes = latest_release(CHANGELOG)

    assert "pick lock" not in notes
    assert "1.1.0" not in notes


def test_no_release_yet_gives_none():
    assert latest_release("# Changelog\n\n## [Unreleased]\n\n- Work") is None


def test_the_real_changelog_has_a_release():
    """The bot reads the project's own CHANGELOG.md: its format must keep
    working with the function."""
    text = (Path(__file__).parent.parent / "CHANGELOG.md").read_text(encoding="utf-8")

    release = latest_release(text)

    assert release is not None
    version, notes = release
    assert version.count(".") == 2
    assert notes.startswith("### ")


# Remembering the announced version


def cog_with_tab(cells):
    """An Announcements cog whose state tab holds `cells` in A2."""
    cog = Announcements(MagicMock())
    tab = MagicMock()
    tab.get.return_value = cells
    cog._state_tab = AsyncMock(return_value=tab)
    return cog, tab


@pytest.mark.asyncio
async def test_load_announced_gives_the_saved_version():
    cog, _ = cog_with_tab([["1.2.0"]])

    assert await cog._load_announced() == "1.2.0"


@pytest.mark.asyncio
@pytest.mark.parametrize("cells", [[], [[]], [[""]]])
async def test_load_announced_is_none_for_an_empty_tab(cells):
    """A new tab, or an empty A2: nothing has been announced yet."""
    cog, _ = cog_with_tab(cells)

    assert await cog._load_announced() is None


@pytest.mark.asyncio
async def test_save_announced_writes_the_version_to_a2():
    cog, tab = cog_with_tab([])

    await cog._save_announced("1.2.0")

    tab.update.assert_called_once_with([["1.2.0"]], "A2")


# Switched on or off


def test_off_without_the_channel_setting(monkeypatch):
    monkeypatch.setattr(announcements, "CHANGELOG_CHANNEL_ID", None)
    bot = MagicMock()

    cog = Announcements(bot)

    bot.loop.create_task.assert_not_called()
    assert cog.task is None


def test_on_with_the_channel_setting(monkeypatch):
    monkeypatch.setattr(announcements, "CHANGELOG_CHANNEL_ID", 777)
    bot = MagicMock()
    # Close the coroutine instead of running it, so Python doesn't warn about it.
    bot.loop.create_task.side_effect = lambda coroutine: coroutine.close()

    Announcements(bot)

    bot.loop.create_task.assert_called_once()


# Announcing


def announcing_cog(monkeypatch, tmp_path, changelog, announced):
    """A cog set up to announce: the channel setting on, `changelog` as the
    CHANGELOG file, and `announced` as the last announced version."""
    monkeypatch.setattr(announcements, "CHANGELOG_CHANNEL_ID", None)
    cog = Announcements(MagicMock())  # created with the setting off: no task
    monkeypatch.setattr(announcements, "CHANGELOG_CHANNEL_ID", 777)
    path = tmp_path / "CHANGELOG.md"
    path.write_text(changelog, encoding="utf-8")
    monkeypatch.setattr(announcements, "CHANGELOG", path)
    channel = MagicMock()
    channel.send = AsyncMock()
    monkeypatch.setattr(announcements, "get_text_channel", lambda bot, cid: channel)
    cog._load_announced = AsyncMock(return_value=announced)
    cog._save_announced = AsyncMock()
    cog._notify_admin = AsyncMock()
    cog.bot.wait_until_ready = AsyncMock()
    return cog, channel


def sent_text(channel):
    return "\n".join(call.args[0] for call in channel.send.await_args_list)


@pytest.mark.asyncio
async def test_a_new_version_is_posted_and_saved(monkeypatch, tmp_path):
    cog, channel = announcing_cog(monkeypatch, tmp_path, CHANGELOG, "1.1.0")

    await cog.announce_once()

    text = sent_text(channel)
    assert text.startswith("**quarterbot 1.2.0 is live!** What's new:")
    assert "- Player updates" in text and "pick lock" not in text
    cog._save_announced.assert_awaited_once_with("1.2.0")


@pytest.mark.asyncio
async def test_announcements_never_ping_anyone(monkeypatch, tmp_path):
    """The CHANGELOG mentions `@everyone` (in backticks)."""
    cog, channel = announcing_cog(monkeypatch, tmp_path, CHANGELOG, None)

    await cog.announce_once()

    for call in channel.send.await_args_list:
        mentions = call.kwargs["allowed_mentions"]
        assert not mentions.everyone and not mentions.users and not mentions.roles


@pytest.mark.asyncio
async def test_the_same_version_is_not_posted_again(monkeypatch, tmp_path):
    """A restart or a redeploy of the same version."""
    cog, channel = announcing_cog(monkeypatch, tmp_path, CHANGELOG, "1.2.0")

    await cog.announce_once()

    channel.send.assert_not_awaited()
    cog._save_announced.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_first_start_posts_the_current_version(monkeypatch, tmp_path):
    cog, channel = announcing_cog(monkeypatch, tmp_path, CHANGELOG, None)

    await cog.announce_once()

    channel.send.assert_awaited()
    cog._save_announced.assert_awaited_once_with("1.2.0")


@pytest.mark.asyncio
async def test_a_long_release_is_split_into_several_messages(monkeypatch, tmp_path):
    long_notes = "\n".join(f"- Change number {n} with some words" for n in range(150))
    changelog = (
        f"## [Unreleased]\n\n## [1.3.0] - 01-11-2026\n\n### Added\n{long_notes}\n"
    )
    cog, channel = announcing_cog(monkeypatch, tmp_path, changelog, "1.2.0")

    await cog.announce_once()

    assert channel.send.await_count >= 2
    assert all(len(call.args[0]) <= 2000 for call in channel.send.await_args_list)
    cog._save_announced.assert_awaited_once_with("1.3.0")


@pytest.mark.asyncio
async def test_no_release_yet_posts_nothing(monkeypatch, tmp_path):
    cog, channel = announcing_cog(
        monkeypatch, tmp_path, "## [Unreleased]\n\n- Work\n", None
    )

    await cog.announce_once()

    channel.send.assert_not_awaited()
    cog._save_announced.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_missing_channel_posts_nothing(monkeypatch, tmp_path):
    cog, _ = announcing_cog(monkeypatch, tmp_path, CHANGELOG, None)
    monkeypatch.setattr(announcements, "get_text_channel", lambda bot, cid: None)

    await cog.announce_once()

    cog._save_announced.assert_not_awaited()


@pytest.mark.asyncio
async def test_discord_failing_saves_nothing_and_tells_the_admin(monkeypatch, tmp_path):
    """Nothing saved, so the next start tries again."""
    cog, channel = announcing_cog(monkeypatch, tmp_path, CHANGELOG, "1.1.0")
    channel.send.side_effect = discord.HTTPException(MagicMock(), "Discord down")

    await cog.announce()

    cog._save_announced.assert_not_awaited()
    cog._notify_admin.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_missing_changelog_file_tells_the_admin(monkeypatch, tmp_path):
    cog, _ = announcing_cog(monkeypatch, tmp_path, CHANGELOG, None)
    monkeypatch.setattr(announcements, "CHANGELOG", tmp_path / "missing.md")

    await cog.announce()

    cog._save_announced.assert_not_awaited()
    cog._notify_admin.assert_awaited_once()
