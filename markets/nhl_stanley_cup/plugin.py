import logging

from core.polymarket_client import PolymarketClient
from markets.base import MarketPlugin, OutcomeFairValue, TradeParams
from markets.nhl_stanley_cup.fair_value import FairValueEngine
from scrapers.models import BookOdds, ScrapedOdds

logger = logging.getLogger(__name__)


class NHLStanleyCupPlugin(MarketPlugin):
    def __init__(self, plugin_config: dict, client: PolymarketClient, global_config: dict):
        self.config = plugin_config
        self.name = plugin_config["name"]
        self.event_key = plugin_config["scraper"]["event_key"]

        # Merge global defaults with plugin overrides
        trade_defaults = global_config.get("trade_defaults", {})
        self.trade_params = TradeParams.from_config(
            plugin_config.get("trade_params", {}), defaults=trade_defaults
        )

        # Sportsbook weights: global defaults + plugin overrides
        weight_defaults = global_config.get("sportsbook_weight_defaults", {})
        plugin_weights = plugin_config.get("sportsbook_weights", {})
        merged_weights = {**weight_defaults, **plugin_weights}
        self.fair_value_engine = FairValueEngine(merged_weights)

        # Auto-discover all outcomes from Polymarket
        self.token_map: dict[str, str] = {}  # { outcome_name: yes_token_id }
        self._resolve_tokens(client, plugin_config["polymarket"]["event_slug"])

    def _resolve_tokens(self, client: PolymarketClient, slug: str):
        """Query Gamma API and map all outcome names to token IDs."""
        event = client.get_event(slug)
        for market in event.markets:
            self.token_map[market.outcome_name] = market.yes_token_id

        logger.info(
            f"Auto-discovered {len(self.token_map)} outcomes for {self.name}"
        )

    def get_name(self) -> str:
        return self.name

    def get_token_ids(self) -> list[str]:
        return list(self.token_map.values())

    def get_trade_params(self) -> TradeParams:
        return self.trade_params

    def extract_odds(self, scraped_odds: ScrapedOdds) -> dict[str, list[BookOdds]]:
        """
        Filter ScrapedOdds to this plugin's event, keeping only outcomes
        that exist on Polymarket.

        Returns: { outcome_name: [BookOdds, ...] }
        """
        event_odds = scraped_odds.events.get(self.event_key)
        if not event_odds:
            return {}

        mapped: dict[str, list[BookOdds]] = {}
        for team_name, odds_list in event_odds.outcomes.items():
            if team_name in self.token_map:
                mapped[team_name] = odds_list

        return mapped

    def compute_fair_values(self, mapped_odds: dict) -> list[OutcomeFairValue]:
        fair_probs = self.fair_value_engine.compute(mapped_odds)
        results = []
        for name, prob in fair_probs.items():
            token_id = self.token_map.get(name)
            if token_id is None:
                continue
            sources = len({b.sportsbook for b in mapped_odds.get(name, [])})
            results.append(OutcomeFairValue(
                outcome_name=name,
                token_id=token_id,
                fair_value=prob,
                sources_agreeing=sources,
            ))
        return results
