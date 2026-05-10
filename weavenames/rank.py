"""Pairwise LLM ranking for candidate names.

Why pairwise instead of 0-10 scoring: single-rubric scoring is noisy. Same
name twice on different days yields different scores. Pairwise (`is A or B
better for this description?`) is significantly more stable in the
literature — same model, same temperature, same input, ~3x more consistent.

Implementation choice: ELO-style tournament with random pairings.
- Initialize every candidate at 1500.
- Run `rounds` rounds. Each round, shuffle and pair candidates; ask the LLM
  which is better; update both ratings via standard Elo update (k=32).
- Stop when the top-N order is stable across two consecutive rounds, or
  rounds is exhausted.

We chose ELO over single-elimination tournament because:
- ELO produces a natural global ranking (single-elim only ranks the winner).
- ELO converges fast on a small candidate set (50-100 names).
- Pair count is configurable independent of candidate count.
- Re-runs from a partial state are trivial (just keep rating, run more
  rounds).

Scores are normalized to [0, 1] for the composite formula in pipeline.py.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from weavenames.models import Candidate

DEFAULT_RANK_MODEL = "claude-haiku-4-5-20251001"
INITIAL_RATING = 1500.0
K_FACTOR = 32.0


@dataclass
class RankConfig:
    description: str
    model: str = DEFAULT_RANK_MODEL
    rounds: int = 4
    top_n: int = 30
    max_concurrent: int = 8
    stability_threshold: int = 0  # 0 = identical top-N order

    # If a pair is judged "tie", we treat it as a draw (0.5 / 0.5).
    accept_tie: bool = True


PAIRWISE_SYSTEM = """You compare two candidate names for a software project and decide which is a better fit. You consider:
- Distinctiveness and memorability
- Pronounceability and ease of typing
- Fit with the project description
- Brandability (works as a domain, package, mark)
- Avoidance of generic SaaS-naming clichés

Respond with EXACTLY one token: "A", "B", or "TIE". No other text."""


def _user_prompt(description: str, a: str, b: str) -> str:
    return (
        f"Project: {description}\n\n"
        f"A: {a}\n"
        f"B: {b}\n\n"
        "Which is the better name? Answer A, B, or TIE."
    )


async def _judge_pair(
    client: AsyncAnthropic,
    cfg: RankConfig,
    a: str,
    b: str,
    sem: asyncio.Semaphore,
) -> str:
    """Returns 'A', 'B', or 'TIE'. Defaults to 'TIE' on parse failure."""

    async with sem:
        try:
            msg = await client.messages.create(
                model=cfg.model,
                max_tokens=8,
                system=PAIRWISE_SYSTEM,
                messages=[
                    {"role": "user", "content": _user_prompt(cfg.description, a, b)}
                ],
            )
        except Exception:
            return "TIE"
    text = "".join(
        block.text for block in msg.content if getattr(block, "type", None) == "text"
    ).strip().upper()
    # Take the first letter — model usually emits a single token.
    if text.startswith("A"):
        return "A"
    if text.startswith("B"):
        return "B"
    return "TIE"


def _expected(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def _update(rating: float, expected: float, score: float, k: float = K_FACTOR) -> float:
    return rating + k * (score - expected)


def _pair_round(names: list[str], rng: random.Random) -> list[tuple[str, str]]:
    shuffled = list(names)
    rng.shuffle(shuffled)
    pairs: list[tuple[str, str]] = []
    for i in range(0, len(shuffled) - 1, 2):
        pairs.append((shuffled[i], shuffled[i + 1]))
    return pairs


async def rank_candidates(
    candidates: list[Candidate],
    cfg: RankConfig,
    *,
    api_key: str | None = None,
    rng_seed: int | None = None,
) -> list[Candidate]:
    """Apply ELO-pairwise ranking. Returns candidates sorted descending by rating.

    Each candidate's `pairwise_wins`, `pairwise_matches` are populated. The
    composite scoring layer reads these via `scores.pairwise_fit`.
    """

    if not candidates:
        return candidates
    if len(candidates) == 1:
        c = candidates[0]
        c.scores.pairwise_fit = 0.5
        return [c]

    if api_key is None:
        import os

        api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # No API key — assign neutral pairwise score and bail.
        for c in candidates:
            c.scores.pairwise_fit = 0.5
        return candidates

    rng = random.Random(rng_seed)
    name_to_rating: dict[str, float] = {c.name: INITIAL_RATING for c in candidates}
    name_to_wins: dict[str, int] = {c.name: 0 for c in candidates}
    name_to_matches: dict[str, int] = {c.name: 0 for c in candidates}
    sem = asyncio.Semaphore(cfg.max_concurrent)

    client = AsyncAnthropic(api_key=api_key)
    last_top: list[str] = []
    consecutive_stable = 0
    # Require ~floor(n_candidates / 2) average matches per name before
    # trusting "stability" — otherwise random-pair ties trigger an early
    # exit before the ranking has converged.
    min_avg_matches = max(2, len(name_to_rating) // 2)
    async with client:
        for round_idx in range(cfg.rounds):
            pairs = _pair_round(list(name_to_rating.keys()), rng)
            results = await asyncio.gather(
                *[_judge_pair(client, cfg, a, b, sem) for a, b in pairs]
            )
            for (a, b), verdict in zip(pairs, results):
                ra, rb = name_to_rating[a], name_to_rating[b]
                ea = _expected(ra, rb)
                eb = 1.0 - ea
                if verdict == "A":
                    sa, sb = 1.0, 0.0
                    name_to_wins[a] += 1
                elif verdict == "B":
                    sa, sb = 0.0, 1.0
                    name_to_wins[b] += 1
                else:
                    sa, sb = 0.5, 0.5
                name_to_rating[a] = _update(ra, ea, sa)
                name_to_rating[b] = _update(rb, eb, sb)
                name_to_matches[a] += 1
                name_to_matches[b] += 1

            # Stability check — only exit early if the top-N order has been
            # stable for two consecutive rounds AND every name has had at
            # least `min_avg_matches` matches. This prevents random-pairing
            # ties from triggering an exit before the ranking converges.
            sorted_names = sorted(
                name_to_rating, key=lambda n: name_to_rating[n], reverse=True
            )
            top = sorted_names[: cfg.top_n]
            avg_matches = sum(name_to_matches.values()) / len(name_to_matches)
            if last_top == top and avg_matches >= min_avg_matches:
                consecutive_stable += 1
                if consecutive_stable >= 2:
                    break
            else:
                consecutive_stable = 0
            last_top = top

    # Normalize ratings to [0, 1] for scoring layer.
    ratings = list(name_to_rating.values())
    rmin, rmax = min(ratings), max(ratings)
    span = max(1e-9, rmax - rmin)

    by_name = {c.name: c for c in candidates}
    for name, rating in name_to_rating.items():
        c = by_name[name]
        c.pairwise_wins = name_to_wins[name]
        c.pairwise_matches = name_to_matches[name]
        c.scores.pairwise_fit = (rating - rmin) / span
    return sorted(by_name.values(), key=lambda c: c.scores.pairwise_fit, reverse=True)
