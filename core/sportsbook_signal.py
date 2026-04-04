"""Detect outlier sportsbook prices vs consensus fair value."""
import logging

from core.models import SportsbookSignal
from markets.base import OutcomeFairValue

logger = logging.getLogger(__name__)


def evaluate_sportsbook_signals(
    fair_values: list[OutcomeFairValue],
    event_name: str,
    edge_threshold: float,
    abs_edge_threshold: float,
    min_sources: int,
) -> list[SportsbookSignal]:
    """Compare each book's devigged prob to consensus fair value.

    Emits a SportsbookSignal when a single book deviates from the consensus
    by more than both edge_threshold (relative) and abs_edge_threshold (absolute).

    Args:
        fair_values: OutcomeFairValues with book_devigged populated.
        event_name: For labeling signals.
        edge_threshold: Minimum relative deviation to flag.
        abs_edge_threshold: Minimum absolute probability difference to flag.
        min_sources: Skip outcomes with fewer contributing books.
    """
    signals = []
    for fv in fair_values:
        if not fv.book_devigged or len(fv.book_devigged) < min_sources:
            continue

        for book, book_prob in fv.book_devigged.items():
            if fv.fair_value <= 0:
                continue
            abs_diff = abs(book_prob - fv.fair_value)
            deviation = (book_prob - fv.fair_value) / fv.fair_value

            if abs(deviation) > edge_threshold and abs_diff > abs_edge_threshold:
                direction = "OVER" if deviation > 0 else "UNDER"
                signals.append(SportsbookSignal(
                    outcome_name=fv.outcome_name,
                    event_name=event_name,
                    outlier_book=book,
                    outlier_devigged_prob=book_prob,
                    consensus_prob=fv.fair_value,
                    edge=abs(deviation),
                    direction=direction,
                ))

    return signals
