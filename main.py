"""Poly-bot entry point. Phase 1: loads config, inits client, prints status."""

from core.utils import setup_logging, load_config, ensure_data_dir
from core.polymarket_client import PolymarketClient


def main():
    config = load_config()
    logger = setup_logging(
        level=config.get("logging", {}).get("level", "INFO"),
        console=config.get("logging", {}).get("console", True),
    )
    ensure_data_dir()

    logger.info("Initializing Polymarket client...")
    client = PolymarketClient(config)

    balance = client.get_usdc_balance()
    logger.info(f"USDC balance: ${balance:.2f}")
    logger.info("Poly-bot ready.")


if __name__ == "__main__":
    main()
