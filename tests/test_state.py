"""Tests for StateManager persistence."""
import json
import tempfile
from pathlib import Path

import pytest

from core.state import StateManager
from core.models import BankrollSnapshot, Position


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_position(**overrides) -> Position:
    defaults = dict(
        token_id="0xabc123",
        outcome_name="Toronto Maple Leafs",
        market_name="NHL Stanley Cup",
        side="BUY",
        size=25.0,
        avg_cost=0.18,
    )
    defaults.update(overrides)
    return Position(**defaults)


def _make_bankroll(**overrides) -> BankrollSnapshot:
    defaults = dict(
        usdc_balance=1500.0,
        positions_value=200.0,
        total_bankroll=1700.0,
        timestamp="2026-04-01T12:00:00",
    )
    defaults.update(overrides)
    return BankrollSnapshot(**defaults)


# ---------------------------------------------------------------------------
# Default state
# ---------------------------------------------------------------------------

def test_load_missing_file_returns_defaults():
    """When no state file exists, load() returns default state."""
    with tempfile.TemporaryDirectory() as d:
        sm = StateManager(state_dir=d)
        state = sm.load()

    assert state["bankroll"] is None
    assert state["positions"] == []
    assert state["cooldowns"] == {}


# ---------------------------------------------------------------------------
# Round-trip: save → load
# ---------------------------------------------------------------------------

def test_round_trip_bankroll():
    """Save and load a bankroll snapshot."""
    with tempfile.TemporaryDirectory() as d:
        sm = StateManager(state_dir=d)
        bankroll = _make_bankroll()
        sm.save(bankroll=bankroll)

        state = sm.load()
        assert isinstance(state["bankroll"], BankrollSnapshot)
        assert state["bankroll"].usdc_balance == 1500.0
        assert state["bankroll"].total_bankroll == 1700.0


def test_round_trip_positions():
    """Save and load positions."""
    with tempfile.TemporaryDirectory() as d:
        sm = StateManager(state_dir=d)
        pos = _make_position()
        sm.save(positions=[pos])

        state = sm.load()
        assert len(state["positions"]) == 1
        loaded = state["positions"][0]
        assert isinstance(loaded, Position)
        assert loaded.token_id == "0xabc123"
        assert loaded.side == "BUY"
        assert loaded.size == 25.0


def test_round_trip_cooldowns():
    """Save and load cooldowns dict."""
    cooldowns = {"0xabc123": "2026-04-01T12:30:00"}
    with tempfile.TemporaryDirectory() as d:
        sm = StateManager(state_dir=d)
        sm.save(cooldowns=cooldowns)

        state = sm.load()
        assert state["cooldowns"]["0xabc123"] == "2026-04-01T12:30:00"


def test_round_trip_full_state():
    """Save and load all state components together."""
    with tempfile.TemporaryDirectory() as d:
        sm = StateManager(state_dir=d)
        bankroll = _make_bankroll()
        positions = [
            _make_position(token_id="0x111", outcome_name="Team A"),
            _make_position(token_id="0x222", outcome_name="Team B"),
        ]
        cooldowns = {"0x111": "2026-04-01T13:00:00"}

        sm.save(bankroll=bankroll, positions=positions, cooldowns=cooldowns)
        state = sm.load()

        assert state["bankroll"].usdc_balance == 1500.0
        assert len(state["positions"]) == 2
        assert state["positions"][0].outcome_name == "Team A"
        assert state["positions"][1].outcome_name == "Team B"
        assert "0x111" in state["cooldowns"]


# ---------------------------------------------------------------------------
# Overwrite
# ---------------------------------------------------------------------------

def test_save_overwrites_previous():
    """Second save replaces the first."""
    with tempfile.TemporaryDirectory() as d:
        sm = StateManager(state_dir=d)
        sm.save(bankroll=_make_bankroll(usdc_balance=1000.0))
        sm.save(bankroll=_make_bankroll(usdc_balance=2000.0))

        state = sm.load()
        assert state["bankroll"].usdc_balance == 2000.0


# ---------------------------------------------------------------------------
# Corrupt file recovery
# ---------------------------------------------------------------------------

def test_corrupt_file_returns_defaults_and_creates_backup():
    """Corrupt JSON triggers backup and returns defaults."""
    with tempfile.TemporaryDirectory() as d:
        sm = StateManager(state_dir=d)
        # Write garbage to state file
        sm.state_path.write_text("NOT VALID JSON {{{", encoding="utf-8")

        state = sm.load()
        assert state["bankroll"] is None
        assert state["positions"] == []

        # Backup should exist
        backup = sm.state_path.with_suffix(".json.bak")
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == "NOT VALID JSON {{{"


def test_corrupt_structure_returns_defaults():
    """Valid JSON but wrong structure triggers recovery."""
    with tempfile.TemporaryDirectory() as d:
        sm = StateManager(state_dir=d)
        # Write valid JSON but positions have wrong structure
        sm.state_path.write_text(
            json.dumps({"bankroll": None, "positions": [{"bad_key": 1}], "cooldowns": {}}),
            encoding="utf-8",
        )

        state = sm.load()
        # Should recover to defaults due to Position.from_dict failure
        assert state["bankroll"] is None


# ---------------------------------------------------------------------------
# Empty save
# ---------------------------------------------------------------------------

def test_save_no_args():
    """Saving with no arguments creates valid empty state."""
    with tempfile.TemporaryDirectory() as d:
        sm = StateManager(state_dir=d)
        sm.save()

        state = sm.load()
        assert state["bankroll"] is None
        assert state["positions"] == []
        assert state["cooldowns"] == {}


def test_round_trip_reconcile_meta():
    """Save and load reconcile metadata used by tracker safety guards."""
    with tempfile.TemporaryDirectory() as d:
        sm = StateManager(state_dir=d)
        reconcile_meta = {
            "last_local_trade_at": {"tok1": "2026-04-01T12:00:00+00:00"},
            "consecutive_empty_syncs": 2,
            "missing_seen_counts": {"tok1": 1},
        }
        sm.save(reconcile_meta=reconcile_meta)
        state = sm.load()
        assert state["reconcile_meta"]["consecutive_empty_syncs"] == 2
        assert state["reconcile_meta"]["missing_seen_counts"]["tok1"] == 1
