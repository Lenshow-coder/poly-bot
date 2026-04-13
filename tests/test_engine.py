from datetime import datetime, timedelta, timezone

import pytest

from core.engine import Engine
from core.models import PriceInfo, RiskDecision, Signal
from markets.base import OutcomeFairValue, TradeParams
from scrapers.models import BookOdds, EventOdds, ScrapedOdds


class _BookLevel:
    def __init__(self, price, size):
        self.price = price
        self.size = size


class _OrderBook:
    def __init__(self, bids, asks):
        self.bids = bids
        self.asks = asks


class DummyClient:
    def get_exchange_balance(self):
        return 100.0

    def get_positions(self):
        return []

    def get_order_book(self, token_id: str):
        return _OrderBook(
            bids=[_BookLevel(0.2, 500)],
            asks=[_BookLevel(0.21, 500)],
        )


class DummyPlugin:
    def __init__(self, name="Plugin", return_mapped=True):
        self.name = name
        self.return_mapped = return_mapped
        self.extract_calls = 0
        self.event_key = "Event A"

    def get_name(self):
        return self.name

    def get_token_ids(self):
        return ["tok1"]

    def extract_odds(self, scraped_odds):
        self.extract_calls += 1
        if not self.return_mapped:
            return {}
        return {"Team A": [BookOdds("fanduel", 4.0)]}

    def compute_fair_values(self, mapped_odds):
        return [OutcomeFairValue("Team A", "tok1", 0.4, 3)]

    def get_trade_params(self):
        return TradeParams(
            edge_threshold=0.1,
            max_outcome_exposure=100,
            kelly_fraction=0.25,
            min_bet_size=5,
            max_bet_size=100,
            order_type="FOK",
            min_sources=2,
            cooldown_minutes=30,
            price_range=(0.01, 0.99),
            sportsbook_buffer=0.0,
        )


def _config():
    return {
        "engine": {
            "dry_run": True,
            "position_sync_interval": 1,
            "loop_error_backoff_seconds": 0,
            "trade_log_path": "data/test_trades.csv",
        },
        "risk": {
            "kelly_bankroll": 1000,
            "max_event_exposure": 100,
            "max_portfolio_exposure": 200,
            "min_balance": 0,
            "min_bankroll": 0,
            "reconcile_tolerance_pct": 0.05,
            "size_clip_enabled": True,
        },
    }


def _scraped():
    return ScrapedOdds(
        timestamp=datetime.now(timezone.utc),
        events={"Event A": EventOdds("Event A", outcomes={"Team A": [BookOdds("fanduel", 4.0)]})},
    )


@pytest.mark.asyncio
async def test_process_routes_to_matching_plugin(tmp_path):
    engine = Engine(_config(), DummyClient(), [DummyPlugin(return_mapped=True)], [])
    engine.executor.trade_logger.path = tmp_path / "trades.csv"
    engine.executor.trade_logger._ensure_header()
    await engine.process_scraper_result(_scraped(), "csv")
    assert engine.plugins[0].extract_calls == 1


@pytest.mark.asyncio
async def test_risk_rejection_prevents_execution(tmp_path, monkeypatch):
    engine = Engine(_config(), DummyClient(), [DummyPlugin(return_mapped=True)], [])
    engine.executor.trade_logger.path = tmp_path / "trades.csv"
    engine.executor.trade_logger._ensure_header()

    async def _never_execute(**kwargs):
        raise AssertionError("Executor should not be called on risk rejection")

    engine.executor.execute = _never_execute
    engine.risk_manager.approve = lambda **kwargs: RiskDecision(False, 0.0, "blocked")

    await engine.process_scraper_result(_scraped(), "csv")


@pytest.mark.asyncio
async def test_approved_signal_calls_executor(tmp_path):
    engine = Engine(_config(), DummyClient(), [DummyPlugin(return_mapped=True)], [])
    engine.executor.trade_logger.path = tmp_path / "trades.csv"
    engine.executor.trade_logger._ensure_header()
    called = {"count": 0}

    async def _execute(**kwargs):
        called["count"] += 1
        return None

    engine.executor.execute = _execute
    engine.risk_manager.approve = lambda **kwargs: RiskDecision(True, 10.0, "ok")
    await engine.process_scraper_result(_scraped(), "csv")
    assert called["count"] >= 1


@pytest.mark.asyncio
async def test_uses_event_specific_scrape_timestamp(tmp_path):
    engine = Engine(_config(), DummyClient(), [DummyPlugin(return_mapped=True)], [])
    engine.executor.trade_logger.path = tmp_path / "trades.csv"
    engine.executor.trade_logger._ensure_header()
    captured = {}

    async def _execute(**kwargs):
        captured["scrape_timestamp"] = kwargs.get("scrape_timestamp")
        return None

    engine.executor.execute = _execute
    engine.risk_manager.approve = lambda **kwargs: RiskDecision(True, 10.0, "ok")

    scraped = ScrapedOdds(
        timestamp=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
        events={
            "Event A": EventOdds(
                "Event A",
                outcomes={"Team A": [BookOdds("fanduel", 4.0)]},
                timestamp=datetime(2026, 4, 1, 11, 59, tzinfo=timezone.utc),
            )
        },
    )
    await engine.process_scraper_result(scraped, "csv")
    assert captured["scrape_timestamp"] == datetime(2026, 4, 1, 11, 59, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_scraper_exception_isolated(monkeypatch):
    class BadScraper:
        interval = 0

        def get_name(self):
            return "bad"

        async def scrape(self):
            raise RuntimeError("boom")

    engine = Engine(_config(), DummyClient(), [DummyPlugin()], [BadScraper()])

    async def _sleep(_):
        engine._stop_event.set()
        return None

    monkeypatch.setattr("core.engine.asyncio.sleep", _sleep)
    await engine._scraper_loop(BadScraper())


@pytest.mark.asyncio
async def test_stale_scrape_data_skipped(tmp_path):
    cfg = _config()
    cfg["engine"]["max_scrape_age_seconds"] = 600
    plugin = DummyPlugin(return_mapped=True)
    engine = Engine(cfg, DummyClient(), [plugin], [])
    engine.executor.trade_logger.path = tmp_path / "trades.csv"
    engine.executor.trade_logger._ensure_header()

    stale = ScrapedOdds(
        timestamp=datetime.now(timezone.utc) - timedelta(seconds=900),
        events={"Event A": EventOdds("Event A", outcomes={"Team A": [BookOdds("fanduel", 4.0)]})},
    )
    await engine.process_scraper_result(stale, "csv")
    assert plugin.extract_calls == 0
