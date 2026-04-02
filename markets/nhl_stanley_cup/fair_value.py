from scrapers.models import BookOdds


class FairValueEngine:
    def __init__(self, sportsbook_weights: dict[str, float]):
        self.weights = sportsbook_weights

    def compute(self, mapped_odds: dict[str, list[BookOdds]]) -> dict[str, float]:
        """
        Converts raw sportsbook odds into fair value probabilities.

        Args:
            mapped_odds: { canonical_outcome_name: [BookOdds, ...] }

        Returns:
            { canonical_outcome_name: fair_value_probability }
        """
        # 1. Group by sportsbook: { book: { outcome: decimal_odds } }
        by_book: dict[str, dict[str, float]] = {}
        for outcome, odds_list in mapped_odds.items():
            for bo in odds_list:
                by_book.setdefault(bo.sportsbook, {})[outcome] = bo.decimal_odds

        # 2. Devig each book: implied probs → divide by overround
        devigged: dict[str, dict[str, float]] = {}
        for book, outcomes in by_book.items():
            implied = {o: 1.0 / odds for o, odds in outcomes.items()}
            overround = sum(implied.values())
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

        return fair_values
