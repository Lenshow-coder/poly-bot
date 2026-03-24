from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class PriceInfo:
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    midpoint: Optional[float] = None
    bid_liquidity: float = 0.0
    ask_liquidity: float = 0.0


@dataclass
class MarketInfo:
    condition_id: str
    question: str
    outcome_name: str
    yes_token_id: str
    no_token_id: str
    active: bool
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None


@dataclass
class EventInfo:
    event_id: str
    event_slug: str
    title: str
    neg_risk: bool
    markets: list[MarketInfo] = field(default_factory=list)


@dataclass
class OrderResult:
    order_id: str
    status: str
    filled_size: float
    filled_price: float
    timestamp: str
    raw_response: dict = field(default_factory=dict)


@dataclass
class Position:
    token_id: str
    outcome_name: str
    market_name: str
    side: str
    size: float
    avg_cost: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    last_trade_time: Optional[str] = None

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
    side: str  # "BUY" or "SELL"
    edge: float
    fair_value: float
    market_price: float
    max_price: Optional[float] = None
    min_price: Optional[float] = None
    size: Optional[float] = None
    reason: str = ""


@dataclass
class TradeParams:
    edge_threshold: float = 0.05
    max_outcome_exposure: float = 50.0
    max_event_exposure: float = 200.0
    max_portfolio_exposure: float = 500.0
    kelly_fraction: float = 0.25
    kelly_cap: float = 50.0
    order_type: str = "FOK"
    cooldown_seconds: int = 300
    min_price: float = 0.05
    max_price: float = 0.95
