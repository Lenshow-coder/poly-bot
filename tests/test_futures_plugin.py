"""Tests for FuturesPlugin — extract_odds, compute_fair_values, and config merging.

FuturesPlugin.__init__ calls Gamma API via client.get_event(). These tests
construct a partially-initialized plugin to avoid hitting live APIs.
"""
import pytest

from markets.base import OutcomeFairValue, TradeParams
from markets.fair_value import FairValueEngine
from markets.futures_plugin import FuturesPlugin
from scrapers.models import BookOdds, EventOdds, ScrapedOdds
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers — build a FuturesPlugin without hitting Gamma API
# ---------------------------------------------------------------------------

def _make_plugin(
    token_map: dict[str, str] | None = None,
    plugin_trade_params: dict | None = None,
    sportsbook_weights: dict[str, float] | None = None,
    event_key: str = "Stanley Cup Winner",
) -> FuturesPlugin:
    """Create a FuturesPlugin with injected token_map, bypassing __init__."""
    if token_map is None:
        token_map = {
            "Toronto Maple Leafs": "0xtoronto",
            "Montreal Canadiens": "0xmontreal",
            "Edmonton Oilers": "0xedmonton",
        }
    if sportsbook_weights is None:
        sportsbook_weights = {"draftkings": 1.0, "fanduel": 1.0, "betmgm": 1.0}

    global_config = {
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
        "sportsbook_weight_defaults": sportsbook_weights,
    }

    # Bypass __init__ to avoid Gamma API call
    plugin = object.__new__(FuturesPlugin)
    plugin.name = "Test Plugin"
    plugin.event_key = event_key
    plugin.token_map = token_map
    plugin.trade_params = TradeParams.from_config(
        plugin_trade_params or {}, defaults=global_config["trade_defaults"]
    )
    merged_weights = {**sportsbook_weights}
    plugin.fair_value_engine = FairValueEngine(merged_weights)
    return plugin


def _make_scraped_odds(
    event_key: str = "Stanley Cup Winner",
    outcomes: dict[str, list[BookOdds]] | None = None,
) -> ScrapedOdds:
    if outcomes is None:
        outcomes = {
            "Toronto Maple Leafs": [
                BookOdds("draftkings", 4.00),
                BookOdds("fanduel", 4.50),
            ],
            "Montreal Canadiens": [
                BookOdds("draftkings", 8.00),
                BookOdds("fanduel", 7.00),
            ],
            "Edmonton Oilers": [
                BookOdds("draftkings", 6.00),
                BookOdds("fanduel", 5.50),
            ],
        }
    return ScrapedOdds(
        timestamp=datetime(2026, 4, 1, tzinfo=timezone.utc),
        events={
            event_key: EventOdds(event_name=event_key, outcomes=outcomes),
        },
    )


# ---------------------------------------------------------------------------
# extract_odds
# ---------------------------------------------------------------------------

def test_extract_odds_filters_to_known_outcomes():
    """Only outcomes in token_map are returned."""
    plugin = _make_plugin(
        token_map={"Toronto Maple Leafs": "0xtoronto"},
    )
    scraped = _make_scraped_odds(outcomes={
        "Toronto Maple Leafs": [BookOdds("draftkings", 4.00)],
        "Unknown Team": [BookOdds("draftkings", 20.00)],
    })
    result = plugin.extract_odds(scraped)
    assert "Toronto Maple Leafs" in result
    assert "Unknown Team" not in result


def test_extract_odds_wrong_event_key():
    """Wrong event key returns empty dict."""
    plugin = _make_plugin(event_key="NBA Champion")
    scraped = _make_scraped_odds(event_key="Stanley Cup Winner")
    result = plugin.extract_odds(scraped)
    assert result == {}


def test_extract_odds_empty_scraped():
    """Empty ScrapedOdds returns empty dict."""
    plugin = _make_plugin()
    scraped = ScrapedOdds(
        timestamp=datetime(2026, 4, 1, tzinfo=timezone.utc), events={}
    )
    result = plugin.extract_odds(scraped)
    assert result == {}


def test_extract_odds_preserves_all_books():
    """All BookOdds for a matched outcome are preserved."""
    plugin = _make_plugin()
    scraped = _make_scraped_odds()
    result = plugin.extract_odds(scraped)
    leafs = result["Toronto Maple Leafs"]
    assert len(leafs) == 2
    books = {bo.sportsbook for bo in leafs}
    assert books == {"draftkings", "fanduel"}


# ---------------------------------------------------------------------------
# compute_fair_values
# ---------------------------------------------------------------------------

def test_compute_fair_values_returns_all_outcomes():
    """Each outcome in mapped_odds gets a fair value."""
    plugin = _make_plugin()
    scraped = _make_scraped_odds()
    mapped = plugin.extract_odds(scraped)
    fvs = plugin.compute_fair_values(mapped)
    names = {fv.outcome_name for fv in fvs}
    assert names == {"Toronto Maple Leafs", "Montreal Canadiens", "Edmonton Oilers"}


def test_compute_fair_values_sum_to_one():
    """Fair values should normalize to ~1.0."""
    plugin = _make_plugin()
    scraped = _make_scraped_odds()
    mapped = plugin.extract_odds(scraped)
    fvs = plugin.compute_fair_values(mapped)
    total = sum(fv.fair_value for fv in fvs)
    assert abs(total - 1.0) < 0.001


def test_compute_fair_values_token_ids():
    """Each fair value has the correct token_id from token_map."""
    plugin = _make_plugin()
    scraped = _make_scraped_odds()
    mapped = plugin.extract_odds(scraped)
    fvs = plugin.compute_fair_values(mapped)
    by_name = {fv.outcome_name: fv for fv in fvs}
    assert by_name["Toronto Maple Leafs"].token_id == "0xtoronto"
    assert by_name["Montreal Canadiens"].token_id == "0xmontreal"


def test_compute_fair_values_sources_count():
    """sources_agreeing counts distinct sportsbooks."""
    plugin = _make_plugin()
    scraped = _make_scraped_odds()
    mapped = plugin.extract_odds(scraped)
    fvs = plugin.compute_fair_values(mapped)
    for fv in fvs:
        assert fv.sources_agreeing == 2  # draftkings and fanduel


def test_compute_fair_values_best_book_tracked():
    """best_book_name and best_book_implied_prob are populated."""
    plugin = _make_plugin()
    scraped = _make_scraped_odds()
    mapped = plugin.extract_odds(scraped)
    fvs = plugin.compute_fair_values(mapped)
    for fv in fvs:
        assert fv.best_book_name != ""
        assert fv.best_book_implied_prob > 0


def test_compute_fair_values_book_devigged_present():
    """book_devigged dict is populated with per-book probs."""
    plugin = _make_plugin()
    scraped = _make_scraped_odds()
    mapped = plugin.extract_odds(scraped)
    fvs = plugin.compute_fair_values(mapped)
    for fv in fvs:
        assert fv.book_devigged is not None
        assert len(fv.book_devigged) > 0


def test_compute_fair_values_single_book():
    """Works correctly with only one sportsbook."""
    plugin = _make_plugin()
    odds = {
        "Toronto Maple Leafs": [BookOdds("draftkings", 4.00)],
        "Montreal Canadiens": [BookOdds("draftkings", 3.00)],
        "Edmonton Oilers": [BookOdds("draftkings", 5.00)],
    }
    fvs = plugin.compute_fair_values(odds)
    total = sum(fv.fair_value for fv in fvs)
    assert abs(total - 1.0) < 0.001


# ---------------------------------------------------------------------------
# Trade params merging
# ---------------------------------------------------------------------------

def test_plugin_trade_params_override():
    """Plugin-level trade params override global defaults."""
    plugin = _make_plugin(plugin_trade_params={"edge_threshold": 0.20})
    assert plugin.trade_params.edge_threshold == 0.20
    # Other params stay at defaults
    assert plugin.trade_params.kelly_fraction == 0.25


def test_plugin_trade_params_defaults():
    """With no plugin overrides, global defaults are used."""
    plugin = _make_plugin()
    assert plugin.trade_params.edge_threshold == 0.10
    assert plugin.trade_params.order_type == "FAK"


# ---------------------------------------------------------------------------
# Sportsbook weight merging
# ---------------------------------------------------------------------------

def test_plugin_custom_weights():
    """Plugin with custom weights uses them in fair value computation."""
    # FanDuel weighted 5x vs DraftKings 1x
    plugin = _make_plugin(sportsbook_weights={"draftkings": 1.0, "fanduel": 5.0})
    odds = {
        "Toronto Maple Leafs": [
            BookOdds("draftkings", 4.00),
            BookOdds("fanduel", 5.00),
        ],
        "Montreal Canadiens": [
            BookOdds("draftkings", 3.00),
            BookOdds("fanduel", 3.00),
        ],
    }
    fvs = plugin.compute_fair_values(odds)
    by_name = {fv.outcome_name: fv for fv in fvs}
    leafs_fv = by_name["Toronto Maple Leafs"].fair_value

    # With equal weights, build a reference
    plugin_equal = _make_plugin(sportsbook_weights={"draftkings": 1.0, "fanduel": 1.0})
    fvs_equal = plugin_equal.compute_fair_values(odds)
    by_name_equal = {fv.outcome_name: fv for fv in fvs_equal}
    leafs_equal = by_name_equal["Toronto Maple Leafs"].fair_value

    # FanDuel has lower implied prob (1/5=0.20 vs 1/4=0.25) for Leafs.
    # Heavy FanDuel weight should pull the fair value lower.
    assert leafs_fv < leafs_equal
