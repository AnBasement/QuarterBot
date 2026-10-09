"""Tests for cogs/announcements.py."""

from pathlib import Path

from cogs.announcements import latest_release

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
