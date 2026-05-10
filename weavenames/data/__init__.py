"""Static data files for weavenames.

Each loader returns a freshly-parsed dict / list. Files are small (~KB), so
re-reading per call is fine and avoids stale-cache-after-update headaches.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=1)
def known_packages() -> list[str]:
    raw = files(__package__).joinpath("known_packages.json").read_text(encoding="utf-8")
    return [p.lower() for p in json.loads(raw)["packages"]]


@lru_cache(maxsize=1)
def collisions() -> set[str]:
    raw = files(__package__).joinpath("collisions.json").read_text(encoding="utf-8")
    return {c.lower() for c in json.loads(raw)["collisions"]}


@lru_cache(maxsize=1)
def github_reserved() -> set[str]:
    raw = files(__package__).joinpath("github_reserved.json").read_text(encoding="utf-8")
    return {c.lower() for c in json.loads(raw)["reserved"]}
