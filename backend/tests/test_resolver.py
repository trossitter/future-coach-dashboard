"""Critical path #1 — concept resolution.

Why this path: every coach request and member signal enters the system as free
text that must land on the *right* canonical graph concept. If resolution is
wrong, everything downstream (retrieval, safety) reasons about the wrong thing.
We test all three passes, the gym-jargon aliases, graceful degradation, and the
Cypher-injection guard.
"""
import pytest

from app import resolver
from app.resolver import UnknownLabel


def test_exact_match():
    r = resolver.resolve("chest", "Muscle")
    assert r["match"] == "chest"
    assert r["method"] == "exact"


def test_alias_jargon_is_deterministic():
    # SKOS-style altLabels — caught by exact/fuzzy, not the embedding gamble
    assert resolver.resolve("pecs", "Muscle")["match"] == "chest"
    assert resolver.resolve("delts", "Muscle")["match"] == "deltoids"
    assert resolver.resolve("hammies", "Muscle")["match"] == "hamstrings"
    assert resolver.resolve("neck", "Joint")["match"] == "cervical spine"


def test_fuzzy_typo():
    r = resolver.resolve("tricpes", "Muscle")  # typo, not an exact surface form
    assert r["match"] == "triceps"
    assert r["method"] in ("fuzzy", "embedding")


def test_semantic_free_text_resolves_to_concept():
    r = resolver.resolve("my knee has been bugging me on deep squats", "Joint")
    assert r["match"] == "knee"


def test_graceful_degradation_flags_low_confidence():
    r = resolver.resolve("zxqwv nonsense token", "Muscle")
    # never crashes; returns a structured guess and a clarification flag
    assert set(r) >= {"match", "method", "confidence", "needs_clarification"}


def test_injection_guard_rejects_bad_label():
    with pytest.raises(UnknownLabel):
        resolver.resolve("x", "Exercise) DETACH DELETE n //")
    with pytest.raises(UnknownLabel):
        resolver.resolve("x", "Exercise")  # exercises resolve via the vector index


def test_vector_exercise_search_is_relevant():
    hits = resolver.semantic_exercise_search("chest fly isolation", k=5)
    assert hits and any("chest" in h["name"].lower() or "fly" in h["name"].lower()
                        for h in hits)
