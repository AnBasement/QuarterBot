"""Posts NFL injury and status reports to PLAYER_UPDATES_CHANNEL_ID."""

import re
from core.utils.espn_site import parse_espn_date
from typing import Any

from data.messages import PLAYER_UPDATE_TEMPLATE

INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
INJURIES_PAGE = "https://www.espn.com/nfl/injuries"  # fallback if no player page
INJURY_STATUSES = {"Questionable", "Doubtful", "Out", "Injured Reserve"}
# ESPN's reports write an injury right after the player's name: "Sweat (groin)
# is active...". Up to four words of the player's name, then the brackets.
# One word of a player's name: either it starts with a capital letter
# ("Sweat", "St.", "Smith-Njigba"), or it's one of the small lowercase
# words some surnames have ("van" and "den" in "van den Berg").
NAME_WORD = r"(?:[A-Z][\w.'\-]*|van|von|der|den|de|da|di|du|la|le|del)"
NAME_THEN_BRACKETS = re.compile(rf"^(?:{NAME_WORD}\s){{0,3}}{NAME_WORD} \(([^)]+)\)")
INACTIVE = re.compile(r"\binactive\b", re.IGNORECASE)
PLAYER_ID_IN_LINK = re.compile(r"/id/(\d+)")  # e.g. ".../player/_/id/4363538/..."


def is_injury_news(report: dict[str, Any]) -> bool:
    """Whether a report is worth posting: an injury status, or an "Active"
    report that names an injury (cleared to play, practice updates). Not the
    after-game stat lines, not coaches' decisions, and not the plain entries
    of the official inactive lists."""
    comment = report.get("shortComment", "")
    match = NAME_THEN_BRACKETS.match(comment)
    if match and match.group(1).lower() == "coach's decision":
        return False
    if match is None and INACTIVE.search(comment):
        return False  # a plain "inactive": no injury named
    return report.get("status") in INJURY_STATUSES or match is not None


def player_id(report: dict[str, Any]) -> int | None:
    """The player's ESPN ID, read from the link to their ESPN page. The same ID
    the fantasy league uses for its players."""
    for link_info in report.get("athlete", {}).get("links", []):
        found = PLAYER_ID_IN_LINK.search(link_info.get("href", ""))
        if found:
            return int(found.group(1))
    return None


def format_update(report: dict[str, Any], owner: int | None = None) -> str:
    """The Discord message for one report, with a ping for `owner` (a Discord
    user ID) if the player is on a fantasy team."""
    athlete = report.get("athlete", {})
    link = next(
        (
            link_info["href"]
            for link_info in athlete.get("links", [])
            if "playercard" in link_info.get("rel", [])
        ),
        INJURIES_PAGE,
    )
    message = PLAYER_UPDATE_TEMPLATE.format(
        player=athlete.get("displayName", "?"),
        team=athlete.get("team", {}).get("abbreviation", "?"),
        position=athlete.get("position", {}).get("abbreviation", "?"),
        status=report.get("status", "?"),
        comment=report.get("shortComment", ""),
        link=link,
    )
    if owner is not None:
        message += f"\n<@{owner}>"
    return message


def new_reports(
    reports: list[dict[str, Any]], last_minute: str, ids_at_last_minute: set[str]
) -> list[dict[str, Any]]:
    """The reports not handled yet, oldest first: from a later minute than
    `last_minute`, or from that minute but not in `ids_at_last_minute`."""
    last = parse_espn_date(last_minute)
    fresh = [
        r
        for r in reports
        if parse_espn_date(r["date"]) > last
        or (parse_espn_date(r["date"]) == last and r["id"] not in ids_at_last_minute)
    ]
    return sorted(fresh, key=lambda r: parse_espn_date(r["date"]))


def advance_marker(
    last_minute: str, ids_at_last_minute: set[str], report: dict[str, Any]
) -> tuple[str, set[str]]:
    """The marker after handling `report`: its minute, and the IDs handled
    from that minute."""
    if parse_espn_date(report["date"]) > parse_espn_date(last_minute):
        return report["date"], {report["id"]}
    return last_minute, ids_at_last_minute | {report["id"]}
