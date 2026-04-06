from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class BookOdds:
    sportsbook: str       # lowercase, e.g., "bet365"
    decimal_odds: float   # European decimal format, e.g., 4.50


@dataclass
class EventOdds:
    event_name: str                            # e.g., "2026 NHL Stanley Cup Champion"
    outcomes: dict[str, list[BookOdds]]        # sportsbook-native outcome name → list of BookOdds
    timestamp: Optional[datetime] = None        # latest scrape timestamp for this market/event batch


@dataclass
class ScrapedOdds:
    timestamp: datetime
    events: dict[str, EventOdds]               # event name → EventOdds
