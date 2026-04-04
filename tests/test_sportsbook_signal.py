"""Unit tests for evaluate_sportsbook_signals()."""
import pytest

from core.models import SportsbookSignal
from core.sportsbook_signal import evaluate_sportsbook_signals
from markets.base import OutcomeFairValue


def test_outlier_detected():
    """One book deviates >8% from consensus → signal emitted."""
    fv = [OutcomeFairValue(
        outcome_name="Team A",
        token_id="tok1",
        fair_value=0.30,
        sources_agreeing=3,
        book_devigged={
            "book1": 0.29,   # -3.3% — within threshold
            "book2": 0.31,   # +3.3% — within threshold
            "book3": 0.24,   # -20% — outlier
        },
    )]
    signals = evaluate_sportsbook_signals(fv, "Test Event", edge_threshold=0.08, abs_edge_threshold=0.01, min_sources=3)
    assert len(signals) == 1
    assert signals[0].outlier_book == "book3"
    assert signals[0].direction == "UNDER"
    assert signals[0].edge > 0.08


def test_over_outlier():
    """Book prices outcome much higher than consensus → OVER signal."""
    fv = [OutcomeFairValue(
        outcome_name="Team A",
        token_id="tok1",
        fair_value=0.20,
        sources_agreeing=3,
        book_devigged={
            "book1": 0.20,
            "book2": 0.19,
            "book3": 0.28,   # +40% over consensus
        },
    )]
    signals = evaluate_sportsbook_signals(fv, "Test Event", edge_threshold=0.08, abs_edge_threshold=0.01, min_sources=3)
    over = [s for s in signals if s.direction == "OVER"]
    assert len(over) == 1
    assert over[0].outlier_book == "book3"


def test_no_outliers():
    """All books close to consensus → no signals."""
    fv = [OutcomeFairValue(
        outcome_name="Team A",
        token_id="tok1",
        fair_value=0.30,
        sources_agreeing=3,
        book_devigged={
            "book1": 0.30,
            "book2": 0.31,
            "book3": 0.29,
        },
    )]
    signals = evaluate_sportsbook_signals(fv, "Test Event", edge_threshold=0.08, abs_edge_threshold=0.01, min_sources=3)
    assert len(signals) == 0


def test_min_sources_filter():
    """Fewer books than min_sources → skip outcome."""
    fv = [OutcomeFairValue(
        outcome_name="Team A",
        token_id="tok1",
        fair_value=0.30,
        sources_agreeing=2,
        book_devigged={
            "book1": 0.30,
            "book2": 0.40,   # big deviation but only 2 books
        },
    )]
    signals = evaluate_sportsbook_signals(fv, "Test Event", edge_threshold=0.08, abs_edge_threshold=0.01, min_sources=3)
    assert len(signals) == 0


def test_no_book_devigged():
    """book_devigged is None → outcome skipped."""
    fv = [OutcomeFairValue(
        outcome_name="Team A",
        token_id="tok1",
        fair_value=0.30,
        sources_agreeing=3,
        book_devigged=None,
    )]
    signals = evaluate_sportsbook_signals(fv, "Test Event", edge_threshold=0.08, abs_edge_threshold=0.01, min_sources=3)
    assert len(signals) == 0


def test_multiple_outcomes():
    """Signals from multiple outcomes reported independently."""
    fv = [
        OutcomeFairValue(
            outcome_name="Team A", token_id="tok1", fair_value=0.50,
            sources_agreeing=3,
            book_devigged={"b1": 0.50, "b2": 0.50, "b3": 0.35},  # b3 is outlier
        ),
        OutcomeFairValue(
            outcome_name="Team B", token_id="tok2", fair_value=0.50,
            sources_agreeing=3,
            book_devigged={"b1": 0.50, "b2": 0.50, "b3": 0.65},  # b3 is outlier
        ),
    ]
    signals = evaluate_sportsbook_signals(fv, "Test Event", edge_threshold=0.08, abs_edge_threshold=0.01, min_sources=3)
    assert len(signals) == 2
    names = {s.outcome_name for s in signals}
    assert names == {"Team A", "Team B"}


def test_abs_threshold_blocks():
    """Relative deviation is 10% but absolute diff is only 0.005 (< 0.01) → no signal.

    fair_value=0.05, book_prob=0.055 → relative=10%, absolute=0.005
    """
    fv = [OutcomeFairValue(
        outcome_name="Team A",
        token_id="tok1",
        fair_value=0.05,
        sources_agreeing=3,
        book_devigged={
            "book1": 0.05,
            "book2": 0.05,
            "book3": 0.055,  # 10% relative but only 0.005 absolute
        },
    )]
    signals = evaluate_sportsbook_signals(
        fv, "Test Event", edge_threshold=0.08, abs_edge_threshold=0.01, min_sources=3
    )
    assert len(signals) == 0


def test_abs_threshold_passes():
    """Same relative deviation but larger absolute diff → signal emitted.

    fair_value=0.30, book_prob=0.34 → relative=13.3%, absolute=0.04
    """
    fv = [OutcomeFairValue(
        outcome_name="Team A",
        token_id="tok1",
        fair_value=0.30,
        sources_agreeing=3,
        book_devigged={
            "book1": 0.30,
            "book2": 0.30,
            "book3": 0.34,
        },
    )]
    signals = evaluate_sportsbook_signals(
        fv, "Test Event", edge_threshold=0.08, abs_edge_threshold=0.01, min_sources=3
    )
    assert len(signals) == 1
