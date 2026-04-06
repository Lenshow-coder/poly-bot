from datetime import timedelta

from core.position_tracker import PositionTracker
from core.state import StateManager


def _tracker(tmp_path) -> PositionTracker:
    return PositionTracker(StateManager(state_dir=str(tmp_path)))


def test_average_cost_multiple_buys(tmp_path):
    tracker = _tracker(tmp_path)
    tracker.apply_fill("tok1", "Team A", "Event A", "BUY", 10, 0.20)
    tracker.apply_fill("tok1", "Team A", "Event A", "BUY", 30, 0.40)
    pos = tracker.get_position("tok1")
    assert pos is not None
    assert pos.size == 40
    assert abs(pos.avg_cost - 0.35) < 1e-9


def test_sell_reduces_and_cleans_zero_position(tmp_path):
    tracker = _tracker(tmp_path)
    tracker.apply_fill("tok1", "Team A", "Event A", "BUY", 10, 0.25)
    tracker.apply_fill("tok1", "Team A", "Event A", "SELL", 4, 0.40)
    assert tracker.get_position("tok1").size == 6
    tracker.apply_fill("tok1", "Team A", "Event A", "SELL", 6, 0.30)
    assert tracker.get_position("tok1") is None


def test_cooldown_lifecycle(tmp_path):
    tracker = _tracker(tmp_path)
    tracker.mark_traded("tok1", cooldown_minutes=30)
    assert tracker.is_on_cooldown("tok1") is True

    tracker.cooldowns["tok1"] = tracker._utcnow() - timedelta(seconds=1)
    assert tracker.is_on_cooldown("tok1") is False


def test_reconciliation_adopts_api_values(tmp_path):
    tracker = _tracker(tmp_path)
    tracker.apply_fill("tok1", "Team A", "Event A", "BUY", 10, 0.20)
    tracker.sync_from_api(
        [
            {
                "asset": "tok1",
                "size": "8",
                "avgPrice": "0.22",
                "outcome": "Team A",
                "title": "Event A",
            }
        ],
        reconcile_tolerance_pct=0.01,
        local_fill_grace_seconds=0,
    )
    pos = tracker.get_position("tok1")
    assert pos.size == 8
    assert abs(pos.avg_cost - 0.22) < 1e-9


def test_snapshot_bankroll_uses_mark_prices(tmp_path):
    tracker = _tracker(tmp_path)
    tracker.apply_fill("tok1", "Team A", "Event A", "BUY", 10, 0.20)
    snapshot = tracker.snapshot_bankroll(
        exchange_balance=100.0,
        prices_by_token={"tok1": 0.5},
    )
    assert snapshot.usdc_balance == 100.0
    assert snapshot.positions_value == 5.0
    assert snapshot.total_bankroll == 105.0


def test_empty_sync_does_not_wipe_immediately(tmp_path):
    tracker = _tracker(tmp_path)
    tracker.apply_fill("tok1", "Team A", "Event A", "BUY", 10, 0.20)
    tracker.sync_from_api([], empty_sync_threshold=2, local_fill_grace_seconds=0)
    assert tracker.get_position("tok1") is not None
    tracker.sync_from_api(
        [],
        empty_sync_threshold=2,
        local_fill_grace_seconds=0,
        missing_sync_threshold=1,
    )
    assert tracker.get_position("tok1") is None


def test_recent_local_fill_prevents_stale_overwrite(tmp_path):
    tracker = _tracker(tmp_path)
    tracker.apply_fill("tok1", "Team A", "Event A", "BUY", 10, 0.20)
    tracker.sync_from_api(
        [{"asset": "tok1", "size": "5", "avgPrice": "0.22"}],
        local_fill_grace_seconds=60,
    )
    pos = tracker.get_position("tok1")
    assert pos.size == 10


def test_reconcile_guard_state_persists_across_restart(tmp_path):
    state_dir = tmp_path / "state"
    tracker = PositionTracker(StateManager(state_dir=str(state_dir)))
    tracker.apply_fill("tok1", "Team A", "Event A", "BUY", 10, 0.20)
    tracker.consecutive_empty_syncs = 2
    tracker.missing_seen_counts["tok1"] = 1
    tracker.save_state()

    reloaded = PositionTracker(StateManager(state_dir=str(state_dir)))
    assert "tok1" in reloaded.last_local_trade_at
    assert reloaded.consecutive_empty_syncs == 2
    assert reloaded.missing_seen_counts["tok1"] == 1
