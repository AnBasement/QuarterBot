"""Posts a new version's CHANGELOG notes to CHANGELOG_CHANNEL_ID, once."""

import re

# A released version's heading, e.g. "## [1.2.0] - 11-10-2026".
RELEASE_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\]")


def latest_release(changelog: str) -> tuple[str, str] | None:
    """The newest released version in the CHANGELOG and its notes: the lines
    under its heading, up to the next "## " heading. [Unreleased] is skipped.
    None if nothing has been released yet."""
    lines = changelog.splitlines()
    for index, line in enumerate(lines):
        found = RELEASE_HEADING.match(line)
        if found is None:
            continue
        notes = []
        for following in lines[index + 1 :]:
            if following.startswith("## "):
                break
            notes.append(following)
        return found.group(1), "\n".join(notes).strip()
    return None
