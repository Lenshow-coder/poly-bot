"""Unit tests for kelly_bet_size() and evaluate_signals()."""
import pytest

from core.models import PriceInfo, Signal
from core.signal import kelly_bet_size, evaluate_signals
from markets.base import OutcomeFairValue, TradeParams


# --- kelly_bet_size tests ---

def test_kelly_positive_edge():
    """fair_prob=0.20, market_price=0.18, bankroll=1000, kelly_fraction=0.25."""
    bet = kelly_bet_size(
        fair_prob=0.20, market_price=0.18, bankroll=1000,
        kelly_fraction=0.25, min_bet=5.0, max_bet=50.0,
    )
    # b = (1/0.18) - 1 ≈ 4.556
    # kelly_pct = (0.20 * 4.556 - 0.80) / 4.556 ≈ (0.911 - 0.80) / 4.556 ≈ 0.0244
    # bet = 1000 * 0.0244 * 0.25 ≈ 6.10
    assert 5.0 < bet < 8.0


def test_kelly_zero_edge():
    """fair_prob equals market_price → no edge → returns 0."""
    bet = kelly_bet_size(fair_prob=0.20, market_price=0.20, bankroll=1000)
    assert bet == 0.0


def test_kelly_negative_edge():
    """fair_prob < market_price → negative edge → returns 0."""
    bet = kelly_bet_size(fair_prob=0.18, market_price=0.20, bankroll=1000)
    assert bet == 0.0


def test_kelly_below_min():
    """Edge exists but Kelly suggests below min_bet → returns 0."""
    bet = kelly_bet_size(
        fair_prob=0.20, market_price=0.18, bankroll=100,
        kelly_fraction=0.25, min_bet=5.0, max_bet=50.0,
    )
    # With bankroll=100, bet ≈ $0.61 — below min
    assert bet == 0.0


def test_kelly_above_max():
    """Edge exists, Kelly suggests above max_bet → capped at max."""
    bet = kelly_bet_size(
        fair_prob=0.50, market_price=0.30, bankroll=10000,
        kelly_fraction=0.25, min_bet=5.0, max_bet=50.0,
    )
    assert bet == 50.0


def test_kelly_boundary_prices():
    """market_price at 0 or 1 → returns 0."""
    assert kelly_bet_size(fair_prob=0.5, market_price=0.0, bankroll=1000) == 0.0
    assert kelly_bet_size(fair_prob=0.5, market_price=1.0, bankroll=1000) == 0.0


# --- evaluate_signals tests ---

def _make_trade_params(**overrides):
    defaults = dict(
        edge_threshold=0.10,
        max_outcome_exposure=200,
        kelly_fraction=0.25,
        min_bet_size=5.0,
        max_bet_size=50.0,
        order_type="FOK",
        min_sources=3,
        cooldown_minutes=30,
        price_range=(0.03, 0.95),
    )
    defaults.update(overrides)
    return TradeParams(**defaults)


def test_signal_buy_generated():
    """fair_value=0.25, best_ask=0.18, threshold=0.10 → edge ~28% → signal emitted."""
    fv = [OutcomeFairValue("Team A", "tok1", 0.25, 4)]
    prices = {"tok1": PriceInfo(best_bid=0.17, best_ask=0.18, midpoint=0.175)}
    tp = _make_trade_params()
    signals = evaluate_signals(fv, prices, tp, 1000, "Test Event")
    assert len(signals) == 1
    assert signals[0].side == "BUY"
    assert signals[0].edge > 0.10


def test_signal_no_signal_below_threshold():
    """fair_value=0.20, best_ask=0.19, threshold=0.10 → edge=5% → no signal."""
    fv = [OutcomeFairValue("Team A", "tok1", 0.20, 4)]
    prices = {"tok1": PriceInfo(best_bid=0.18, best_ask=0.19, midpoint=0.185)}
    tp = _make_trade_params()
    signals = evaluate_signals(fv, prices, tp, 1000, "Test Event")
    assert len(signals) == 0


def test_signal_source_filter():
    """Edge exists but only 2 sources (min_sources=3) → no signal."""
    fv = [OutcomeFairValue("Team A", "tok1", 0.25, 2)]
    prices = {"tok1": PriceInfo(best_bid=0.17, best_ask=0.18, midpoint=0.175)}
    tp = _make_trade_params(min_sources=3)
    signals = evaluate_signals(fv, prices, tp, 1000, "Test Event")
    assert len(signals) == 0


def test_signal_price_range_below():
    """best_ask=0.02 (below range [0.03, 0.95]) → no signal."""
    fv = [OutcomeFairValue("Team A", "tok1", 0.10, 4)]
    prices = {"tok1": PriceInfo(best_bid=0.01, best_ask=0.02, midpoint=0.015)}
    tp = _make_trade_params()
    signals = evaluate_signals(fv, prices, tp, 1000, "Test Event")
    assert len(signals) == 0


def test_signal_price_range_above():
    """best_ask=0.96 (above range [0.03, 0.95]) → no signal."""
    fv = [OutcomeFairValue("Team A", "tok1", 0.99, 4)]
    prices = {"tok1": PriceInfo(best_bid=0.95, best_ask=0.96, midpoint=0.955)}
    tp = _make_trade_params()
    signals = evaluate_signals(fv, prices, tp, 1000, "Test Event")
    assert len(signals) == 0


def test_signal_missing_price_data():
    """best_ask or best_bid is None → outcome skipped."""
    fv = [OutcomeFairValue("Team A", "tok1", 0.25, 4)]
    prices = {"tok1": PriceInfo(best_bid=None, best_ask=0.18, midpoint=None)}
    tp = _make_trade_params()
    signals = evaluate_signals(fv, prices, tp, 1000, "Test Event")
    assert len(signals) == 0

    prices2 = {"tok1": PriceInfo(best_bid=0.17, best_ask=None, midpoint=None)}
    signals2 = evaluate_signals(fv, prices2, tp, 1000, "Test Event")
    assert len(signals2) == 0


def test_signal_missing_token():
    """Token not in prices dict → skipped."""
    fv = [OutcomeFairValue("Team A", "tok1", 0.25, 4)]
    prices = {}
    tp = _make_trade_params()
    signals = evaluate_signals(fv, prices, tp, 1000, "Test Event")
    assert len(signals) == 0


def test_signal_sell():
    """best_bid significantly above fair_value → SELL signal."""
    fv = [OutcomeFairValue("Team A", "tok1", 0.15, 4)]
    prices = {"tok1": PriceInfo(best_bid=0.22, best_ask=0.23, midpoint=0.225)}
    tp = _make_trade_params()
    signals = evaluate_signals(fv, prices, tp, 1000, "Test Event")
    sell_signals = [s for s in signals if s.side == "SELL"]
    assert len(sell_signals) == 1
    assert sell_signals[0].edge > 0.10
