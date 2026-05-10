"""End-to-end pipeline orchestration.

Glues together generate → filter → availability → interpret → trademark →
rank → score. Stays out of CLI concerns; CLI calls into here.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import httpx
from rich.console import Console

from weavenames.availability.domains import DEFAULT_TLDS, check_domains
from weavenames.availability.github import check_github_repo, check_github_user
from weavenames.availability.npm import check_npm
from weavenames.availability.pypi import check_pypi
from weavenames.availability.trademark import check_trademark
from weavenames.filters import (
    first_reject_reason,
    length_penalty,
    normalize,
    pronounceability_score,
)
from weavenames.generate import GenerationConfig, generate_candidates
from weavenames.interpret import fastly_premium_check, reinterpret_candidate
from weavenames.models import AvailabilityResult, Candidate, TrademarkHit
from weavenames.profile import load as load_profile
from weavenames.rank import RankConfig, rank_candidates

# Composite weights — from spec.
W_AVAILABILITY = 0.5
W_PAIRWISE = 0.25
W_PRONOUNCEABILITY = 0.15
W_LENGTH = 0.10

# Per-registry weight when computing availability_completeness.
REGISTRY_WEIGHTS: dict[str, float] = {
    "pypi": 1.0,
    "npm": 1.0,
    "github_user": 0.7,
    "github_repo": 0.3,
    "trademark": 0.5,
    # Domains weighted by TLD: .com is the king, others count less.
    "domain:.com": 1.0,
    "domain:.dev": 0.7,
    "domain:.io": 0.7,
    "domain:.ai": 0.7,
}
DEFAULT_DOMAIN_WEIGHT = 0.5

# Status → score multiplier in [0, 1]. "free" is best, "taken" is worst.
STATUS_SCORE: dict[str, float] = {
    "free": 1.0,
    "available_soon": 0.6,
    "premium": 0.3,
    "soft_claimed": 0.4,
    "reserved": 0.0,
    "taken": 0.0,
    "error": 0.5,  # neutral
    "skipped": 0.5,  # neutral
}


@dataclass
class PipelineConfig:
    description: str
    keywords: list[str] | None = None
    tlds: tuple[str, ...] = DEFAULT_TLDS
    target_count: int = 200
    top_n_after_filter: int = 60
    top_n_trademark: int = 30
    top_n_report: int = 30
    rank_rounds: int = 4
    skip_trademark: bool = False
    skip_fastly: bool = False
    model: str = "claude-haiku-4-5-20251001"
    rank_model: str = "claude-haiku-4-5-20251001"


def _availability_score(candidate: Candidate) -> float:
    """Weighted score across registries. Higher = more available overall."""

    if not candidate.availability:
        return 0.0
    total_w = 0.0
    total = 0.0
    for registry, result in candidate.availability.items():
        w = REGISTRY_WEIGHTS.get(registry)
        if w is None:
            w = (
                DEFAULT_DOMAIN_WEIGHT
                if registry.startswith("domain:")
                else 0.3
            )
        s = STATUS_SCORE.get(result.status, 0.5)
        total += w * s
        total_w += w
    return total / total_w if total_w else 0.0


def _composite(candidate: Candidate) -> float:
    s = candidate.scores
    return (
        W_AVAILABILITY * s.availability
        + W_PAIRWISE * s.pairwise_fit
        + W_PRONOUNCEABILITY * s.pronounceability
        - W_LENGTH * s.length_penalty
    )


async def _check_one(
    candidate: Candidate,
    client: httpx.AsyncClient,
    tlds: tuple[str, ...] | list[str],
) -> Candidate:
    """Run all parallel availability probes for a single candidate."""

    coros = [
        check_pypi(candidate.name, client),
        check_npm(candidate.name, client),
        check_github_user(candidate.name, client),
        check_github_repo(candidate.name, client),
    ]
    results: list[AvailabilityResult | BaseException] = await asyncio.gather(
        *coros, return_exceptions=True
    )
    domain_map = await check_domains(candidate.name, tlds, client)

    avail: dict[str, AvailabilityResult] = {}
    for r in results:
        if isinstance(r, BaseException):
            continue
        avail[r.registry] = r
    avail.update(domain_map)
    candidate.availability = avail
    return candidate


async def _availability_phase(
    candidates: list[Candidate],
    tlds: tuple[str, ...],
    *,
    console: Console | None = None,
) -> list[Candidate]:
    timeout = httpx.Timeout(20.0, connect=10.0)
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        tasks = [_check_one(c, client, tlds) for c in candidates]
        out: list[Candidate] = []
        for coro in asyncio.as_completed(tasks):
            c = await coro
            out.append(c)
            if console:
                done = len(out)
                if done % 5 == 0 or done == len(candidates):
                    console.log(
                        f"[dim]availability {done}/{len(candidates)}[/dim]"
                    )
        return out


async def _trademark_phase(
    candidates: list[Candidate],
    *,
    console: Console | None = None,
) -> list[Candidate]:
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        results: list[AvailabilityResult | BaseException] = await asyncio.gather(
            *[check_trademark(c.name, client) for c in candidates],
            return_exceptions=True,
        )
    for c, r in zip(candidates, results):
        if isinstance(r, BaseException):
            continue
        c.availability["trademark"] = r
        if r.raw and "hits" in r.raw:
            c.trademark_hits = [TrademarkHit.model_validate(h) for h in r.raw["hits"]]
    if console:
        console.log("[dim]trademark phase done[/dim]")
    return candidates


async def _fastly_phase(
    candidates: list[Candidate],
    tlds: tuple[str, ...],
) -> list[Candidate]:
    if not os.environ.get("FASTLY_API_TOKEN"):
        return candidates
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        out = await asyncio.gather(
            *[fastly_premium_check(c, list(tlds), client) for c in candidates]
        )
    return list(out)


def _filter_phase(raw_names: list[str]) -> tuple[list[Candidate], list[Candidate]]:
    """Returns (passed, rejected) candidates from raw name list."""

    passed: list[Candidate] = []
    rejected: list[Candidate] = []
    seen: set[str] = set()
    for raw in raw_names:
        name = normalize(raw)
        if not name or name in seen:
            continue
        seen.add(name)
        reason = first_reject_reason(name)
        if reason:
            rejected.append(
                Candidate(name=name, rejected=True, reject_reason=reason)
            )
        else:
            passed.append(Candidate(name=name))
    return passed, rejected


async def run_pipeline(
    cfg: PipelineConfig,
    *,
    console: Console | None = None,
) -> tuple[list[Candidate], list[Candidate]]:
    """Top-level entry. Returns (top_candidates, rejected_interesting)."""

    log = console.log if console else (lambda *_a, **_k: None)
    profile = load_profile()

    # Stage 1 — generate
    log(f"[bold]Generating[/bold] {cfg.target_count} candidates …")
    gen_cfg = GenerationConfig(
        description=cfg.description,
        keywords=cfg.keywords,
        target_count=cfg.target_count,
        model=cfg.model,
    )
    raw_names = await generate_candidates(gen_cfg, profile)
    log(f"  → {len(raw_names)} unique raw candidates")

    # Stage 2 — filter
    passed, rejected = _filter_phase(raw_names)
    log(f"[bold]Filtered[/bold]: {len(passed)} kept · {len(rejected)} rejected")

    if not passed:
        log("[red]No candidates survived filtering — try adjusting the description.[/red]")
        return [], rejected[:10]

    # Score the survivors on cheap signals so we can pick the top-N to
    # spend network budget on.
    for c in passed:
        c.scores.pronounceability = pronounceability_score(c.name)
        c.scores.length_penalty = length_penalty(c.name)

    # Take the top N by pronounceability (proxy) for availability checks.
    pre_avail = sorted(
        passed,
        key=lambda c: (c.scores.pronounceability - c.scores.length_penalty),
        reverse=True,
    )[: cfg.top_n_after_filter]
    log(f"  → top {len(pre_avail)} by heuristic pre-score")

    # Stage 3 — availability (parallel)
    log("[bold]Availability[/bold] across PyPI / npm / GitHub / domains …")
    pre_avail = await _availability_phase(pre_avail, cfg.tlds, console=console)

    # Stage 4 — interpret (in-process, cheap)
    pre_avail = [reinterpret_candidate(c) for c in pre_avail]

    # Stage 4b — Fastly premium domain check on top 30 by avail score
    for c in pre_avail:
        c.scores.availability = _availability_score(c)
    pre_avail.sort(key=lambda c: c.scores.availability, reverse=True)

    if not cfg.skip_fastly:
        top_for_fastly = pre_avail[: cfg.top_n_report]
        log(
            f"[bold]Fastly premium check[/bold] on top {len(top_for_fastly)} "
            "(skips silently if FASTLY_API_TOKEN unset)"
        )
        refined = await _fastly_phase(top_for_fastly, cfg.tlds)
        # Splice refined back into pre_avail.
        refined_by_name = {c.name: c for c in refined}
        pre_avail = [refined_by_name.get(c.name, c) for c in pre_avail]
        # Recompute availability after fastly refinement.
        for c in pre_avail:
            c.scores.availability = _availability_score(c)

    # Stage 5 — trademark on top N
    if not cfg.skip_trademark:
        tm_targets = pre_avail[: cfg.top_n_trademark]
        log(f"[bold]Trademark[/bold] check on top {len(tm_targets)}")
        await _trademark_phase(tm_targets, console=console)
        # Re-score now that trademark may have updated availability map.
        for c in pre_avail:
            c.scores.availability = _availability_score(c)

    # Stage 6 — rank (pairwise) on the survivors
    rank_targets = pre_avail[: max(cfg.top_n_report * 2, 30)]
    log(f"[bold]Pairwise ranking[/bold] on {len(rank_targets)} candidates")
    rank_cfg = RankConfig(
        description=cfg.description,
        rounds=cfg.rank_rounds,
        top_n=cfg.top_n_report,
        model=cfg.rank_model,
    )
    ranked = await rank_candidates(rank_targets, rank_cfg)

    # Composite scoring
    for c in ranked:
        c.scores.composite = _composite(c)

    ranked.sort(key=lambda c: c.scores.composite, reverse=True)
    top = ranked[: cfg.top_n_report]

    # Bottom 10 of the ranked shortlist for "rejected but interesting".
    interesting_tail = ranked[-min(10, len(ranked)) :] if len(ranked) > 10 else []

    return top, interesting_tail
