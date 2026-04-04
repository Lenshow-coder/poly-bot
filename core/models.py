from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional


@dataclass
class PriceInfo:
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    midpoint: Optional[float] = None


@dataclass
class MarketInfo:
    condition_id: str
    question: str
    outcome_name: str
    yes_token_id: str
    no_token_id: str
    active: bool


@dataclass
class EventInfo:
    event_id: str
    event_slug: str
    title: str
    neg_risk: bool
    markets: list[MarketInfo] = None

    def __post_init__(self):
        if self.markets is None:
            self.markets = []


@dataclass
class OrderResult:
    order_id: str
    status: str
    filled_size: float
    filled_price: float
    timestamp: str


@dataclass
class Position:
    token_id: str
    outcome_name: str
    market_name: str
    side: str
    size: float
    avg_cost: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class BankrollSnapshot:
    usdc_balance: float
    positions_value: float
    total_bankroll: float
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BankrollSnapshot":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Signal:
    token_id: str
    outcome_name: str
    event_name: str
    side: str               # "BUY" or "SELL"
    edge: float             # relative edge (0.0 - 1.0)
    fair_value: float       # our computed probability
    market_price: float     # best_ask (BUY) or best_bid (SELL)
    size_usd: float         # bet size in USDC (from Kelly)
    max_price: Optional[float] = None  # for BUY: max price willing to pay
    min_price: Optional[float] = None  # for SELL: min price willing to accept
    reason: str = "edge_detected"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SportsbookSignal:
    outcome_name: str
    event_name: str
    outlier_book: str
    outlier_devigged_prob: float
    consensus_prob: float
    edge: float
    direction: str              # "UNDER" or "OVER"
