"""Tests for cogs/player_updates.py."""

import pytest

from cogs.player_updates import (
    INJURIES_PAGE,
    advance_marker,
    format_update,
    is_injury_news,
    new_reports,
    player_id,
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
