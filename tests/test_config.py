"""Tests for config validation and TradeParams loading."""
import pytest
import yaml
from pathlib import Path

from main import validate_config, REQUIRED_TRADE_DEFAULTS, REQUIRED_SPORTSBOOK_SIGNALS
from markets.base import TradeParams


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_config(overrides: dict | None = None) -> dict:
    """Return a valid config dict with all required keys present."""
    cfg = {
        "trade_defaults": {
            "edge_threshold": 0.10,
            "max_outcome_exposure": 200,
            "kelly_fraction": 0.25,
            "min_bet_size": 5,
            "max_bet_size": 100,
            "order_type": "FAK",
            "min_sources": 2,
            "cooldown_minutes": 30,
            "price_range": [0.01, 0.66],
            "sportsbook_buffer": 0.05,
        },
        "sportsbook_signals": {
            "enabled": False,
        },
    }
    if overrides:
        for section, vals in overrides.items():
            cfg.setdefault(section, {}).update(vals)
    return cfg


FULL_DEFAULTS = _minimal_config()["trade_defaults"]


# ---------------------------------------------------------------------------
# validate_config — trade_defaults
# ---------------------------------------------------------------------------

def test_validate_config_passes_with_all_keys():
    """No exception when all required keys present."""
    validate_config(_minimal_config())


@pytest.mark.parametrize("missing_key", REQUIRED_TRADE_DEFAULTS)
def test_validate_config_missing_trade_default(missing_key):
    """Each required trade_defaults key raises KeyError when absent."""
    cfg = _minimal_config()
    del cfg["trade_defaults"][missing_key]
    with pytest.raises(KeyError, match="trade_defaults"):
        validate_config(cfg)


def test_validate_config_empty_trade_defaults():
    cfg = _minimal_config()
    cfg["trade_defaults"] = {}
    with pytest.raises(KeyError, match="trade_defaults"):
        validate_config(cfg)


def test_validate_config_missing_trade_defaults_section():
    cfg = _minimal_config()
    del cfg["trade_defaults"]
    with pytest.raises(KeyError, match="trade_defaults"):
        validate_config(cfg)


# ---------------------------------------------------------------------------
# validate_config — sportsbook_signals
# ---------------------------------------------------------------------------

def test_validate_config_sportsbook_signals_disabled():
    """When disabled, missing keys are not checked."""
    cfg = _minimal_config()
    cfg["sportsbook_signals"] = {"enabled": False}
    validate_config(cfg)  # should not raise


def test_validate_config_sportsbook_signals_enabled_all_keys():
    cfg = _minimal_config()
    cfg["sportsbook_signals"] = {
        "enabled": True,
        "edge_threshold": 0.1,
        "abs_edge_threshold": 0.01,
        "min_sources": 3,
    }
    validate_config(cfg)


@pytest.mark.parametrize("missing_key", REQUIRED_SPORTSBOOK_SIGNALS)
def test_validate_config_sportsbook_signals_missing_key(missing_key):
    cfg = _minimal_config()
    cfg["sportsbook_signals"] = {
        "enabled": True,
        "edge_threshold": 0.1,
        "abs_edge_threshold": 0.01,
        "min_sources": 3,
    }
    del cfg["sportsbook_signals"][missing_key]
    with pytest.raises(KeyError, match="sportsbook_signals"):
        validate_config(cfg)


# ---------------------------------------------------------------------------
# TradeParams.from_config — merging defaults + overrides
# ---------------------------------------------------------------------------

def test_trade_params_from_defaults_only():
    """Construct from global defaults with no plugin overrides."""
    tp = TradeParams.from_config({}, defaults=FULL_DEFAULTS)
    assert tp.edge_threshold == 0.10
    assert tp.order_type == "FAK"
    assert tp.price_range == (0.01, 0.66)
    assert tp.min_sources == 2


def test_trade_params_plugin_overrides():
    """Plugin values override global defaults."""
    plugin = {"edge_threshold": 0.15, "max_bet_size": 50}
    tp = TradeParams.from_config(plugin, defaults=FULL_DEFAULTS)
    assert tp.edge_threshold == 0.15
    assert tp.max_bet_size == 50
    # Non-overridden keys remain at defaults
    assert tp.kelly_fraction == 0.25
    assert tp.min_sources == 2


def test_trade_params_no_defaults():
    """Works when plugin provides all keys and no defaults are given."""
    tp = TradeParams.from_config(FULL_DEFAULTS, defaults=None)
    assert tp.edge_threshold == 0.10
    assert tp.sportsbook_buffer == 0.05


def test_trade_params_missing_key_raises():
    """Missing required key raises KeyError."""
    incomplete = {k: v for k, v in FULL_DEFAULTS.items() if k != "edge_threshold"}
    with pytest.raises(KeyError):
        TradeParams.from_config(incomplete, defaults=None)


def test_trade_params_price_range_is_tuple():
    """price_range should always be a tuple, even if input is a list."""
    tp = TradeParams.from_config(FULL_DEFAULTS)
    assert isinstance(tp.price_range, tuple)
    assert len(tp.price_range) == 2


# ---------------------------------------------------------------------------
# Real config.yaml smoke test
# ---------------------------------------------------------------------------

def test_real_config_yaml_validates():
    """The actual config.yaml in the repo passes validation."""
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if not config_path.exists():
        pytest.skip("config.yaml not present")
    with open(config_path) as f:
        config = yaml.safe_load(f)
    validate_config(config)
