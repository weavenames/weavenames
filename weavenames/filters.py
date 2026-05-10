"""Heuristic filters — cheap rejection before any network calls.

Every filter is independent and pure. They take a candidate name and return
either None (pass) or a string reason (reject). Order them cheapest-first in
the pipeline.
"""

from __future__ import annotations

import re

from better_profanity import profanity

from weavenames.data import collisions, known_packages

VOWELS = set("aeiouy")
_PROFANITY_LOADED = False


def _ensure_profanity_loaded() -> None:
    global _PROFANITY_LOADED
    if not _PROFANITY_LOADED:
        profanity.load_censor_words()
        _PROFANITY_LOADED = True


def normalize(name: str) -> str:
    """Lowercase and strip whitespace / surrounding punctuation."""

    return name.strip().strip("\"'`.,;:!?").lower()


def reject_length(name: str, *, min_len: int = 4, max_len: int = 14) -> str | None:
    n = len(name)
    if n < min_len:
        return f"too short ({n} < {min_len})"
    if n > max_len:
        return f"too long ({n} > {max_len})"
    return None


def reject_profanity(name: str) -> str | None:
    _ensure_profanity_loaded()
    if profanity.contains_profanity(name):
        return "contains profanity"
    return None


def reject_unpronounceable(name: str) -> str | None:
    """Reject names with no vowels or 4+ consecutive consonants.

    Keeps the bar low — we want to filter genuine word-salad like 'qrtks',
    not penalize creative consonant clusters like 'klyx' or 'thrum'.
    """

    lowered = name.lower()
    if not any(c in VOWELS for c in lowered):
        return "no vowels"
    # 4+ consecutive consonants. We use re for clarity.
    if re.search(r"[^aeiouy0-9\-_]{4,}", lowered):
        return ">3 consecutive consonants"
    return None


def reject_collision(name: str) -> str | None:
    if name.lower() in collisions():
        return "exact collision with major project"
    return None


# --- similarity ---


def _ngrams(s: str, n: int = 3) -> set[str]:
    if len(s) < n:
        return {s}
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def _ngram_overlap(a: str, b: str, n: int = 3) -> float:
    """Jaccard similarity over character n-grams. 1.0 = identical."""

    ga, gb = _ngrams(a, n), _ngrams(b, n)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _levenshtein(a: str, b: str) -> int:
    """Standard DP Levenshtein. Names are short; allocation cost is fine."""

    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


def reject_similar_to_known(
    name: str,
    *,
    levenshtein_threshold: int = 1,
    ngram_threshold: float = 0.7,
) -> str | None:
    """Reject if name is too similar to a known infra package.

    Two-signal: Levenshtein distance <= 1 OR n-gram Jaccard >= 0.7.
    Both signals catch different failure modes — Levenshtein nails typos
    ('djangoo' vs 'django'), n-grams nail rearrangements / contractions.
    """

    lowered = name.lower()
    for pkg in known_packages():
        if pkg == lowered:
            return f"matches known package '{pkg}' exactly"
        if _levenshtein(lowered, pkg) <= levenshtein_threshold:
            return f"too similar to known package '{pkg}' (levenshtein)"
        if _ngram_overlap(lowered, pkg) >= ngram_threshold:
            return f"too similar to known package '{pkg}' (n-gram)"
    return None


# --- pipeline ---


def all_reject_reasons(name: str) -> list[str]:
    """Run every filter; return the list of reasons (empty == pass)."""

    reasons: list[str] = []
    for fn in (
        reject_length,
        reject_profanity,
        reject_unpronounceable,
        reject_collision,
        reject_similar_to_known,
    ):
        r = fn(name)
        if r is not None:
            reasons.append(r)
    return reasons


def first_reject_reason(name: str) -> str | None:
    """Cheap-first filtering. Returns first reason found or None."""

    for fn in (
        reject_length,
        reject_profanity,
        reject_unpronounceable,
        reject_collision,
        reject_similar_to_known,
    ):
        r = fn(name)
        if r is not None:
            return r
    return None


# --- pronounceability score for ranking (separate concern from reject) ---


def pronounceability_score(name: str) -> float:
    """Score in [0, 1]. Higher = more pronounceable.

    Heuristic only — penalize long consonant runs and reward alternation.
    """

    lowered = name.lower()
    if not lowered:
        return 0.0
    vowel_count = sum(1 for c in lowered if c in VOWELS)
    consonant_runs = re.findall(r"[^aeiouy0-9\-_]+", lowered)
    max_run = max((len(r) for r in consonant_runs), default=0)
    vowel_ratio = vowel_count / len(lowered)

    # Score: ideal vowel ratio ~0.4, penalize long consonant runs.
    ratio_score = 1.0 - abs(vowel_ratio - 0.4) * 1.5
    run_score = max(0.0, 1.0 - max(0, max_run - 2) * 0.25)
    return max(0.0, min(1.0, 0.6 * ratio_score + 0.4 * run_score))


def length_penalty(name: str, *, ideal: int = 7) -> float:
    """Penalty in [0, 1]. 0 = ideal length. Used as composite-score subtraction."""

    diff = abs(len(name) - ideal)
    return min(1.0, diff / 10.0)
