"""GitHub user + repo availability probes.

User: api.github.com/users/<name> — 404 = free (or reserved if on the
reserved list, which interpret.py handles). 200 = taken.

Repo: api.github.com/search/repositories?q=<name>+in:name. We don't reject
on repo hits — we surface the count as a flag for the report.

Auth: GITHUB_TOKEN env var lifts unauthenticated 60/hr to authenticated
5K/hr. Without a token the orchestrator skips repo search to avoid burning
the budget on noise.
"""

from __future__ import annotations

import os

import httpx

from weavenames.models import AvailabilityResult

USER_URL = "https://api.github.com/users/{name}"
SEARCH_URL = "https://api.github.com/search/repositories"
USER_AGENT = "weavenames/0.1 (+https://github.com/weavenames/weavenames)"


def _headers() -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def check_github_user(name: str, client: httpx.AsyncClient) -> AvailabilityResult:
    try:
        r = await client.get(
            USER_URL.format(name=name),
            headers=_headers(),
            timeout=15.0,
        )
    except httpx.HTTPError as e:
        return AvailabilityResult(
            registry="github_user", name=name, status="error", detail=str(e)
        )

    if r.status_code == 404:
        # interpret.py upgrades to "reserved" for known reserved names.
        return AvailabilityResult(registry="github_user", name=name, status="free")
    if r.status_code == 200:
        try:
            data = r.json()
        except ValueError:
            data = {}
        compact = {
            "login": data.get("login"),
            "type": data.get("type"),
            "public_repos": data.get("public_repos"),
            "created_at": data.get("created_at"),
        }
        return AvailabilityResult(
            registry="github_user", name=name, status="taken", raw=compact
        )
    if r.status_code in (403, 429):
        return AvailabilityResult(
            registry="github_user",
            name=name,
            status="error",
            detail=f"rate limited (HTTP {r.status_code})",
        )
    return AvailabilityResult(
        registry="github_user",
        name=name,
        status="error",
        detail=f"HTTP {r.status_code}",
    )


async def check_github_repo(name: str, client: httpx.AsyncClient) -> AvailabilityResult:
    """Search for repos with this exact name. Reports a flag, not a reject.

    Without auth this would burn the 60/hr budget very quickly — we skip
    if no token.
    """

    if not os.environ.get("GITHUB_TOKEN"):
        return AvailabilityResult(
            registry="github_repo",
            name=name,
            status="skipped",
            detail="no GITHUB_TOKEN; skipping repo search",
        )

    try:
        r = await client.get(
            SEARCH_URL,
            params={"q": f"{name} in:name", "per_page": "1"},
            headers=_headers(),
            timeout=15.0,
        )
    except httpx.HTTPError as e:
        return AvailabilityResult(
            registry="github_repo", name=name, status="error", detail=str(e)
        )

    if r.status_code in (403, 429):
        return AvailabilityResult(
            registry="github_repo",
            name=name,
            status="error",
            detail=f"rate limited (HTTP {r.status_code})",
        )
    if r.status_code != 200:
        return AvailabilityResult(
            registry="github_repo",
            name=name,
            status="error",
            detail=f"HTTP {r.status_code}",
        )
    try:
        data = r.json()
    except ValueError:
        data = {}
    total = data.get("total_count", 0) or 0
    flags: list[str] = []
    if total > 0:
        flags.append(f"{total} repo(s) match")
    status = "taken" if total > 0 else "free"
    return AvailabilityResult(
        registry="github_repo",
        name=name,
        status=status,
        flags=flags,
        raw={"total_count": total},
    )
