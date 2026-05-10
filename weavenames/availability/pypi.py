"""PyPI availability probe.

PyPI normalizes package names: lowercase, runs of [-_.] collapse to '-'.
We hit pypi.org/pypi/<name>/json — 404 = free, 200 = taken (caller's
interpret layer decides if it's a squatter).
"""

from __future__ import annotations

import httpx

from weavenames.models import AvailabilityResult

PYPI_URL = "https://pypi.org/pypi/{name}/json"
USER_AGENT = "weavenames/0.1 (+https://github.com/weavenames/weavenames)"


def normalize_pypi_name(name: str) -> str:
    """PEP 503 normalization."""

    lowered = name.lower()
    out: list[str] = []
    last_dash = False
    for ch in lowered:
        if ch in "-_.":
            if not last_dash:
                out.append("-")
            last_dash = True
        else:
            out.append(ch)
            last_dash = False
    return "".join(out).strip("-")


async def check_pypi(name: str, client: httpx.AsyncClient) -> AvailabilityResult:
    normalized = normalize_pypi_name(name)
    try:
        r = await client.get(
            PYPI_URL.format(name=normalized),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=15.0,
        )
    except httpx.HTTPError as e:
        return AvailabilityResult(
            registry="pypi", name=name, status="error", detail=str(e)
        )

    if r.status_code == 404:
        return AvailabilityResult(registry="pypi", name=name, status="free")
    if r.status_code == 200:
        # Keep a sanitized subset of the response for the interpret layer.
        try:
            data = r.json()
        except ValueError:
            data = {}
        info = data.get("info", {}) or {}
        releases = data.get("releases", {}) or {}
        compact = {
            "name": info.get("name"),
            "summary": info.get("summary"),
            "home_page": info.get("home_page"),
            "project_urls": info.get("project_urls"),
            "release_count": len(releases),
            "release_versions": list(releases.keys())[-5:],
            # Per-version files carry upload_time; we'll sample the most
            # recent release if any.
            "latest_upload_time": _latest_upload_time(releases),
        }
        return AvailabilityResult(
            registry="pypi", name=name, status="taken", raw=compact
        )
    return AvailabilityResult(
        registry="pypi",
        name=name,
        status="error",
        detail=f"HTTP {r.status_code}",
    )


def _latest_upload_time(releases: dict) -> str | None:
    latest: str | None = None
    for files in releases.values():
        for f in files or []:
            t = f.get("upload_time_iso_8601") or f.get("upload_time")
            if t and (latest is None or t > latest):
                latest = t
    return latest
