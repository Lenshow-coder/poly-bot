import logging
import math
from dataclasses import dataclass

from scrapers.models import BookOdds

logger = logging.getLogger(__name__)


@dataclass
class FairValueResult:
    fair_value: float                    # devigged weighted average
    best_book_implied_prob: float        # lowest raw implied prob (1/decimal_odds) across books
    best_book_name: str                  # which book offered it
    book_devigged: dict[str, float]      # { sportsbook: devigged_prob }


class FairValueEngine:
    def __init__(self, sportsbook_weights: dict[str, float]):
        self.weights = sportsbook_weights

    def compute(self, mapped_odds: dict[str, list[BookOdds]]) -> dict[str, FairValueResult]:
        """
        Converts raw sportsbook odds into fair value probabilities.

        Args:
            mapped_odds: { canonical_outcome_name: [BookOdds, ...] }

        Returns:
            { canonical_outcome_name: FairValueResult }
        """
        # 1. Group by sportsbook: { book: { outcome: decimal_odds } }
        by_book: dict[str, dict[str, float]] = {}
        for outcome, odds_list in mapped_odds.items():
            for bo in odds_list:
                by_book.setdefault(bo.sportsbook, {})[outcome] = bo.decimal_odds

        # 2. Devig each book: implied probs → divide by overround
        devigged: dict[str, dict[str, float]] = {}
        for book, outcomes in by_book.items():
            # If any odds are invalid for a book, skip the whole book. Devigging a
            # partial outcome set can distort probabilities.
            invalid_odds = [
                (outcome, odds)
                for outcome, odds in outcomes.items()
                if not math.isfinite(odds) or odds <= 0
            ]
            if invalid_odds:
                logger.warning(
                    f"Skipping book '{book}' due to invalid odds: {invalid_odds}"
                )
                continue

            implied = {o: 1.0 / odds for o, odds in outcomes.items()}
            overround = sum(implied.values())
            if not math.isfinite(overround) or overround <= 0:
                logger.warning(
                    f"Skipping book '{book}' due to invalid overround: {overround}"
                )
                continue
            devigged[book] = {o: p / overround for o, p in implied.items()}

        # 3. Weighted average per outcome (contributing books only)
        fair_values: dict[str, float] = {}
        for outcome in mapped_odds:
            weighted_sum = 0.0
            weight_total = 0.0
            for book, probs in devigged.items():
                if outcome not in probs:
                    continue
                w = self.weights.get(book, 1.0)
                weighted_sum += probs[outcome] * w
                weight_total += w
            if weight_total > 0:
                fair_values[outcome] = weighted_sum / weight_total

        # 4. Normalize so all fair values sum to 1.0
        total = sum(fair_values.values())
        if total > 0:
            fair_values = {o: p / total for o, p in fair_values.items()}

        # 5. Build results with per-book data
        # Track best raw implied prob (lowest = best price for a buyer) per outcome
        results: dict[str, FairValueResult] = {}
        for outcome, fv in fair_values.items():
            best_prob = 0.0
            best_name = ""
            for bo in mapped_odds[outcome]:
                if not math.isfinite(bo.decimal_odds) or bo.decimal_odds <= 0:
                    continue
                implied = 1.0 / bo.decimal_odds
                if best_prob == 0.0 or implied < best_prob:
                    best_prob = implied
                    best_name = bo.sportsbook

            book_devigged_for_outcome = {
                book: probs[outcome]
                for book, probs in devigged.items()
                if outcome in probs
            }

            results[outcome] = FairValueResult(
                fair_value=fv,
                best_book_implied_prob=best_prob,
                best_book_name=best_name,
                book_devigged=book_devigged_for_outcome,
            )

        return results
