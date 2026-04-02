from abc import ABC, abstractmethod
from dataclasses import dataclass

from scrapers.models import BookOdds, ScrapedOdds


@dataclass
class OutcomeFairValue:
    outcome_name: str       # canonical name, e.g., "Toronto Maple Leafs"
    token_id: str           # Polymarket YES token ID for this outcome
    fair_value: float       # devigged probability (0.0 - 1.0)
    sources_agreeing: int   # number of sportsbooks that contributed


@dataclass
class TradeParams:
    edge_threshold: float           # relative edge required (e.g., 0.10 = 10%)
    max_outcome_exposure: float     # max USDC per outcome
    kelly_fraction: float           # fraction of full Kelly (e.g., 0.25)
    min_bet_size: float             # minimum bet in USDC
    max_bet_size: float             # cap per trade in USDC
    order_type: str                 # "FOK" or "FAK"
    min_sources: int                # minimum sportsbooks required
    cooldown_minutes: int           # wait time after trade on same outcome
    price_range: tuple[float, float]  # only trade in this range

    @classmethod
    def from_config(cls, plugin_cfg: dict, defaults: dict | None = None) -> "TradeParams":
        """Load from plugin config, falling back to global defaults for missing keys.

        Args:
            plugin_cfg: Plugin's trade_params section (overrides).
            defaults: Global trade_defaults section from config.yaml (fallbacks).
        """
        merged = {**(defaults or {}), **plugin_cfg}
        return cls(
            edge_threshold=merged["edge_threshold"],
            max_outcome_exposure=merged["max_outcome_exposure"],
            kelly_fraction=merged["kelly_fraction"],
            min_bet_size=merged["min_bet_size"],
            max_bet_size=merged["max_bet_size"],
            order_type=merged.get("order_type", "FOK"),
            min_sources=merged.get("min_sources", 3),
            cooldown_minutes=merged.get("cooldown_minutes", 30),
            price_range=tuple(merged.get("price_range", [0.03, 0.95])),
        )


class MarketPlugin(ABC):
    @abstractmethod
    def get_name(self) -> str: ...

    @abstractmethod
    def get_token_ids(self) -> list[str]: ...

    @abstractmethod
    def extract_odds(self, scraped_odds: ScrapedOdds) -> dict[str, list[BookOdds]]: ...

    @abstractmethod
    def compute_fair_values(self, mapped_odds: dict) -> list[OutcomeFairValue]: ...

    @abstractmethod
    def get_trade_params(self) -> TradeParams: ...
