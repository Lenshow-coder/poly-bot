"""Poly-bot entry point."""
import argparse
import asyncio
import logging
from pathlib import Path

import yaml

from core.utils import setup_logging, load_config, ensure_data_dir
from core.polymarket_client import PolymarketClient
from core.signal import evaluate_signals
from core.sportsbook_signal import evaluate_sportsbook_signals

logger = logging.getLogger("poly-bot")
ROOT_DIR = Path(__file__).resolve().parent

REQUIRED_TRADE_DEFAULTS = [
    "edge_threshold", "max_outcome_exposure", "kelly_fraction",
    "min_bet_size", "max_bet_size", "order_type", "min_sources",
    "cooldown_minutes", "price_range", "sportsbook_buffer",
]

REQUIRED_SPORTSBOOK_SIGNALS = [
    "edge_threshold", "abs_edge_threshold", "min_sources",
]


def validate_config(config: dict):
    """Check that required config sections and keys exist. Raises on missing keys."""
    trade_defaults = config.get("trade_defaults", {})
    missing = [k for k in REQUIRED_TRADE_DEFAULTS if k not in trade_defaults]
    if missing:
        raise KeyError(f"Missing required keys in trade_defaults: {missing}")

    sb_cfg = config.get("sportsbook_signals", {})
    if sb_cfg.get("enabled", False):
        missing = [k for k in REQUIRED_SPORTSBOOK_SIGNALS if k not in sb_cfg]
        if missing:
            raise KeyError(f"Missing required keys in sportsbook_signals: {missing}")


def load_plugin_config(path: str) -> dict:
    """Load a plugin's config.yaml file."""
    with open(path) as f:
        return yaml.safe_load(f)


KNOWN_SCRAPERS = {"csv"}


PLUGIN_TYPES: dict[str, type] = {}


def _register_plugin_types():
    """Import and register all plugin types. Called once at startup."""
    from markets.futures_plugin import FuturesPlugin
    PLUGIN_TYPES["futures"] = FuturesPlugin


def load_plugins(config, client):
    """Load enabled market plugins from markets/configs/ yaml files."""
    if not PLUGIN_TYPES:
        _register_plugin_types()

    plugins = []
    for market_name in config.get("enabled_markets", []):
        config_path = ROOT_DIR / "markets" / "configs" / f"{market_name}.yaml"
        try:
            plugin_config = load_plugin_config(config_path)
        except FileNotFoundError:
            logger.warning(f"No config found at {config_path} — skipping '{market_name}'")
            continue

        plugin_type = plugin_config.get("type", "futures")
        cls = PLUGIN_TYPES.get(plugin_type)
        if cls is None:
            logger.warning(f"Unknown plugin type '{plugin_type}' for '{market_name}' — skipping")
            continue

        plugins.append(cls(plugin_config, client, config))
    return plugins


def load_scrapers(config):
    """Load enabled scrapers based on config."""
    scrapers = []
    for scraper_cfg in config.get("scrapers", []):
        if scraper_cfg["name"] == "csv":
            from scrapers.csv_scraper import CsvScraper
            scrapers.append(CsvScraper(
                name=scraper_cfg["name"],
                interval=scraper_cfg["interval"],
                path=scraper_cfg["path"],
            ))
        elif scraper_cfg["name"] not in KNOWN_SCRAPERS:
            logger.warning(f"Unknown scraper: '{scraper_cfg['name']}' — skipping")
    return scrapers


async def dry_run_cycle(scrapers, plugins, client, config):
    """Run one cycle: scrape → fair values → signals. Log everything, execute nothing."""
    kelly_bankroll = config.get("risk", {}).get("kelly_bankroll", 1000)
    sb_cfg = config.get("sportsbook_signals", {})

    for scraper in scrapers:
        scraped_odds = await scraper.scrape()
        logger.info(f"Scraper '{scraper.get_name()}' returned {len(scraped_odds.events)} events")

        for plugin in plugins:
            mapped_odds = plugin.extract_odds(scraped_odds)
            if not mapped_odds:
                continue

            fair_values = plugin.compute_fair_values(mapped_odds)

            # Fetch live Polymarket prices for all plugin tokens
            prices = {}
            for fv in fair_values:
                try:
                    prices[fv.token_id] = client.get_prices(fv.token_id)
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch prices for {fv.outcome_name}: {e}"
                    )

            signals = evaluate_signals(
                fair_values=fair_values,
                polymarket_prices=prices,
                trade_params=plugin.get_trade_params(),
                kelly_bankroll=kelly_bankroll,
                event_name=plugin.get_name(),
            )

            # Log fair values
            for fv in fair_values:
                pm = prices.get(fv.token_id)
                if pm:
                    logger.info(
                        f"  {fv.outcome_name}: fair={fv.fair_value:.3f} "
                        f"ask={pm.best_ask} bid={pm.best_bid} sources={fv.sources_agreeing}"
                    )
                else:
                    logger.info(
                        f"  {fv.outcome_name}: fair={fv.fair_value:.3f} "
                        f"ask=N/A bid=N/A sources={fv.sources_agreeing} (no orderbook)"
                    )

            # Sportsbook signals: flag outlier books vs consensus
            if sb_cfg.get("enabled", False):
                sb_signals = evaluate_sportsbook_signals(
                    fair_values=fair_values,
                    event_name=plugin.get_name(),
                    edge_threshold=sb_cfg["edge_threshold"],
                    abs_edge_threshold=sb_cfg["abs_edge_threshold"],
                    min_sources=sb_cfg["min_sources"],
                )
                for sbs in sb_signals:
                    logger.info(
                        f"  [SPORTSBOOK] {sbs.outlier_book} {sbs.direction} on "
                        f"{sbs.outcome_name}: book={sbs.outlier_devigged_prob:.3f} "
                        f"consensus={sbs.consensus_prob:.3f} dev={sbs.edge:.1%}"
                    )

            # Log signals
            if signals:
                for sig in signals:
                    logger.info(
                        f"  [DRY RUN] Signal: {sig.side} {sig.outcome_name} "
                        f"| edge={sig.edge:.1%} fair={sig.fair_value:.3f} "
                        f"mkt={sig.market_price:.3f} kelly=${sig.size_usd:.2f}"
                    )
            else:
                logger.info(f"  No signals for {plugin.get_name()}")


def main():
    parser = argparse.ArgumentParser(description="Poly-bot")
    parser.add_argument("--dry-run", action="store_true", help="Log signals without executing")
    args = parser.parse_args()

    config = load_config()
    validate_config(config)
    dry_run = args.dry_run or config.get("engine", {}).get("dry_run", False)

    setup_logging(
        level=config.get("logging", {}).get("level", "INFO"),
        console=config.get("logging", {}).get("console", True),
    )
    ensure_data_dir()

    logger.info("Initializing Polymarket client...")
    client = PolymarketClient(config)

    exchange_balance = client.get_exchange_balance()
    logger.info(f"Exchange balance: ${exchange_balance:.2f}")

    plugins = load_plugins(config, client)
    scrapers = load_scrapers(config)
    logger.info(f"Loaded {len(plugins)} plugins, {len(scrapers)} scrapers")

    if dry_run:
        logger.info("Running in DRY RUN mode — no orders will be placed")
        asyncio.run(dry_run_cycle(scrapers, plugins, client, config))
    else:
        logger.info("Live mode not yet implemented (Phase 3)")


if __name__ == "__main__":
    main()
