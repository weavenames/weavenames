"""Interpretation layer — make raw availability signals tell the truth.

Squatter detection on PyPI/npm: a 200 response means "someone registered
the name," not "this name is unusable." Real-world: many namespaces are
crammed with abandoned cybersquatter packages with zero real adoption.
We re-classify those as `soft_claimed` so the report flags them and the
human gets a better signal.

Reserved-name filter on GitHub: a 404 doesn't always mean free. GitHub
reserves dozens of usernames at the platform level. We re-classify those
as `reserved`.

Domain pending-delete: handled in availability/domains.py at probe time.

Premium-domain detection: Fastly Domain Research API (10K free/month).
Only run on the top N candidates (default 30) because it counts against
the free quota. If the API token is not set, we skip silently.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx

from weavenames.data import github_reserved
from weavenames.models import AvailabilityResult, Candidate

USER_AGENT = "weavenames/0.1 (+https://github.com/weavenames/weavenames)"
SQUATTER_INACTIVITY_DAYS = 730  # 2 years
# TODO Phase 2: 90-day download-count signal via pypistats.org. The /pypi/<name>/json
# endpoint does not return download stats; we'd need a separate
# `pypistats.org/api/packages/<name>/recent` call. Deferred — the 2-year inactivity
# signal above is the working squatter heuristic for v1.


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # Python 3.11+ handles 'Z' and most ISO variants natively.
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def looks_like_squatter_pypi(raw: dict | None) -> tuple[bool, list[str]]:
    """Heuristic: very few releases, no GitHub link, last upload >2yr ago."""

    if not raw:
        return (False, [])
    flags: list[str] = []

    project_urls = raw.get("project_urls") or {}
    home_page = raw.get("home_page") or ""
    has_github = "github.com" in (home_page or "").lower() or any(
        "github.com" in str(v).lower() for v in project_urls.values()
    )
    release_count = raw.get("release_count") or 0
    last_upload = _parse_iso(raw.get("latest_upload_time"))
    now = datetime.now(timezone.utc)

    score = 0
    if not has_github:
        score += 1
        flags.append("no GitHub link")
    if release_count <= 1:
        score += 1
        flags.append(f"{release_count} release(s)")
    if last_upload and (now - last_upload) > timedelta(days=SQUATTER_INACTIVITY_DAYS):
        score += 1
        flags.append(f"last release > {SQUATTER_INACTIVITY_DAYS}d ago")

    return (score >= 2, flags)


def looks_like_squatter_npm(raw: dict | None) -> tuple[bool, list[str]]:
    if not raw:
        return (False, [])
    flags: list[str] = []
    repo = raw.get("repository") or {}
    repo_url = repo.get("url") if isinstance(repo, dict) else str(repo)
    has_repo = bool(repo_url and "github.com" in (repo_url or "").lower())
    version_count = raw.get("version_count") or 0
    modified = _parse_iso(raw.get("modified"))
    now = datetime.now(timezone.utc)

    score = 0
    if not has_repo:
        score += 1
        flags.append("no GitHub link")
    if version_count <= 1:
        score += 1
        flags.append(f"{version_count} version(s)")
    if modified and (now - modified) > timedelta(days=SQUATTER_INACTIVITY_DAYS):
        score += 1
        flags.append(f"last modified > {SQUATTER_INACTIVITY_DAYS}d ago")
    return (score >= 2, flags)


def reinterpret_pypi(result: AvailabilityResult) -> AvailabilityResult:
    if result.status != "taken":
        return result
    is_soft, flags = looks_like_squatter_pypi(result.raw)
    if is_soft:
        return result.model_copy(
            update={
                "status": "soft_claimed",
                "flags": [*result.flags, "squatter-like", *flags],
            }
        )
    return result


def reinterpret_npm(result: AvailabilityResult) -> AvailabilityResult:
    if result.status != "taken":
        return result
    is_soft, flags = looks_like_squatter_npm(result.raw)
    if is_soft:
        return result.model_copy(
            update={
                "status": "soft_claimed",
                "flags": [*result.flags, "squatter-like", *flags],
            }
        )
    return result


def reinterpret_github_user(result: AvailabilityResult) -> AvailabilityResult:
    if result.status != "free":
        return result
    if result.name.lower() in github_reserved():
        return result.model_copy(
            update={
                "status": "reserved",
                "flags": [*result.flags, "GitHub reserved name"],
            }
        )
    return result


def reinterpret_candidate(candidate: Candidate) -> Candidate:
    """Apply all interpret rules to a single candidate's availability map."""

    out: dict[str, AvailabilityResult] = {}
    for registry, result in candidate.availability.items():
        if registry == "pypi":
            out[registry] = reinterpret_pypi(result)
        elif registry == "npm":
            out[registry] = reinterpret_npm(result)
        elif registry == "github_user":
            out[registry] = reinterpret_github_user(result)
        else:
            out[registry] = result
    return candidate.model_copy(update={"availability": out})


# --- Fastly premium domain check ---

FASTLY_URL = "https://domain-research.fastly.com/v1/domain/{domain}"


async def fastly_premium_check(
    candidate: Candidate,
    tlds: list[str] | tuple[str, ...],
    client: httpx.AsyncClient,
    *,
    api_token: str | None = None,
) -> Candidate:
    """Refine `domain:*` results with Fastly's premium-pricing signal.

    Skips silently if no token is set. Only writes back when we get useful
    info — never downgrades a 'free' to anything worse without evidence.
    """

    api_token = api_token or os.environ.get("FASTLY_API_TOKEN")
    if not api_token:
        return candidate

    out = dict(candidate.availability)
    for tld in tlds:
        registry = f"domain:{tld}"
        existing = out.get(registry)
        # Only refine free results — taken/available_soon already give us
        # what we need from RDAP.
        if not existing or existing.status != "free":
            continue
        domain = existing.name  # fully-qualified, e.g. "weavenames.com"
        try:
            r = await client.get(
                FASTLY_URL.format(domain=domain),
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Authorization": f"Bearer {api_token}",
                },
                timeout=20.0,
            )
        except httpx.HTTPError as e:
            # Don't blow up the run if Fastly is unhappy.
            existing = existing.model_copy(
                update={"flags": [*existing.flags, f"fastly error: {e}"]}
            )
            out[registry] = existing
            continue

        if r.status_code != 200:
            existing = existing.model_copy(
                update={"flags": [*existing.flags, f"fastly HTTP {r.status_code}"]}
            )
            out[registry] = existing
            continue

        try:
            data = r.json()
        except ValueError:
            continue

        # Fastly's exact response shape isn't well documented. We
        # defensively look for premium / aftermarket flags on common keys.
        # If the shape changes, the worst case is we miss the flag — we
        # never erase the underlying RDAP signal.
        is_premium = bool(
            data.get("premium")
            or data.get("is_premium")
            or data.get("aftermarket")
            or (data.get("price") and float(data.get("price") or 0) > 100)
        )
        if is_premium:
            out[registry] = existing.model_copy(
                update={
                    "status": "premium",
                    "flags": [*existing.flags, "premium / aftermarket"],
                    "raw": data if isinstance(data, dict) else None,
                }
            )

    return candidate.model_copy(update={"availability": out})
