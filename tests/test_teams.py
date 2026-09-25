"""Tests for data/teams.py: guild-aware team emoji resolution."""

from unittest.mock import MagicMock

from data.teams import (
    DEFAULT_TEAM_EMOJIS,
    DRAW_ABBR,
    teams,
    get_team_emoji,
    get_team_emoji_by_name,
    get_draw_emoji,
    get_emoji_to_team_short_map,
)


class FakeEmoji:
    """Minimal stand-in for discord.Emoji: has a name and a <:name:id> str()."""

    def __init__(self, name: str, emoji_id: int = 123456789):
        self.name = name
        self._id = emoji_id

    def __str__(self) -> str:
        return f"<:{self.name}:{self._id}>"


def make_guild(*emoji_names: str) -> MagicMock:
    guild = MagicMock()
    guild.emojis = [FakeEmoji(name) for name in emoji_names]
    return guild


class TestGetTeamEmoji:
    def test_no_guild_uses_default(self):
        assert get_team_emoji(None, "ne") == DEFAULT_TEAM_EMOJIS["ne"]

    def test_guild_without_matching_emoji_uses_default(self):
        guild = make_guild("some_other_emoji")
        assert get_team_emoji(guild, "ne") == DEFAULT_TEAM_EMOJIS["ne"]

    def test_guild_with_matching_custom_emoji_is_used(self):
        guild = make_guild("ne", "buf")
        assert get_team_emoji(guild, "ne") == "<:ne:123456789>"

    def test_unknown_abbr_with_no_guild_returns_empty_string(self):
        assert get_team_emoji(None, "not_a_real_team") == ""


class TestGetTeamEmojiByName:
    def test_known_team_resolves_via_abbr(self):
        assert get_team_emoji_by_name(None, "New England Patriots") == (
            DEFAULT_TEAM_EMOJIS["ne"]
        )

    def test_unknown_team_returns_empty_string(self):
        assert get_team_emoji_by_name(None, "Not A Real Team") == ""

    def test_prefers_custom_guild_emoji(self):
        guild = make_guild("phi")
        assert (
            get_team_emoji_by_name(guild, "Philadelphia Eagles") == "<:phi:123456789>"
        )


class TestGetDrawEmoji:
    def test_no_guild_uses_default(self):
        assert get_draw_emoji(None) == DEFAULT_TEAM_EMOJIS[DRAW_ABBR]

    def test_custom_draw_emoji_on_guild_is_used(self):
        guild = make_guild(DRAW_ABBR)
        assert get_draw_emoji(guild) == f"<:{DRAW_ABBR}:123456789>"


class TestGetEmojiToTeamShortMap:
    def test_maps_every_team_with_defaults_when_no_guild(self):
        mapping = get_emoji_to_team_short_map(None)
        assert len(mapping) == len(teams)
        assert mapping[DEFAULT_TEAM_EMOJIS["ne"]] == "Patriots"

    def test_reflects_custom_guild_emoji(self):
        guild = make_guild("ne")
        mapping = get_emoji_to_team_short_map(guild)
        assert mapping["<:ne:123456789>"] == "Patriots"
        # Teams without a matching custom emoji still fall back correctly.
        assert mapping[DEFAULT_TEAM_EMOJIS["buf"]] == "Bills"
