"""Tests for core.book_sweep — order book sweep logic."""

import pytest

from core.book_sweep import SweepResult, sweep_asks, sweep_bids


# ---------------------------------------------------------------------------
# Helpers — lightweight stand-in for CLOB book levels
# ---------------------------------------------------------------------------

class _Level:
    """Mimics a py-clob-client OrderBookLevel with .price and .size."""

    def __init__(self, price: float, size: float):
        self.price = price
        self.size = size


def _asks(*levels):
    """Build ascending ask list from (price, size) tuples."""
    return [_Level(p, s) for p, s in levels]


def _bids(*levels):
    """Build descending bid list from (price, size) tuples."""
    return [_Level(p, s) for p, s in levels]


# ---------------------------------------------------------------------------
# sweep_asks tests
# ---------------------------------------------------------------------------

class TestSweepAsks:
    def test_empty_book_returns_zero(self):
        result = sweep_asks([], fair_value=0.60, edge_threshold=0.05)
        assert result.executable_shares == 0
        assert result.levels_used == 0

    def test_none_asks_returns_zero(self):
        result = sweep_asks(None, fair_value=0.60, edge_threshold=0.05)
        assert result.executable_shares == 0

    def test_zero_fair_value_returns_zero(self):
        asks = _asks((0.50, 100))
        result = sweep_asks(asks, fair_value=0.0, edge_threshold=0.05)
        assert result.executable_shares == 0

    def test_single_level_with_edge(self):
        # fair=0.60, ask=0.50 → edge = (0.60-0.50)/0.60 = 0.1667 > 0.05
        asks = _asks((0.50, 100))
        result = sweep_asks(asks, fair_value=0.60, edge_threshold=0.05)
        assert result.levels_used == 1
        assert result.executable_shares == 100.0
        assert result.vwap == 0.50
        assert result.worst_price == 0.50
        assert result.edge_at_vwap == pytest.approx((0.60 - 0.50) / 0.60, abs=1e-4)

    def test_single_level_no_edge(self):
        # fair=0.50, ask=0.50 → edge = 0 < 0.05
        asks = _asks((0.50, 100))
        result = sweep_asks(asks, fair_value=0.50, edge_threshold=0.05)
        assert result.executable_shares == 0

    def test_multiple_levels_all_with_edge(self):
        # fair=0.70, threshold=0.05
        # L1: ask=0.50, size=50 → vwap=0.50, edge=(0.70-0.50)/0.70=0.286
        # L2: ask=0.55, size=50 → vwap=0.525, edge=(0.70-0.525)/0.70=0.25
        # L3: ask=0.60, size=50 → vwap=0.55, edge=(0.70-0.55)/0.70=0.214
        asks = _asks((0.50, 50), (0.55, 50), (0.60, 50))
        result = sweep_asks(asks, fair_value=0.70, edge_threshold=0.05)
        assert result.levels_used == 3
        assert result.executable_shares == 150.0
        assert result.worst_price == 0.60

    def test_edge_breaks_at_level_3(self):
        # fair=0.55, threshold=0.05
        # L1: ask=0.50, size=50 → vwap=0.50, edge=(0.55-0.50)/0.55=0.0909 ✓
        # L2: ask=0.51, size=50 → vwap=0.505, edge=(0.55-0.505)/0.55=0.0818 ✓
        # L3: ask=0.54, size=50 → vwap=0.5167, edge=(0.55-0.5167)/0.55=0.0606 ✓
        # L4: ask=0.55, size=50 → vwap=0.525, edge=(0.55-0.525)/0.55=0.0455 ✗
        asks = _asks((0.50, 50), (0.51, 50), (0.54, 50), (0.55, 50))
        result = sweep_asks(asks, fair_value=0.55, edge_threshold=0.05)
        assert result.levels_used == 3
        assert result.executable_shares == 150.0
        assert result.worst_price == 0.54

    def test_max_levels_cap(self):
        asks = _asks((0.30, 10), (0.31, 10), (0.32, 10), (0.33, 10), (0.34, 10))
        result = sweep_asks(asks, fair_value=0.70, edge_threshold=0.05, max_levels=3)
        assert result.levels_used == 3
        assert result.executable_shares == 30.0

    def test_max_price_cap(self):
        # Level at 0.65 is above max_sweep_price=0.60 → stop
        asks = _asks((0.50, 50), (0.55, 50), (0.65, 50))
        result = sweep_asks(
            asks, fair_value=0.80, edge_threshold=0.05, max_price_cap=0.60
        )
        assert result.levels_used == 2
        assert result.worst_price == 0.55

    def test_rounding_to_4_decimals(self):
        # 3 shares each → total 9 shares, but sizes designed to create odd sum
        asks = _asks((0.40, 1.00001), (0.41, 1.00002))
        result = sweep_asks(asks, fair_value=0.60, edge_threshold=0.05)
        # executable_shares must have at most 4 decimal places
        shares_str = f"{result.executable_shares:.10f}"
        # Everything past 4th decimal should be zero
        assert result.executable_shares == round(result.executable_shares, 4)

    def test_sub_dollar_notional_returns_zero(self):
        # 0.5 shares at 0.50 = $0.25 notional → below $1 min
        asks = _asks((0.50, 0.5))
        result = sweep_asks(asks, fair_value=0.80, edge_threshold=0.05)
        assert result.executable_shares == 0
        assert result.levels_used == 0

    def test_vwap_calculation_accuracy(self):
        # L1: 0.40 * 100 = 40
        # L2: 0.50 * 100 = 50
        # VWAP = (40 + 50) / 200 = 0.45
        asks = _asks((0.40, 100), (0.50, 100))
        result = sweep_asks(asks, fair_value=0.70, edge_threshold=0.05)
        assert result.vwap == pytest.approx(0.45, abs=1e-6)


# ---------------------------------------------------------------------------
# sweep_bids tests
# ---------------------------------------------------------------------------

class TestSweepBids:
    def test_empty_book_returns_zero(self):
        result = sweep_bids([], fair_value=0.40, edge_threshold=0.05)
        assert result.executable_shares == 0

    def test_single_level_with_edge(self):
        # fair=0.40, bid=0.50 → edge = (0.50-0.40)/0.40 = 0.25 > 0.05
        bids = _bids((0.50, 100))
        result = sweep_bids(bids, fair_value=0.40, edge_threshold=0.05)
        assert result.levels_used == 1
        assert result.executable_shares == 100.0
        assert result.vwap == 0.50
        assert result.worst_price == 0.50
        assert result.edge_at_vwap == pytest.approx((0.50 - 0.40) / 0.40, abs=1e-4)

    def test_single_level_no_edge(self):
        # fair=0.50, bid=0.50 → edge = 0 < 0.05
        bids = _bids((0.50, 100))
        result = sweep_bids(bids, fair_value=0.50, edge_threshold=0.05)
        assert result.executable_shares == 0

    def test_multiple_levels_edge_breaks(self):
        # fair=0.40, threshold=0.10
        # L1: bid=0.50, size=50 → vwap=0.50, edge=0.25 ✓
        # L2: bid=0.48, size=50 → vwap=0.49, edge=0.225 ✓
        # L3: bid=0.42, size=50 → vwap=0.4667, edge=0.1667 ✓
        # L4: bid=0.40, size=50 → vwap=0.45, edge=0.125 ✓
        # But: L5: bid=0.38, size=200 → below min_price_floor=0.40 → stop
        bids = _bids((0.50, 50), (0.48, 50), (0.42, 50), (0.40, 50), (0.38, 200))
        result = sweep_bids(
            bids, fair_value=0.40, edge_threshold=0.10, min_price_floor=0.40
        )
        assert result.levels_used == 4
        assert result.worst_price == 0.40

    def test_min_price_floor(self):
        bids = _bids((0.50, 50), (0.30, 50))
        result = sweep_bids(
            bids, fair_value=0.30, edge_threshold=0.05, min_price_floor=0.40
        )
        # Only level at 0.50 qualifies (0.30 < floor 0.40)
        assert result.levels_used == 1
        assert result.worst_price == 0.50

    def test_max_levels_cap(self):
        bids = _bids((0.60, 10), (0.58, 10), (0.56, 10), (0.54, 10))
        result = sweep_bids(
            bids, fair_value=0.40, edge_threshold=0.05, max_levels=2
        )
        assert result.levels_used == 2

    def test_sub_dollar_notional_returns_zero(self):
        bids = _bids((0.50, 0.5))
        result = sweep_bids(bids, fair_value=0.30, edge_threshold=0.05)
        assert result.executable_shares == 0
