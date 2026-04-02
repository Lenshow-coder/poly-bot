"""Unit tests for FairValueEngine."""
import pytest

from markets.nhl_stanley_cup.fair_value import FairValueEngine
from scrapers.models import BookOdds


def test_basic_vig_removal_single_book():
    """3 outcomes from 1 sportsbook, implied probs sum to ~115%. Devigged should sum to 1.0."""
    engine = FairValueEngine({"bookA": 1.0})
    mapped = {
        "Team A": [BookOdds("bookA", 2.50)],   # implied 0.400
        "Team B": [BookOdds("bookA", 4.00)],   # implied 0.250
        "Team C": [BookOdds("bookA", 3.33)],   # implied ~0.300 → sum ~0.950... let's use vig
    }
    # Use odds that produce clear vig: 1/2.50 + 1/4.00 + 1/2.85 ≈ 0.40 + 0.25 + 0.351 = 1.001
    # Better: force a clean vig
    mapped = {
        "Team A": [BookOdds("bookA", 2.00)],   # implied 0.500
        "Team B": [BookOdds("bookA", 3.00)],   # implied 0.333
        "Team C": [BookOdds("bookA", 3.00)],   # implied 0.333 → sum = 1.167
    }
    result = engine.compute(mapped)
    assert abs(sum(result.values()) - 1.0) < 1e-9
    # Team A has highest implied prob → should have highest fair value
    assert result["Team A"] > result["Team B"]
    assert abs(result["Team B"] - result["Team C"]) < 1e-9


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
    assert abs(sum(result.values()) - 1.0) < 1e-9
    # All books agree on ordering: A > B > C
    assert result["Team A"] > result["Team B"] > result["Team C"]


def test_unequal_book_weights():
    """Sharp book (weight 2.0) vs soft books (weight 1.0). Fair values pulled toward sharp."""
    # Sharp book gives Team A lower odds (higher prob) than soft books
    engine = FairValueEngine({"sharp": 2.0, "soft1": 1.0, "soft2": 1.0})
    mapped = {
        "Team A": [
            BookOdds("sharp", 1.80),  # sharp thinks A is more likely
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
    assert abs(sum(result.values()) - 1.0) < 1e-9

    # Compare to equal-weight result
    equal_engine = FairValueEngine({"sharp": 1.0, "soft1": 1.0, "soft2": 1.0})
    equal_result = equal_engine.compute(mapped)

    # With sharp weighted 2x, Team A's fair value should be higher than equal weighting
    assert result["Team A"] > equal_result["Team A"]


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
            # bookB missing
            BookOdds("bookC", 5.50),
        ],
    }
    result = engine.compute(mapped)
    # All 4 outcomes should be present
    assert len(result) == 4
    # Should still sum to 1.0 after normalization
    assert abs(sum(result.values()) - 1.0) < 1e-9
    # All values should be positive and < 1
    for v in result.values():
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
    assert abs(sum(result.values()) - 1.0) < 1e-9


def test_single_book():
    """Only 1 sportsbook provides odds. Vig removal alone should produce valid fair values."""
    engine = FairValueEngine({})
    mapped = {
        "A": [BookOdds("solo", 2.50)],   # implied 0.400
        "B": [BookOdds("solo", 3.50)],   # implied ~0.286
        "C": [BookOdds("solo", 5.00)],   # implied 0.200 → sum = 0.886... need vig
    }
    # Use odds with vig (sum > 1.0)
    mapped = {
        "A": [BookOdds("solo", 1.80)],   # 0.556
        "B": [BookOdds("solo", 3.00)],   # 0.333
        "C": [BookOdds("solo", 6.00)],   # 0.167 → sum = 1.056
    }
    result = engine.compute(mapped)
    assert abs(sum(result.values()) - 1.0) < 1e-9
    assert result["A"] > result["B"] > result["C"]
    # Default weight 1.0 should be used for "solo"
    assert len(result) == 3
