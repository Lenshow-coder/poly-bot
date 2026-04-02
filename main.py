"""Poly-bot entry point."""
import argparse
import asyncio
import logging

import yaml

from core.utils import setup_logging, load_config, ensure_data_dir
from core.polymarket_client import PolymarketClient
from core.signal import evaluate_signals

logger = logging.getLogger("poly-bot")


def load_plugin_config(path: str) -> dict:
    """Load a plugin's config.yaml file."""
    with open(path) as f:
        return yaml.safe_load(f)


KNOWN_PLUGINS = {"nhl_stanley_cup"}
KNOWN_SCRAPERS = {"stub", "csv"}


def load_plugins(config, client):
    """Load enabled market plugins based on config."""
    plugins = []
    for market_name in config.get("enabled_markets", []):
        if market_name == "nhl_stanley_cup":
            from markets.nhl_stanley_cup.plugin import NHLStanleyCupPlugin
            plugin_config = load_plugin_config(f"markets/{market_name}/config.yaml")
            plugins.append(NHLStanleyCupPlugin(plugin_config, client, config))
        elif market_name not in KNOWN_PLUGINS:
            logger.warning(f"Unknown market plugin: '{market_name}' — skipping")
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
        elif scraper_cfg["name"] == "stub":
            from scrapers.stub_scraper import StubScraper
            scrapers.append(StubScraper(
                name=scraper_cfg["name"],
                interval=scraper_cfg["interval"],
            ))
        elif scraper_cfg["name"] not in KNOWN_SCRAPERS:
            logger.warning(f"Unknown scraper: '{scraper_cfg['name']}' — skipping")
    return scrapers


async def dry_run_cycle(scrapers, plugins, client, config):
    """Run one cycle: scrape → fair values → signals. Log everything, execute nothing."""
    kelly_bankroll = config.get("risk", {}).get("kelly_bankroll", 1000)

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
                logger.info(
                    f"  {fv.outcome_name}: fair={fv.fair_value:.3f} "
                    f"ask={pm.best_ask} bid={pm.best_bid} sources={fv.sources_agreeing}"
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
