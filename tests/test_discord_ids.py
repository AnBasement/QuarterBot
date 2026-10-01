"""Tests for data/discord_ids.py."""

import pytest

from data.discord_ids import load_discord_ids


def write(tmp_path, text):
    path = tmp_path / "discord_ids.json"
    path.write_text(text, encoding="utf-8")
    return path


def test_reads_team_ids_and_discord_ids_as_numbers(tmp_path):
    path = write(tmp_path, '{"1": "111111111111111111", "2": "222222222222222222"}')

    assert load_discord_ids(path) == {1: 111111111111111111, 2: 222222222222222222}


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_discord_ids(tmp_path / "discord_ids.json")


def test_broken_json_is_a_value_error(tmp_path):
    with pytest.raises(ValueError):
        load_discord_ids(write(tmp_path, '{"1": '))


def test_non_numeric_id_is_a_value_error(tmp_path):
    with pytest.raises(ValueError):
        load_discord_ids(write(tmp_path, '{"1": "not a number"}'))


@pytest.mark.parametrize("text", ['[["1", "111"]]', '"111"', "111"])
def test_json_that_is_not_an_object_is_a_value_error(tmp_path, text):
    """The inactive-player alerts only handle ValueError (and a missing file):
    anything else would stop them until the bot restarts."""
    with pytest.raises(ValueError):
        load_discord_ids(write(tmp_path, text))


def test_unreadable_file_is_a_value_error(tmp_path):
    """A folder where the file should be: reading it fails with an OSError."""
    (tmp_path / "discord_ids.json").mkdir()

    with pytest.raises(ValueError):
        load_discord_ids(tmp_path / "discord_ids.json")
