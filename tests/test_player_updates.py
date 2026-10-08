"""Tests for cogs/player_updates.py."""

import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
import pytz
import requests

from cogs import player_updates
from cogs.player_updates import (
    ERROR_BACKOFF_SECONDS,
    GAME_DAY_CHECK_SECONDS,
    INJURIES_PAGE,
    OTHER_DAY_CHECK_SECONDS,
    ROSTER_REFRESH_SECONDS,
    PlayerUpdates,
    advance_marker,
    format_update,
    is_injury_news,
    is_status_only,
    new_reports,
    player_id,
    seconds_until_next_check,
)

PLAYER_PAGE = "https://www.espn.com/nfl/player/_/id/4363538/montez-sweat"


def report(
    status="Active",
    comment="",
    report_id="1",
    date="2026-10-04T15:43Z",
    links=None,
):
    """A report shaped like the ones in ESPN's injuries feed."""
    if links is None:
        links = [
            {"rel": ["playercard", "desktop", "athlete"], "href": PLAYER_PAGE},
            {"rel": ["stats", "desktop", "athlete"], "href": PLAYER_PAGE + "/stats"},
        ]
    return {
        "id": report_id,
        "date": date,
        "status": status,
        "shortComment": comment,
        "athlete": {
            "displayName": "Montez Sweat",
            "team": {"abbreviation": "CHI"},
            "position": {"abbreviation": "DE"},
            "links": links,
        },
    }


# is_injury_news


@pytest.mark.parametrize(
    "status", ["Questionable", "Doubtful", "Out", "Injured Reserve"]
)
def test_injury_statuses_are_posted(status):
    assert is_injury_news(report(status, "Sweat is dealing with a sore knee."))


@pytest.mark.parametrize(
    "comment",
    [
        "Sweat (groin) is active for Sunday's game against the Lions.",
        "Sweat (groin) was a full participant in Wednesday's practice.",
        "St. Brown (ankle) is active for Sunday's game.",
        "Smith-Njigba (hip) is active for Sunday's game.",
        "van den Berg (knee) is active for Sunday's game.",
        "Jordan van den Berg (knee) is active for Sunday's game.",
    ],
)
def test_active_reports_naming_an_injury_are_posted(comment):
    """Cleared to play, or a practice update: the injury is in brackets right
    after the name."""
    assert is_injury_news(report("Active", comment))


@pytest.mark.parametrize(
    "comment",
    [
        "Sweat recorded five tackles (three solo) in Sunday's win.",
        "Allgeier rushed eight times for 42 yards in the loss.",
    ],
)
def test_stat_lines_are_not_posted(comment):
    """After-game stat lines are "Active" too, but have no injury after the name
    (brackets later in the sentence don't count)."""
    assert not is_injury_news(report("Active", comment))


def test_coachs_decision_is_not_posted():
    """A healthy scratch, not an injury."""
    assert not is_injury_news(
        report("Out", "Sweat (coach's decision) is inactive for Sunday's game.")
    )


def test_a_plain_inactive_is_not_posted():
    """The official inactive lists: no injury named."""
    assert not is_injury_news(report("Out", "Sweat is inactive for Sunday's game."))


def test_an_injured_inactive_is_posted():
    assert is_injury_news(report("Out", "Sweat (toe) is inactive for Sunday's game."))


def test_an_injured_inactive_with_a_lowercase_name_is_posted():
    """A name the bracket rule missed would count as a plain "inactive" and be
    skipped, even though an injury is named."""
    assert is_injury_news(
        report("Out", "van den Berg (knee) is inactive for Sunday's game.")
    )


# Status-only entries (ESPN's automated ones)


def status_entry(status, comment, description, abbreviation, **kwargs):
    """An entry like ESPN's automated ones: the comment is just the status."""
    entry = report(status, comment, **kwargs)
    entry["type"] = {"description": description, "abbreviation": abbreviation}
    return entry


QUESTIONABLE = ("Questionable", "questionable", "questionable", "Q")
INJURED_RESERVE = ("Injured Reserve", "ir", "Injured Reserve", "IR")


def test_a_comment_repeating_the_status_is_status_only():
    assert is_status_only(status_entry(*QUESTIONABLE))


def test_ir_is_status_only_through_the_abbreviation():
    """The description is "Injured Reserve", so only "IR" matches "ir"."""
    assert is_status_only(status_entry(*INJURED_RESERVE))


def test_status_only_ignores_capitals_and_spaces():
    assert is_status_only(status_entry("Out", " Out ", "out", "O"))


def test_an_empty_comment_is_status_only():
    assert is_status_only(status_entry("Active", "", "active", "A"))


def test_a_null_comment_is_status_only():
    """ESPN could send null (None in Python) instead of leaving a field out:
    a crash here would stop every check at the same entry, forever."""
    assert is_status_only(status_entry("Out", None, "out", "O"))


def test_null_type_fields_dont_crash():
    entry = report("Out", "Sweat (knee) is out.")
    entry["type"] = None
    assert not is_status_only(entry)

    entry["type"] = {"description": None, "abbreviation": None}
    assert not is_status_only(entry)


def test_a_real_comment_is_not_status_only():
    entry = status_entry(
        "Questionable",
        "Moore (ankle) didn't practice Thursday.",
        "questionable",
        "Q",
    )

    assert not is_status_only(entry)


@pytest.mark.parametrize("entry", [QUESTIONABLE, INJURED_RESERVE])
def test_status_only_entries_are_not_posted(entry):
    """Even with an injury status: ESPN re-issues these with new IDs, and they
    can flip between two statuses (Out, IR, Out...)."""
    assert not is_injury_news(status_entry(*entry))


# player_id


def test_player_id_comes_from_the_player_link():
    assert player_id(report()) == 4363538


def test_player_id_is_none_without_a_link():
    assert player_id(report(links=[])) is None


# format_update


def test_format_update_with_a_player_link():
    message = format_update(report("Questionable", "Sweat (knee) is questionable."))

    assert message == (
        "**Montez Sweat** (CHI, DE): Questionable\n"
        "Sweat (knee) is questionable.\n"
        f"<{PLAYER_PAGE}>"
    )


def test_format_update_links_the_injuries_page_without_a_player_link():
    message = format_update(report(links=[]))

    assert message.endswith(f"<{INJURIES_PAGE}>")


def test_format_update_pings_the_owner():
    message = format_update(report(), owner=123456789)

    assert message.endswith(f"<{PLAYER_PAGE}>\n<@123456789>")


def test_format_update_without_an_owner_has_no_ping():
    assert "<@" not in format_update(report())


# new_reports and advance_marker

MINUTE = "2026-10-04T15:43Z"
LATER = "2026-10-04T15:44Z"
EARLIER = "2026-10-04T15:42Z"


def test_new_reports_are_the_unhandled_ones_oldest_first():
    reports = [
        report(report_id="later", date=LATER),
        report(report_id="old", date=EARLIER),
        report(report_id="handled", date=MINUTE),
        report(report_id="same minute, new", date=MINUTE),
    ]

    fresh = new_reports(reports, MINUTE, {"handled"})

    assert [r["id"] for r in fresh] == ["same minute, new", "later"]


def test_new_reports_with_nothing_new():
    reports = [report(report_id="handled", date=MINUTE)]

    assert new_reports(reports, MINUTE, {"handled"}) == []


def test_advance_marker_to_a_later_minute_starts_a_new_id_list():
    assert advance_marker(MINUTE, {"a", "b"}, report(report_id="c", date=LATER)) == (
        LATER,
        {"c"},
    )


def test_advance_marker_in_the_same_minute_adds_the_id():
    assert advance_marker(MINUTE, {"a"}, report(report_id="b", date=MINUTE)) == (
        MINUTE,
        {"a", "b"},
    )


# seconds_until_next_check

OSLO = pytz.timezone("Europe/Oslo")


def test_sunday_is_a_game_day():
    assert seconds_until_next_check(OSLO.localize(datetime(2026, 10, 4, 15, 0))) == (
        GAME_DAY_CHECK_SECONDS
    )


def test_wednesday_is_not_a_game_day():
    assert seconds_until_next_check(OSLO.localize(datetime(2026, 10, 7, 15, 0))) == (
        OTHER_DAY_CHECK_SECONDS
    )


def test_thursday_night_game_counts_when_its_already_friday_in_oslo():
    """02:30 Friday in Oslo is 20:30 Thursday in New York: game time."""
    assert seconds_until_next_check(OSLO.localize(datetime(2026, 10, 9, 2, 30))) == (
        GAME_DAY_CHECK_SECONDS
    )


# The cog: one check

SWEAT_ID = 4363538  # the player in report()'s link
OTHER_PAGE = "https://www.espn.com/nfl/player/_/id/111/someone-else"


def make_cog(monkeypatch, reports, last_minute=MINUTE, ids_at_last_minute=None):
    """A PlayerUpdates cog with ESPN, the spreadsheet and Discord faked.

    `cog.saved` lists the marker each time it's saved, as (minute, ids)."""
    monkeypatch.setattr(player_updates, "PLAYER_UPDATES_CHANNEL_ID", 555)
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    monkeypatch.setattr(player_updates, "get_text_channel", lambda bot, cid: channel)
    monkeypatch.setattr(
        player_updates,
        "fetch_json",
        AsyncMock(return_value={"injuries": [{"injuries": reports}]}),
    )

    cog = PlayerUpdates.__new__(PlayerUpdates)  # skips __init__: no background task
    cog.bot = MagicMock()
    cog.last_minute = last_minute
    cog.ids_at_last_minute = ids_at_last_minute or set()
    cog._failing = False
    cog.owners = {}
    cog._owners_loaded_at = None
    cog._owners_failing = False
    cog._notify_admin = AsyncMock()
    cog._refresh_owners = AsyncMock()
    cog._load_marker = AsyncMock()
    cog.saved = []

    async def save_marker():
        cog.saved.append((cog.last_minute, set(cog.ids_at_last_minute)))

    cog._save_marker = save_marker
    return cog, channel


def sent_messages(channel):
    return [call.args[0] for call in channel.send.await_args_list]


def injury(report_id, date, comment="Sweat (knee) is questionable.", **kwargs):
    return report("Questionable", comment, report_id=report_id, date=date, **kwargs)


@pytest.mark.asyncio
async def test_first_start_posts_nothing_and_saves_the_marker(monkeypatch):
    """Otherwise the channel gets two weeks' worth of reports at once."""
    reports = [
        injury("old", EARLIER),
        injury("a", MINUTE),
        injury("b", MINUTE),
    ]
    cog, channel = make_cog(monkeypatch, reports, last_minute=None)

    await cog.check_once()

    channel.send.assert_not_awaited()
    assert cog.saved == [(MINUTE, {"a", "b"})]


@pytest.mark.asyncio
async def test_new_injury_reports_are_posted_oldest_first_and_saved(monkeypatch):
    reports = [
        injury("newest", LATER, "Sweat (knee) is out."),
        injury("handled", MINUTE, "Sweat (knee) was handled."),
        injury("newer", MINUTE, "Sweat (knee) is doubtful."),
    ]
    cog, channel = make_cog(monkeypatch, reports, ids_at_last_minute={"handled"})

    await cog.check_once()

    sent = sent_messages(channel)
    assert len(sent) == 2
    assert "doubtful" in sent[0] and "out" in sent[1]
    assert cog.saved == [(LATER, {"newest"})]


@pytest.mark.asyncio
async def test_a_stat_line_is_not_posted_but_moves_the_marker(monkeypatch):
    """Or the next check would look at it again, forever."""
    stat_line = report("Active", "Sweat recorded five tackles.", "stats", LATER)
    cog, channel = make_cog(monkeypatch, [stat_line])

    await cog.check_once()

    channel.send.assert_not_awaited()
    assert cog.saved == [(LATER, {"stats"})]


@pytest.mark.asyncio
async def test_a_status_only_entry_is_not_posted_but_moves_the_marker(
    monkeypatch,
):
    repeat = status_entry(*QUESTIONABLE, report_id="-2031957", date=LATER)
    cog, channel = make_cog(monkeypatch, [repeat])

    await cog.check_once()

    channel.send.assert_not_awaited()
    assert cog.saved == [(LATER, {"-2031957"})]


@pytest.mark.asyncio
async def test_nothing_new_saves_nothing(monkeypatch):
    cog, channel = make_cog(
        monkeypatch, [injury("handled", MINUTE)], ids_at_last_minute={"handled"}
    )

    await cog.check_once()

    channel.send.assert_not_awaited()
    assert cog.saved == []


@pytest.mark.asyncio
async def test_progress_is_saved_when_discord_fails_halfway(monkeypatch):
    """Reports that were posted must not be posted again after a restart."""
    reports = [injury("first", LATER), injury("second", "2026-10-04T15:45Z")]
    cog, channel = make_cog(monkeypatch, reports)
    channel.send.side_effect = [None, discord.HTTPException(MagicMock(), "down")]

    with pytest.raises(discord.HTTPException):
        await cog.check_once()

    assert cog.saved == [(LATER, {"first"})]


# Pinging the manager


@pytest.mark.asyncio
async def test_a_rostered_players_report_pings_the_manager(monkeypatch):
    cog, channel = make_cog(monkeypatch, [injury("new", LATER)])
    cog.owners = {SWEAT_ID: 999}

    await cog.check_once()

    assert sent_messages(channel)[0].endswith("\n<@999>")


@pytest.mark.asyncio
async def test_an_unrostered_players_report_pings_nobody(monkeypatch):
    other = [{"rel": ["playercard"], "href": OTHER_PAGE}]
    cog, channel = make_cog(monkeypatch, [injury("new", LATER, links=other)])
    cog.owners = {SWEAT_ID: 999}

    await cog.check_once()

    assert "<@" not in sent_messages(channel)[0]


def team(team_id, *player_ids):
    return SimpleNamespace(
        team_id=team_id, roster=[SimpleNamespace(playerId=p) for p in player_ids]
    )


def fake_league(monkeypatch, discord_ids, teams=None, error=None):
    """Fakes discord_ids.json (None: no file) and ESPN's league (or its error).
    Returns a list that counts the calls to get_league."""
    calls = []

    def load_discord_ids():
        if discord_ids is None:
            raise FileNotFoundError("discord_ids.json")
        return discord_ids

    def get_league():
        calls.append(1)
        if error is not None:
            raise error
        return SimpleNamespace(teams=teams or [])

    monkeypatch.setattr(player_updates, "load_discord_ids", load_discord_ids)
    monkeypatch.setattr(player_updates, "get_league", get_league)
    return calls


def real_refresh_owners(cog):
    """make_cog fakes _refresh_owners; these tests need the real one."""
    cog._refresh_owners = PlayerUpdates._refresh_owners.__get__(cog)


@pytest.mark.asyncio
async def test_owners_map_each_rostered_player_to_the_manager(monkeypatch):
    fake_league(
        monkeypatch,
        {1: 111},  # team 2's manager isn't in discord_ids.json
        teams=[team(1, SWEAT_ID, 22), team(2, 33)],
    )
    cog, _ = make_cog(monkeypatch, [])
    real_refresh_owners(cog)

    await cog._refresh_owners()

    assert cog.owners == {SWEAT_ID: 111, 22: 111}


@pytest.mark.asyncio
async def test_without_discord_ids_nobody_is_pinged(monkeypatch):
    calls = fake_league(monkeypatch, None, teams=[team(1, SWEAT_ID)])
    cog, channel = make_cog(monkeypatch, [injury("new", LATER)])
    real_refresh_owners(cog)

    await cog.check_once()

    assert cog.owners == {}
    assert calls == []  # no reason to ask ESPN
    assert "<@" not in sent_messages(channel)[0]


@pytest.mark.asyncio
async def test_report_is_posted_without_ping_when_the_league_fails(monkeypatch):
    """The ping is secondary: the injury news still goes out."""
    fake_league(
        monkeypatch, {1: 111}, error=requests.exceptions.ConnectionError("down")
    )
    cog, channel = make_cog(monkeypatch, [injury("new", LATER)])
    real_refresh_owners(cog)

    await cog.check_once()

    sent = sent_messages(channel)
    assert len(sent) == 1 and "<@" not in sent[0]
    cog._notify_admin.assert_awaited_once()


@pytest.mark.asyncio
async def test_league_failure_tells_admin_once_and_keeps_the_old_list(monkeypatch):
    fake_league(
        monkeypatch, {1: 111}, error=requests.exceptions.ConnectionError("down")
    )
    cog, _ = make_cog(monkeypatch, [])
    real_refresh_owners(cog)
    cog.owners = {SWEAT_ID: 111}  # loaded earlier

    await cog._refresh_owners()
    cog._owners_loaded_at -= ROSTER_REFRESH_SECONDS + 1  # half an hour later
    await cog._refresh_owners()

    cog._notify_admin.assert_awaited_once()
    assert cog.owners == {SWEAT_ID: 111}


@pytest.mark.asyncio
async def test_rosters_are_reloaded_only_every_30_minutes(monkeypatch):
    calls = fake_league(monkeypatch, {1: 111}, teams=[team(1, SWEAT_ID)])
    cog, _ = make_cog(monkeypatch, [])
    real_refresh_owners(cog)

    await cog._refresh_owners()
    await cog._refresh_owners()
    assert len(calls) == 1

    cog._owners_loaded_at = time.monotonic() - ROSTER_REFRESH_SECONDS - 1
    await cog._refresh_owners()
    assert len(calls) == 2


# Loading and saving the marker


def cog_with_tab(cells):
    """A cog whose state tab returns `cells` for A2:B2."""
    cog = PlayerUpdates.__new__(PlayerUpdates)
    tab = MagicMock()
    tab.get.return_value = cells
    cog._state_tab = AsyncMock(return_value=tab)
    return cog, tab


@pytest.mark.asyncio
async def test_load_marker_from_an_empty_tab():
    cog, _ = cog_with_tab([])

    await cog._load_marker()

    assert cog.last_minute is None
    assert cog.ids_at_last_minute == set()


@pytest.mark.asyncio
async def test_load_marker_with_a_minute_and_ids():
    cog, _ = cog_with_tab([[MINUTE, "-2028223,4471"]])

    await cog._load_marker()

    assert cog.last_minute == MINUTE
    assert cog.ids_at_last_minute == {"-2028223", "4471"}


@pytest.mark.asyncio
async def test_load_marker_when_google_leaves_out_the_empty_ids_cell():
    """Google drops empty cells at the end of a row: the row has one item."""
    cog, _ = cog_with_tab([[MINUTE]])

    await cog._load_marker()

    assert cog.last_minute == MINUTE
    assert cog.ids_at_last_minute == set()


@pytest.mark.asyncio
async def test_save_marker_writes_minute_and_ids_as_text():
    cog, tab = cog_with_tab([])
    cog.last_minute = MINUTE
    cog.ids_at_last_minute = {"b", "a"}

    await cog._save_marker()

    tab.update.assert_called_once_with([[MINUTE, "a,b"]], "A2")


# Failures and the admin channel


@pytest.mark.asyncio
async def test_failure_tells_admin_once_then_recovery(monkeypatch):
    cog, _ = make_cog(monkeypatch, [])
    cog.check_once = AsyncMock(side_effect=RuntimeError("ESPN down"))

    first = await cog._run_check()
    await cog._run_check()

    assert first == ERROR_BACKOFF_SECONDS
    cog._notify_admin.assert_awaited_once()

    cog.check_once = AsyncMock()
    monkeypatch.setattr(player_updates, "seconds_until_next_check", lambda now: 60)
    assert await cog._run_check() == 60
    assert cog._notify_admin.await_count == 2  # "works again"


# Switched on or off


def test_feature_off_without_channel(monkeypatch):
    monkeypatch.setattr(player_updates, "PLAYER_UPDATES_CHANNEL_ID", None)
    bot = MagicMock()

    cog = PlayerUpdates(bot)

    bot.loop.create_task.assert_not_called()
    assert cog.task is None


def test_feature_on_with_channel(monkeypatch):
    monkeypatch.setattr(player_updates, "PLAYER_UPDATES_CHANNEL_ID", 555)
    bot = MagicMock()
    # Close the coroutine instead of running it, so Python doesn't warn about it.
    bot.loop.create_task.side_effect = lambda coroutine: coroutine.close()

    PlayerUpdates(bot)

    bot.loop.create_task.assert_called_once()
