"""Tests for core/utils/espn_helpers.py."""

from unittest.mock import patch

import pytest

from core.utils.espn_helpers import get_league


def test_builds_the_league_from_the_settings(monkeypatch):
    monkeypatch.setenv("ESPN_LEAGUE_ID", "123456")
    monkeypatch.setenv("ESPN_YEAR", "2026")
    monkeypatch.setenv("ESPN_S2", "s2-cookie")
    monkeypatch.setenv("ESPN_SWID", "{swid}")

    with patch("core.utils.espn_helpers.League") as league_cls:
        get_league()

    league_cls.assert_called_once_with(
        league_id=123456, year=2026, espn_s2="s2-cookie", swid="{swid}"
    )


def test_public_league_needs_no_cookies(monkeypatch):
    monkeypatch.setenv("ESPN_LEAGUE_ID", "123456")
    monkeypatch.setenv("ESPN_YEAR", "2026")
    monkeypatch.delenv("ESPN_S2", raising=False)
    monkeypatch.delenv("ESPN_SWID", raising=False)

    with patch("core.utils.espn_helpers.League") as league_cls:
        get_league()

    assert league_cls.call_args.kwargs["espn_s2"] is None
    assert league_cls.call_args.kwargs["swid"] is None


@pytest.mark.parametrize("missing", ["ESPN_LEAGUE_ID", "ESPN_YEAR"])
def test_missing_setting_is_a_value_error(monkeypatch, missing):
    monkeypatch.setenv("ESPN_LEAGUE_ID", "123456")
    monkeypatch.setenv("ESPN_YEAR", "2026")
    monkeypatch.delenv(missing)

    with pytest.raises(ValueError):
        get_league()


def test_non_numeric_setting_is_a_value_error(monkeypatch):
    monkeypatch.setenv("ESPN_LEAGUE_ID", "my league")
    monkeypatch.setenv("ESPN_YEAR", "2026")

    with pytest.raises(ValueError):
        get_league()
