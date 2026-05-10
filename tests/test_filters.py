"""Independent tests for each heuristic filter."""

from __future__ import annotations

from weavenames.filters import (
    _levenshtein,
    _ngram_overlap,
    first_reject_reason,
    length_penalty,
    normalize,
    pronounceability_score,
    reject_collision,
    reject_length,
    reject_profanity,
    reject_similar_to_known,
    reject_unpronounceable,
)


def test_normalize_strips_quotes_and_lowercases():
    assert normalize('"Helix".') == "helix"
    assert normalize("  Aether  ") == "aether"


def test_reject_length_window():
    assert reject_length("abc") is not None
    assert reject_length("a" * 15) is not None
    assert reject_length("helix") is None
    assert reject_length("a" * 14) is None


def test_reject_profanity_passes_clean():
    # Use a generic clean name; we don't want to hardcode profanity lists.
    assert reject_profanity("aether") is None


def test_reject_unpronounceable_no_vowels():
    assert reject_unpronounceable("xrtks") is not None


def test_reject_unpronounceable_long_consonant_run():
    # 'kngst' = 5 consonants in a row, with one vowel after — must reject.
    assert reject_unpronounceable("kngsta") is not None


def test_reject_unpronounceable_pass_normal():
    assert reject_unpronounceable("aether") is None
    assert reject_unpronounceable("clocktower") is None


def test_reject_collision_exact():
    assert reject_collision("react") is not None
    assert reject_collision("React") is not None  # case-insensitive
    assert reject_collision("aether") is None


def test_levenshtein_basic():
    assert _levenshtein("django", "django") == 0
    assert _levenshtein("django", "djangoo") == 1
    assert _levenshtein("django", "djongo") == 1
    assert _levenshtein("a", "b") == 1
    assert _levenshtein("", "abc") == 3


def test_ngram_overlap_basic():
    assert _ngram_overlap("django", "django") == 1.0
    # Completely disjoint
    assert _ngram_overlap("xyzpdq", "abcdef") < 0.1


def test_reject_similar_to_known_levenshtein():
    # 'djangoo' is one edit from 'django' — must reject
    assert reject_similar_to_known("djangoo") is not None


def test_reject_similar_to_known_pass():
    assert reject_similar_to_known("aether") is None


def test_first_reject_reason_orders_cheapest_first():
    # 'rea' is below min length — the length filter fires before collision.
    r = first_reject_reason("rea")
    assert r is not None
    assert "short" in r


def test_pronounceability_score_range():
    s = pronounceability_score("aether")
    assert 0.0 <= s <= 1.0
    bad = pronounceability_score("xrtks")
    assert s > bad


def test_length_penalty_zero_at_ideal():
    assert length_penalty("seven77") == 0.0  # length 7
    assert length_penalty("a") > 0.0
