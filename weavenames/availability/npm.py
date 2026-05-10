"""npm availability probe.

GET registry.npmjs.org/<name> — 404 = free, 200 = taken. The npm registry
returns the full packument; we keep a small subset for interpret.
"""

from __future__ import annotations

import httpx

from weavenames.models import AvailabilityResult

NPM_URL = "https://registry.npmjs.org/{name}"
USER_AGENT = "weavenames/0.1 (+https://github.com/weavenames/weavenames)"


def normalize_npm_name(name: str) -> str:
    """npm enforces lowercase, no leading dot/underscore, URL-safe chars."""

    return name.lower()


async def check_npm(name: str, client: httpx.AsyncClient) -> AvailabilityResult:
    normalized = normalize_npm_name(name)
    try:
        r = await client.get(
            NPM_URL.format(name=normalized),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=15.0,
        )
    except httpx.HTTPError as e:
        return AvailabilityResult(
            registry="npm", name=name, status="error", detail=str(e)
        )

    if r.status_code == 404:
        return AvailabilityResult(registry="npm", name=name, status="free")
    if r.status_code == 200:
        try:
            data = r.json()
        except ValueError:
            data = {}
        time = data.get("time", {}) or {}
        repo = data.get("repository") or {}
        versions = data.get("versions", {}) or {}
        compact = {
            "name": data.get("name"),
            "description": data.get("description"),
            "repository": repo if isinstance(repo, dict) else {"url": repo},
            "homepage": data.get("homepage"),
            "version_count": len(versions),
            "modified": time.get("modified"),
            "created": time.get("created"),
        }
        return AvailabilityResult(
            registry="npm", name=name, status="taken", raw=compact
        )
    return AvailabilityResult(
        registry="npm",
        name=name,
        status="error",
        detail=f"HTTP {r.status_code}",
    )
