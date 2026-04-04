"""Unit tests for FairValueEngine."""
import math
import pytest

from markets.fair_value import FairValueEngine, FairValueResult
from scrapers.models import BookOdds


def _fv_dict(result: dict[str, FairValueResult]) -> dict[str, float]:
    """Extract just the fair_value floats for backward-compatible assertions."""
    return {k: v.fair_value for k, v in result.items()}


def test_basic_vig_removal_single_book():
    """3 outcomes from 1 sportsbook, implied probs sum to ~115%. Devigged should sum to 1.0."""
    engine = FairValueEngine({"bookA": 1.0})
    mapped = {
        "Team A": [BookOdds("bookA", 2.00)],   # implied 0.500
        "Team B": [BookOdds("bookA", 3.00)],   # implied 0.333
        "Team C": [BookOdds("bookA", 3.00)],   # implied 0.333 → sum = 1.167
    }
    result = engine.compute(mapped)
    fv = _fv_dict(result)
    assert abs(sum(fv.values()) - 1.0) < 1e-9
    # Team A has highest implied prob → should have highest fair value
    assert fv["Team A"] > fv["Team B"]
    assert abs(fv["Team B"] - fv["Team C"]) < 1e-9


def test_multi_book_aggregation():
    """Same 3 outcomes, odds from 3 equally-weighted sportsbooks."""
    engine = FairValueEngine({"book1": 1.0, "book2": 1.0, "book3": 1.0})
    mapped = {
        "Team A": [
            BookOdds("book1", 2.00),
            BookOdds("book2", 2.10),
            BookOdds("book3", 1.90),
        ],
        "Team B": [
            BookOdds("book1", 3.00),
            BookOdds("book2", 3.20),
            BookOdds("book3", 2.80),
        ],
        "Team C": [
            BookOdds("book1", 4.00),
            BookOdds("book2", 3.80),
            BookOdds("book3", 4.20),
        ],
    }
    result = engine.compute(mapped)
    fv = _fv_dict(result)
    assert abs(sum(fv.values()) - 1.0) < 1e-9
    # All books agree on ordering: A > B > C
    assert fv["Team A"] > fv["Team B"] > fv["Team C"]


def test_unequal_book_weights():
    """Sharp book (weight 2.0) vs soft books (weight 1.0). Fair values pulled toward sharp."""
    engine = FairValueEngine({"sharp": 2.0, "soft1": 1.0, "soft2": 1.0})
    mapped = {
        "Team A": [
            BookOdds("sharp", 1.80),
            BookOdds("soft1", 2.20),
            BookOdds("soft2", 2.20),
        ],
        "Team B": [
            BookOdds("sharp", 4.00),
            BookOdds("soft1", 3.50),
            BookOdds("soft2", 3.50),
        ],
    }
    result = engine.compute(mapped)
    fv = _fv_dict(result)
    assert abs(sum(fv.values()) - 1.0) < 1e-9

    equal_engine = FairValueEngine({"sharp": 1.0, "soft1": 1.0, "soft2": 1.0})
    equal_result = equal_engine.compute(mapped)
    equal_fv = _fv_dict(equal_result)

    assert fv["Team A"] > equal_fv["Team A"]


def test_partial_book_coverage():
    """4 outcomes, Book B covers only 3. Missing outcome uses only A and C."""
    engine = FairValueEngine({"bookA": 1.0, "bookB": 1.0, "bookC": 1.0})
    mapped = {
        "Team A": [
            BookOdds("bookA", 3.00),
            BookOdds("bookB", 3.10),
            BookOdds("bookC", 2.90),
        ],
        "Team B": [
            BookOdds("bookA", 4.00),
            BookOdds("bookB", 4.20),
            BookOdds("bookC", 3.80),
        ],
        "Team C": [
            BookOdds("bookA", 5.00),
            BookOdds("bookB", 5.50),
            BookOdds("bookC", 4.80),
        ],
        "Team D": [
            BookOdds("bookA", 6.00),
            BookOdds("bookC", 5.50),
        ],
    }
    result = engine.compute(mapped)
    fv = _fv_dict(result)
    assert len(fv) == 4
    assert abs(sum(fv.values()) - 1.0) < 1e-9
    for v in fv.values():
        assert 0 < v < 1


def test_normalization():
    """After aggregation, all fair values must sum to 1.0."""
    engine = FairValueEngine({"b1": 1.5, "b2": 1.0})
    mapped = {
        "A": [BookOdds("b1", 2.00), BookOdds("b2", 2.10)],
        "B": [BookOdds("b1", 3.00), BookOdds("b2", 2.90)],
        "C": [BookOdds("b1", 5.00), BookOdds("b2", 5.20)],
        "D": [BookOdds("b1", 8.00), BookOdds("b2", 7.50)],
        "E": [BookOdds("b1", 12.00), BookOdds("b2", 13.00)],
    }
    result = engine.compute(mapped)
    fv = _fv_dict(result)
    assert abs(sum(fv.values()) - 1.0) < 1e-9


def test_single_book():
    """Only 1 sportsbook provides odds. Vig removal alone should produce valid fair values."""
    engine = FairValueEngine({})
    mapped = {
        "A": [BookOdds("solo", 1.80)],   # 0.556
        "B": [BookOdds("solo", 3.00)],   # 0.333
        "C": [BookOdds("solo", 6.00)],   # 0.167 → sum = 1.056
    }
    result = engine.compute(mapped)
    fv = _fv_dict(result)
    assert abs(sum(fv.values()) - 1.0) < 1e-9
    assert fv["A"] > fv["B"] > fv["C"]
    assert len(fv) == 3


def test_best_book_tracking():
    """FairValueResult should track the book with the lowest raw implied prob."""
    engine = FairValueEngine({"book1": 1.0, "book2": 1.0})
    mapped = {
        "A": [
            BookOdds("book1", 3.00),   # implied 0.333
            BookOdds("book2", 4.00),   # implied 0.250 — best (lowest)
        ],
        "B": [
            BookOdds("book1", 2.00),   # implied 0.500
            BookOdds("book2", 1.80),   # implied 0.556
        ],
    }
    result = engine.compute(mapped)
    assert result["A"].best_book_name == "book2"
    assert abs(result["A"].best_book_implied_prob - 0.25) < 1e-9
    assert result["B"].best_book_name == "book1"
    assert abs(result["B"].best_book_implied_prob - 0.50) < 1e-9


def test_book_devigged_populated():
    """FairValueResult should contain per-book devigged probs."""
    engine = FairValueEngine({"b1": 1.0, "b2": 1.0})
    mapped = {
        "A": [BookOdds("b1", 2.00), BookOdds("b2", 2.10)],
        "B": [BookOdds("b1", 3.00), BookOdds("b2", 2.90)],
    }
    result = engine.compute(mapped)
    assert "b1" in result["A"].book_devigged
    assert "b2" in result["A"].book_devigged
    # Each book's devigged probs for A+B should sum to 1.0
    for book in ["b1", "b2"]:
        book_sum = result["A"].book_devigged[book] + result["B"].book_devigged[book]
        assert abs(book_sum - 1.0) < 1e-9


def test_invalid_book_odds_are_skipped():
    """A book with invalid odds should be ignored, not crash the engine."""
    engine = FairValueEngine({"good": 1.0, "bad": 1.0})
    mapped = {
        "A": [BookOdds("good", 2.00), BookOdds("bad", 0.0)],
        "B": [BookOdds("good", 2.00), BookOdds("bad", 3.00)],
    }
    result = engine.compute(mapped)
    fv = _fv_dict(result)
    assert set(fv.keys()) == {"A", "B"}
    assert abs(sum(fv.values()) - 1.0) < 1e-9
    # With only "good" book contributing at equal odds, split should be equal.
    assert abs(fv["A"] - 0.5) < 1e-9
    assert abs(fv["B"] - 0.5) < 1e-9


def test_all_invalid_odds_returns_empty():
    """If every book is invalid, engine should return empty results."""
    engine = FairValueEngine({"bad": 1.0})
    mapped = {
        "A": [BookOdds("bad", math.inf)],
        "B": [BookOdds("bad", -2.0)],
    }
    result = engine.compute(mapped)
    assert result == {}
