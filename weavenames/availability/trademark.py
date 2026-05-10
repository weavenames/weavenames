"""USPTO trademark probe.

USPTO TSDR is the structured API but requires a key for full access. The
public TESS site doesn't expose a clean API — workable approaches are:

  1) USPTO TMSearch JSON endpoint (used by the public TM Search UI). It's
     not officially documented but is publicly accessible and returns
     structured results. Filter by Class 9 (computer software, downloadable)
     and Class 42 (SaaS, computer services).
  2) Fall back to "skipped + flag" if the endpoint changes shape, since
     trademark proximity is a flag, not a reject.

Bounded to top 30 candidates by the orchestrator. We never reject —
return a list of close matches and let the human read the report.
"""

from __future__ import annotations

import httpx

from weavenames.models import AvailabilityResult, TrademarkHit

# Public TM Search endpoint used by tmsearch.uspto.gov. It's documented
# only by reverse-engineering the UI; if it changes shape we degrade
# gracefully to a "skipped" status.
TM_SEARCH_URL = "https://tmsearch.uspto.gov/api-v1-0-0/tmsearch"
USER_AGENT = "weavenames/0.1 (+https://github.com/weavenames/weavenames)"

RELEVANT_CLASSES = {"009", "042", "9", "42"}


async def check_trademark(
    name: str,
    client: httpx.AsyncClient,
    *,
    classes: set[str] = RELEVANT_CLASSES,
) -> AvailabilityResult:
    """Look up trademark hits for a name. Flag, don't reject."""

    payload = {
        # The query string follows USPTO TMSearch syntax. We search the
        # mark text for an exact-or-similar match.
        "query": f'WM:"{name}"',
        "rows": 10,
        "highlights": False,
    }
    try:
        r = await client.post(
            TM_SEARCH_URL,
            json=payload,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=20.0,
        )
    except httpx.HTTPError as e:
        return AvailabilityResult(
            registry="trademark",
            name=name,
            status="error",
            detail=str(e),
        )

    if r.status_code != 200:
        # The endpoint sometimes returns 403 or 503 under load, or the
        # path changes. Treat as skipped so we don't block the pipeline.
        return AvailabilityResult(
            registry="trademark",
            name=name,
            status="skipped",
            detail=f"USPTO TMSearch HTTP {r.status_code}; treat as unverified",
        )

    try:
        data = r.json()
    except ValueError:
        return AvailabilityResult(
            registry="trademark",
            name=name,
            status="skipped",
            detail="USPTO TMSearch returned non-JSON",
        )

    hits = _parse_hits(data, classes)
    if not hits:
        return AvailabilityResult(registry="trademark", name=name, status="free")

    return AvailabilityResult(
        registry="trademark",
        name=name,
        status="taken",
        flags=[f"{len(hits)} TM hit(s) in classes {sorted(classes)}"],
        raw={"hits": [h.model_dump() for h in hits]},
    )


def _parse_hits(data: dict, classes: set[str]) -> list[TrademarkHit]:
    """Parse USPTO TMSearch response shape (best-effort).

    The exact shape isn't documented; observed responses use a 'hits' key
    with each entry containing 'mark' and 'classes'. We defensively pull
    the fields we know.
    """

    out: list[TrademarkHit] = []
    raw_hits = (
        data.get("hits") or data.get("results") or data.get("docs") or []
    )
    for h in raw_hits:
        if not isinstance(h, dict):
            continue
        mark = (
            h.get("mark")
            or h.get("wordmark")
            or h.get("WM")
            or h.get("markIdentification")
            or ""
        )
        if not mark:
            continue
        cls_raw = (
            h.get("classes")
            or h.get("internationalClasses")
            or h.get("classCodes")
            or []
        )
        cls = {str(c).zfill(3) if str(c).isdigit() else str(c) for c in cls_raw}
        # Only surface trademarks in classes we care about. If the response
        # doesn't include class info, surface the hit anyway — over-flagging
        # is fine.
        if cls and not (cls & classes):
            continue
        out.append(
            TrademarkHit(
                serial_number=str(h.get("serial") or h.get("serialNumber") or "") or None,
                mark=str(mark),
                status=str(h.get("status") or h.get("statusDescription") or "") or None,
                classes=sorted(cls),
                owner=str(h.get("owner") or h.get("ownerName") or "") or None,
            )
        )
    return out
