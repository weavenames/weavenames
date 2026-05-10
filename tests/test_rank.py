"""Tests for pairwise ranking — verify ELO produces a stable, sorted output.

We don't hit the network. Instead we monkeypatch `_judge_pair` with a
deterministic preference function. ELO is randomized in pairing order, so
we use a fixed seed.
"""

from __future__ import annotations

import asyncio

import weavenames.rank as rank
from weavenames.models import Candidate
from weavenames.rank import RankConfig, rank_candidates


def _make_candidates(names: list[str]) -> list[Candidate]:
    return [Candidate(name=n) for n in names]


def test_rank_one_candidate_is_no_op():
    cands = _make_candidates(["solo"])
    out = asyncio.run(
        rank_candidates(
            cands, RankConfig(description="x", rounds=1), api_key="ignored", rng_seed=1
        )
    )
    assert len(out) == 1
    # ELO doesn't run on n=1; we still set a neutral pairwise_fit.
    assert out[0].scores.pairwise_fit == 0.5


def test_rank_orders_by_deterministic_preference(monkeypatch):
    """If we always prefer the alphabetically-earlier name, the output
    should be sorted alphabetically with the earliest name on top."""

    cands = _make_candidates(["delta", "bravo", "charlie", "alpha"])

    async def fake_judge(client, cfg, a, b, sem):
        return "A" if a < b else "B"

    monkeypatch.setattr(rank, "_judge_pair", fake_judge)

    out = asyncio.run(
        rank_candidates(
            cands,
            RankConfig(description="x", rounds=20, top_n=1),
            api_key="ignored",
            rng_seed=42,
        )
    )

    # Top of the list should be 'alpha' — the always-preferred name.
    assert out[0].name == "alpha"
    # 'delta' should be at the bottom.
    assert out[-1].name == "delta"
    # Every candidate has a pairwise_fit in [0, 1].
    for c in out:
        assert 0.0 <= c.scores.pairwise_fit <= 1.0
    # Top candidate's pairwise_fit is the max (== 1 after normalization).
    assert out[0].scores.pairwise_fit == max(c.scores.pairwise_fit for c in out)


def test_rank_stability_breaks_early(monkeypatch):
    """If the order is stable across rounds, ranking should exit early.

    We can't directly observe round count, but we can confirm that the
    early-exit branch doesn't break correctness — running with many rounds
    should still produce the same top candidate."""

    cands = _make_candidates(["foo", "bar", "baz", "qux"])

    async def fake_judge(client, cfg, a, b, sem):
        # Always prefer 'bar'
        if a == "bar":
            return "A"
        if b == "bar":
            return "B"
        return "TIE"

    monkeypatch.setattr(rank, "_judge_pair", fake_judge)
    out = asyncio.run(
        rank_candidates(
            cands,
            RankConfig(description="x", rounds=20, top_n=4),
            api_key="ignored",
            rng_seed=7,
        )
    )
    assert out[0].name == "bar"


def test_rank_no_api_key_returns_neutral():
    cands = _make_candidates(["a", "b", "c"])
    out = asyncio.run(
        rank_candidates(
            cands,
            RankConfig(description="x", rounds=1),
            api_key="",  # explicitly empty; no env fallback either
            rng_seed=0,
        )
    )
    # All neutral — no ranking happened.
    for c in out:
        assert c.scores.pairwise_fit == 0.5
