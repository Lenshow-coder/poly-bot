from core.models import BankrollSnapshot, Signal
from core.position_tracker import PositionTracker
from core.risk_manager import RiskManager
from core.state import StateManager
from markets.base import TradeParams


def _trade_params(**overrides) -> TradeParams:
    base = TradeParams(
        edge_threshold=0.1,
        max_outcome_exposure=100.0,
        kelly_fraction=0.25,
        min_bet_size=5.0,
        max_bet_size=100.0,
        order_type="FOK",
        min_sources=2,
        cooldown_minutes=30,
        price_range=(0.01, 0.99),
        sportsbook_buffer=0.0,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def _buy_signal(**overrides) -> Signal:
    s = Signal(
        token_id="tok1",
        outcome_name="Team A",
        event_name="Event A",
        side="BUY",
        edge=0.2,
        fair_value=0.4,
        market_price=0.3,
        size_usd=50.0,
        max_price=0.3,
    )
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _sell_signal(**overrides) -> Signal:
    s = Signal(
        token_id="tok1",
        outcome_name="Team A",
        event_name="Event A",
        side="SELL",
        edge=0.2,
        fair_value=0.4,
        market_price=0.35,
        size_usd=0.0,
        min_price=0.3,
    )
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _tracker(tmp_path) -> PositionTracker:
    return PositionTracker(StateManager(state_dir=str(tmp_path)))


def test_approve_buy_passes_with_room(tmp_path):
    tracker = _tracker(tmp_path)
    risk = RiskManager(
        {
            "max_event_exposure": 200,
            "max_portfolio_exposure": 300,
            "min_balance": 10,
            "min_bankroll": 50,
            "size_clip_enabled": True,
        }
    )
    tracker.bankroll = BankrollSnapshot(100, 50, 150, "2026-01-01T00:00:00+00:00")

    decision = risk.approve(_buy_signal(), tracker, _trade_params(), exchange_balance=100)
    assert decision.approved is True
    assert decision.adjusted_size_usd == 50.0


def test_approve_buy_clips_to_exposure_when_enabled(tmp_path):
    tracker = _tracker(tmp_path)
    tracker.apply_fill("tok1", "Team A", "Event A", "BUY", shares=100, price=0.9)  # $90 exposure
    risk = RiskManager(
        {
            "max_event_exposure": 500,
            "max_portfolio_exposure": 500,
            "min_balance": 0,
            "min_bankroll": 0,
            "size_clip_enabled": True,
        }
    )
    decision = risk.approve(_buy_signal(size_usd=50), tracker, _trade_params(max_outcome_exposure=100))
    assert decision.approved is True
    assert 9.9 < decision.adjusted_size_usd < 10.1
    assert decision.reason == "approved_clipped"


def test_approve_buy_rejects_when_clip_disabled(tmp_path):
    tracker = _tracker(tmp_path)
    tracker.apply_fill("tok1", "Team A", "Event A", "BUY", shares=100, price=0.9)
    risk = RiskManager(
        {
            "max_event_exposure": 500,
            "max_portfolio_exposure": 500,
            "min_balance": 0,
            "min_bankroll": 0,
            "size_clip_enabled": False,
        }
    )
    decision = risk.approve(_buy_signal(size_usd=50), tracker, _trade_params(max_outcome_exposure=100))
    assert decision.approved is False
    assert decision.reason == "size_clipping_disabled"


def test_approve_rejects_on_cooldown(tmp_path):
    tracker = _tracker(tmp_path)
    tracker.mark_traded("tok1", cooldown_minutes=30)
    risk = RiskManager(
        {
            "max_event_exposure": 500,
            "max_portfolio_exposure": 500,
            "min_balance": 0,
            "min_bankroll": 0,
            "size_clip_enabled": True,
        }
    )
    decision = risk.approve(_buy_signal(), tracker, _trade_params())
    assert decision.approved is False
    assert decision.reason == "cooldown_active"


def test_min_balance_and_bankroll_checks(tmp_path):
    tracker = _tracker(tmp_path)
    tracker.bankroll = BankrollSnapshot(10, 20, 30, "2026-01-01T00:00:00+00:00")
    risk = RiskManager(
        {
            "max_event_exposure": 500,
            "max_portfolio_exposure": 500,
            "min_balance": 50,
            "min_bankroll": 100,
            "size_clip_enabled": True,
        }
    )
    decision_balance = risk.approve(_buy_signal(), tracker, _trade_params(), exchange_balance=10)
    assert decision_balance.approved is False
    assert decision_balance.reason.startswith("min_balance_blocked")

    decision_bankroll = risk.approve(_buy_signal(), tracker, _trade_params(), exchange_balance=100)
    assert decision_bankroll.approved is False
    assert decision_bankroll.reason.startswith("min_bankroll_blocked")


def test_sell_rejected_without_holdings(tmp_path):
    tracker = _tracker(tmp_path)
    risk = RiskManager(
        {
            "max_event_exposure": 500,
            "max_portfolio_exposure": 500,
            "min_balance": 0,
            "min_bankroll": 0,
            "size_clip_enabled": True,
        }
    )
    decision = risk.approve(_sell_signal(), tracker, _trade_params())
    assert decision.approved is False
    assert decision.reason == "no_position_to_sell"


def test_sell_uses_current_holdings_notional(tmp_path):
    tracker = _tracker(tmp_path)
    tracker.apply_fill("tok1", "Team A", "Event A", "BUY", shares=20, price=0.2)
    risk = RiskManager(
        {
            "max_event_exposure": 500,
            "max_portfolio_exposure": 500,
            "min_balance": 0,
            "min_bankroll": 0,
            "size_clip_enabled": True,
        }
    )
    decision = risk.approve(_sell_signal(market_price=0.4), tracker, _trade_params())
    assert decision.approved is True
    assert decision.adjusted_size_usd == 8.0


def test_buy_clipped_by_spendable_balance(tmp_path):
    tracker = _tracker(tmp_path)
    risk = RiskManager(
        {
            "max_event_exposure": 500,
            "max_portfolio_exposure": 500,
            "min_balance": 20,
            "min_bankroll": 0,
            "size_clip_enabled": True,
        }
    )
    decision = risk.approve(
        _buy_signal(size_usd=50),
        tracker,
        _trade_params(max_outcome_exposure=500),
        exchange_balance=40,
    )
    assert decision.approved is True
    assert decision.adjusted_size_usd == 20


def test_buy_rejected_when_no_spendable_balance(tmp_path):
    tracker = _tracker(tmp_path)
    risk = RiskManager(
        {
            "max_event_exposure": 500,
            "max_portfolio_exposure": 500,
            "min_balance": 20,
            "min_bankroll": 0,
            "size_clip_enabled": True,
        }
    )
    decision = risk.approve(
        _buy_signal(size_usd=50),
        tracker,
        _trade_params(max_outcome_exposure=500),
        exchange_balance=20,
    )
    assert decision.approved is False
    assert decision.reason == "insufficient_spendable_balance"
