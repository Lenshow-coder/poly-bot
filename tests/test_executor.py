import pytest

from core.executor import Executor
from core.models import OrderResult, Signal
from core.position_tracker import PositionTracker
from core.state import StateManager
from markets.base import TradeParams


class DummyClient:
    def __init__(self, order_result: OrderResult):
        self.order_result = order_result
        self.calls = []

    def place_order(self, token_id, side, size, price, order_type):
        self.calls.append(
            {
                "token_id": token_id,
                "side": side,
                "size": size,
                "price": price,
                "order_type": order_type,
            }
        )
        return self.order_result


def _trade_params(order_type="FOK") -> TradeParams:
    return TradeParams(
        edge_threshold=0.1,
        max_outcome_exposure=100,
        kelly_fraction=0.25,
        min_bet_size=5,
        max_bet_size=100,
        order_type=order_type,
        min_sources=2,
        cooldown_minutes=30,
        price_range=(0.01, 0.99),
        sportsbook_buffer=0.0,
    )


def _signal(side: str, **overrides) -> Signal:
    s = Signal(
        token_id="tok1",
        outcome_name="Team A",
        event_name="Event A",
        side=side,
        edge=0.2,
        fair_value=0.4,
        market_price=0.25,
        size_usd=20.0,
        max_price=0.25 if side == "BUY" else None,
        min_price=0.20 if side == "SELL" else None,
    )
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _tracker(tmp_path):
    return PositionTracker(StateManager(state_dir=str(tmp_path / "state")))


@pytest.mark.asyncio
async def test_buy_converts_usd_to_shares_and_calls_client(tmp_path):
    client = DummyClient(
        OrderResult(
            order_id="o1",
            status="filled",
            filled_size=4.0,
            filled_price=0.25,
            timestamp="2026-01-01T00:00:00+00:00",
        )
    )
    executor = Executor(client=client, trade_log_path=str(tmp_path / "trades.csv"))
    tracker = _tracker(tmp_path)

    result = await executor.execute(
        signal=_signal("BUY"),
        trade_params=_trade_params("FOK"),
        tracker=tracker,
        adjusted_size_usd=1.0,
    )
    assert result.filled_shares == 4.0
    assert len(client.calls) == 1
    assert client.calls[0]["size"] == 4.0
    assert client.calls[0]["order_type"] == "FOK"
    assert tracker.get_position("tok1").size == 4.0


@pytest.mark.asyncio
async def test_sell_uses_held_size(tmp_path):
    client = DummyClient(
        OrderResult(
            order_id="o2",
            status="filled",
            filled_size=5.0,
            filled_price=0.3,
            timestamp="2026-01-01T00:00:00+00:00",
        )
    )
    executor = Executor(client=client, trade_log_path=str(tmp_path / "trades.csv"))
    tracker = _tracker(tmp_path)
    tracker.apply_fill("tok1", "Team A", "Event A", "BUY", 5.0, 0.2)

    result = await executor.execute(
        signal=_signal("SELL", market_price=0.3),
        trade_params=_trade_params("FAK"),
        tracker=tracker,
        adjusted_size_usd=0.0,
    )
    assert result.status == "FILLED"
    assert len(client.calls) == 1
    assert client.calls[0]["size"] == 5.0
    assert client.calls[0]["order_type"] == "FAK"
    assert tracker.get_position("tok1") is None


@pytest.mark.asyncio
async def test_invalid_notional_blocked_before_order(tmp_path):
    client = DummyClient(
        OrderResult(
            order_id="o3",
            status="filled",
            filled_size=1.0,
            filled_price=0.5,
            timestamp="2026-01-01T00:00:00+00:00",
        )
    )
    executor = Executor(client=client, trade_log_path=str(tmp_path / "trades.csv"))
    tracker = _tracker(tmp_path)

    result = await executor.execute(
        signal=_signal("BUY", market_price=0.5, max_price=0.5),
        trade_params=_trade_params(),
        tracker=tracker,
        adjusted_size_usd=0.1,
    )
    assert result.status == "SKIPPED"
    assert result.reason == "below_min_notional"
    assert client.calls == []


@pytest.mark.asyncio
async def test_rejected_order_does_not_update_tracker(tmp_path):
    client = DummyClient(
        OrderResult(
            order_id="o4",
            status="rejected",
            filled_size=0.0,
            filled_price=0.0,
            timestamp="2026-01-01T00:00:00+00:00",
        )
    )
    executor = Executor(client=client, trade_log_path=str(tmp_path / "trades.csv"))
    tracker = _tracker(tmp_path)

    result = await executor.execute(
        signal=_signal("BUY", market_price=0.25, max_price=0.25),
        trade_params=_trade_params(),
        tracker=tracker,
        adjusted_size_usd=5.0,
    )
    assert result.status == "REJECTED"
    assert tracker.get_position("tok1") is None


@pytest.mark.asyncio
async def test_dry_run_never_places_order(tmp_path):
    client = DummyClient(
        OrderResult(
            order_id="o5",
            status="filled",
            filled_size=10.0,
            filled_price=0.2,
            timestamp="2026-01-01T00:00:00+00:00",
        )
    )
    executor = Executor(client=client, trade_log_path=str(tmp_path / "trades.csv"))
    tracker = _tracker(tmp_path)

    result = await executor.execute(
        signal=_signal("BUY", market_price=0.2, max_price=0.2),
        trade_params=_trade_params(),
        tracker=tracker,
        adjusted_size_usd=2.0,
        dry_run=True,
    )
    assert result.status == "DRY_RUN"
    assert client.calls == []
