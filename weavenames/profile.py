"""Taste profile — JSON file at ~/.weavenames/profile.json.

Read on every run, fed into the generation prompt. Updated interactively
after each run with accepts / rejects from the top results.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from weavenames.models import TasteProfile

DEFAULT_PROFILE_PATH = Path.home() / ".weavenames" / "profile.json"


def profile_path() -> Path:
    override = os.environ.get("WEAVENAMES_PROFILE")
    if override:
        p = Path(override)
        if not p.is_absolute():
            raise ValueError(
                f"WEAVENAMES_PROFILE must be an absolute path, got: {override!r}"
            )
        return p
    return DEFAULT_PROFILE_PATH


def load(path: Path | None = None) -> TasteProfile:
    """Load profile from disk; return defaults if missing."""

    p = path or profile_path()
    if not p.exists():
        return TasteProfile()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return TasteProfile.model_validate(data)
    except (json.JSONDecodeError, ValueError):
        # If the file is malformed, fall back to defaults rather than crash
        # the whole pipeline. The user can fix it manually.
        return TasteProfile()


def save(profile: TasteProfile, path: Path | None = None) -> Path:
    """Persist profile to disk, creating the parent dir if needed."""

    p = path or profile_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    return p


def record_accept(profile: TasteProfile, name: str) -> None:
    if name not in profile.history.accepted:
        profile.history.accepted.append(name)


def record_reject(profile: TasteProfile, name: str, reason: str | None = None) -> None:
    # Replace existing entry for the same name if present, so the latest
    # reason wins.
    profile.history.rejected = [
        r for r in profile.history.rejected if r.name != name
    ]
    profile.history.rejected.append(
        TasteProfile.HistoryEntry(name=name, reason=reason)
    )
