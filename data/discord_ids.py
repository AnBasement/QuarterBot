"""Loads discord_ids.json: which Discord user owns which ESPN team."""

from __future__ import annotations

import json
from pathlib import Path


def load_discord_ids(path: str | Path = "discord_ids.json") -> dict[int, int]:
    """Returns {ESPN team ID: Discord user ID}."""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Could not find the Discord ID file: {file_path}")

    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read JSON from {file_path}") from exc

    # Valid JSON that isn't {...} (e.g. a list) would otherwise fail with an
    # AttributeError, which the caller doesn't expect.
    if not isinstance(raw, dict):
        raise ValueError(
            'The Discord ID file must have the format {"team_id": "discord_id"}'
        )
    try:
        return {int(key): int(value) for key, value in raw.items()}
    except (TypeError, ValueError) as exc:
        raise ValueError(
            'The Discord ID file must have the format {"team_id": "discord_id"}'
        ) from exc
