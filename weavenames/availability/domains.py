"""Domain availability via direct RDAP through `whodap`.

Critical: do NOT use rdap.org. It's Cloudflare-rate-limited at 1 req/sec
and a 1,200-check run takes ~20 minutes. `whodap` uses the IANA bootstrap
(`data.iana.org/rdap/dns.json`) to hit each TLD's registry directly. We
gate per-TLD with an asyncio.Semaphore at 5 concurrent.

Status interpretation (RFC 8056):
  - NotFoundError → "free"
  - Found, status includes 'pending delete' / 'redemption period' →
    "available_soon"
  - Otherwise → "taken"

The Fastly Domain Research API (interpret.py) refines the top candidates
to flag premium / aftermarket pricing, which RDAP cannot tell us.
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict

import httpx
import whodap
from whodap.errors import (
    BadStatusCode,
    MalformedQueryError,
    NotFoundError,
    RateLimitError,
    WhodapError,
)

from weavenames.models import AvailabilityResult

DEFAULT_TLDS = (".dev", ".io", ".ai", ".com")
PER_TLD_CONCURRENCY = 5

# Module-level semaphore registry, keyed by TLD. Created lazily on first
# request inside the running event loop (asyncio.Semaphore must be bound to
# the loop it's used on).
_semaphores: dict[str, asyncio.Semaphore] = {}


def _semaphore_for(tld: str) -> asyncio.Semaphore:
    sem = _semaphores.get(tld)
    if sem is None:
        sem = asyncio.Semaphore(PER_TLD_CONCURRENCY)
        _semaphores[tld] = sem
    return sem


def reset_semaphores() -> None:
    """Drop existing semaphores. Call between event loops if you reuse the process."""

    _semaphores.clear()


def _strip_dot(tld: str) -> str:
    return tld[1:] if tld.startswith(".") else tld


def _extract_status(resp: object) -> list[str]:
    """RDAP `status` is an array of strings; whodap exposes it as `.status`."""

    raw = getattr(resp, "status", None) or []
    if isinstance(raw, str):
        return [raw.lower()]
    return [str(s).lower() for s in raw]


# RFC 8056 / EPP statuses that mean the domain is on its way back to free.
PENDING_STATUSES = {
    "pending delete",
    "pendingdelete",
    "redemption period",
    "redemptionperiod",
    "pending restore",
    "pendingrestore",
}


async def check_domain(
    name: str,
    tld: str,
    client: httpx.AsyncClient,
) -> AvailabilityResult:
    """Run a single RDAP lookup with per-TLD concurrency limit."""

    registry = f"domain:{tld}"
    bare = name.lower()
    tld_clean = _strip_dot(tld)
    domain = f"{bare}.{tld_clean}"

    sem = _semaphore_for(tld)
    async with sem:
        # One retry on rate limit. Verisign (.com) is the main offender;
        # a single 1.5s pause clears 90% of these in practice.
        for attempt in (0, 1):
            try:
                resp = await whodap.aio_lookup_domain(
                    bare, tld_clean, httpx_client=client
                )
                break
            except NotFoundError:
                return AvailabilityResult(registry=registry, name=domain, status="free")
            except NotImplementedError as e:
                # Some ccTLDs aren't in the IANA RDAP bootstrap (notably
                # .io, administered by NIC.IO without a public RDAP server).
                # Surface as 'skipped' rather than 'error' so the report
                # doesn't make it look like a transient failure.
                return AvailabilityResult(
                    registry=registry,
                    name=domain,
                    status="skipped",
                    detail=f"RDAP not supported for {tld}: {e}",
                )
            except RateLimitError as e:
                if attempt == 0:
                    await asyncio.sleep(1.5)
                    continue
                return AvailabilityResult(
                    registry=registry,
                    name=domain,
                    status="error",
                    detail=f"rate limited: {e}",
                )
            except (MalformedQueryError, BadStatusCode, WhodapError) as e:
                return AvailabilityResult(
                    registry=registry,
                    name=domain,
                    status="error",
                    detail=str(e),
                )
            except (httpx.HTTPError, ValueError) as e:
                return AvailabilityResult(
                    registry=registry,
                    name=domain,
                    status="error",
                    detail=str(e),
                )
        else:
            # Loop exhausted without break — should be unreachable.
            return AvailabilityResult(
                registry=registry, name=domain, status="error", detail="unknown"
            )

    statuses = _extract_status(resp)
    flags = list(statuses)
    if any(s in PENDING_STATUSES for s in statuses):
        return AvailabilityResult(
            registry=registry,
            name=domain,
            status="available_soon",
            flags=flags,
        )
    return AvailabilityResult(
        registry=registry, name=domain, status="taken", flags=flags
    )


async def check_domains(
    name: str,
    tlds: tuple[str, ...] | list[str],
    client: httpx.AsyncClient,
) -> dict[str, AvailabilityResult]:
    """Fan out across all TLDs concurrently. Returns map registry → result."""

    results = await asyncio.gather(
        *[check_domain(name, t, client) for t in tlds],
        return_exceptions=True,
    )
    out: dict[str, AvailabilityResult] = {}
    for tld, r in zip(tlds, results):
        registry = f"domain:{tld}"
        if isinstance(r, Exception):
            out[registry] = AvailabilityResult(
                registry=registry,
                name=f"{name.lower()}{tld}",
                status="error",
                detail=str(r),
            )
        else:
            out[registry] = r
    return out


# A regex helper used in tests. Real RFC 8056 mapping happens in
# _extract_status above; keep this as a compatibility shim.
_PENDING_RE = re.compile(r"pending\s*(delete|restore)|redemption\s*period", re.I)


def is_pending(status_strings: list[str]) -> bool:
    return any(_PENDING_RE.search(s) for s in status_strings)


def aggregate_per_tld(results: list[AvailabilityResult]) -> dict[str, list[str]]:
    """Group result statuses by TLD for reporting."""

    out: dict[str, list[str]] = defaultdict(list)
    for r in results:
        out[r.registry].append(r.status)
    return dict(out)
