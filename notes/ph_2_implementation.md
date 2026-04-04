# Phase 2: Scraper Interface + First Market Plugin — Implementation Plan

**Prerequisite:** [Phase 1 — Foundation](ph_1_implementation.md) (Polymarket client, models, state, `test_connection.py`).

**Goal:** Define the scraper output format, build the market plugin system, compute fair values from sportsbook odds, generate trade signals with Kelly sizing, and run end-to-end in dry-run mode.

## Overview

Phase 2 adds three new subsystems on top of Phase 1's foundation:

1. **Scraper layer** (`scrapers/`) — defines the data contract for sportsbook odds, provides a CSV scraper for production (reads pre-normalized odds from an external scraper) and a stub scraper for testing
2. **Market plugin system** (`markets/`) — plugin interface + NHL Stanley Cup as the first concrete plugin, with auto-discovery of outcomes from Polymarket
3. **Signal engine** (`core/signal.py`) — compares fair values to Polymarket prices, computes Kelly bet sizes, emits trade signals

By the end of Phase 2, running `main.py --dry-run` will: load the CSV scraper → read normalized odds → feed them to the NHL plugin → compute fair values via vig removal → compare against live Polymarket prices → log signals with edge %, Kelly size, and trade direction. No orders placed.

## Files to Create/Modify

| # | File | Action | Purpose |
|---|------|--------|---------|
| 1 | `scrapers/__init__.py` | Create | Package marker |
| 2 | `scrapers/models.py` | Create | `BookOdds`, `EventOdds`, `ScrapedOdds` dataclasses |
| 3 | `scrapers/base.py` | Create | `BaseScraper` abstract class |
| 4 | `scrapers/csv_scraper.py` | Create | Reads normalized odds CSV, lowercases sportsbook names, produces `ScrapedOdds` |
| 5 | `scrapers/stub_scraper.py` | Create | Returns hardcoded NHL odds for testing (canonical names, no name mapping) |
| 6 | `markets/__init__.py` | Create | Package marker |
| 7 | `markets/base.py` | Create | `MarketPlugin` abstract class, `OutcomeFairValue`, `TradeParams` (with layered defaults) |
| 8 | `markets/nhl_stanley_cup/__init__.py` | Create | Package marker |
| 9 | `markets/nhl_stanley_cup/plugin.py` | Create | `NHLStanleyCupPlugin` — auto-discovers outcomes from Polymarket, no name mapping needed |
| 10 | `markets/nhl_stanley_cup/fair_value.py` | Create | Vig removal, weighted aggregation across sportsbooks |
| 11 | `markets/nhl_stanley_cup/config.yaml` | Create | Event slug, scraper event key, trade param overrides only |
| 12 | `core/signal.py` | Create | `evaluate_signals()`, `kelly_bet_size()`, `check_exits()` |
| 13 | `core/models.py` | Modify | Add `Signal` dataclass |
| 14 | `config.yaml` | Modify | Add `risk`, `trade_defaults`, `sportsbook_weight_defaults`, `scrapers`, `enabled_markets`, `engine.dry_run` |
| 15 | `main.py` | Modify | Add `--dry-run` flag, load scrapers + plugins, run signal pipeline |
| 15 | `tests/test_fair_value.py` | Create | Unit tests for vig removal and aggregation |
| 16 | `tests/test_signal.py` | Create | Unit tests for edge calculation and Kelly sizing |

---

## Step-by-Step Implementation

### Step 1: Scraper Data Models (`scrapers/models.py`)

Three dataclasses that define the universal data contract between scrapers and plugins.

```python
@dataclass
class BookOdds:
    sportsbook: str       # e.g., "bet365"
    decimal_odds: float   # e.g., 4.50

@dataclass
class EventOdds:
    event_name: str                            # e.g., "2026 NHL Stanley Cup Champion"
    outcomes: dict[str, list[BookOdds]]        # outcome_name → list of BookOdds

@dataclass
class ScrapedOdds:
    timestamp: datetime
    events: dict[str, EventOdds]               # event_name → EventOdds
```

**Key design decisions:**
- `BookOdds.sportsbook` must be **lowercase** (e.g., `"draftkings"`, not `"DraftKings"`). The CSV scraper lowercases on read. This string is used as a key in `sportsbook_weight_defaults` / `sportsbook_weights` config. A case mismatch silently falls back to weight 1.0.
- `decimal_odds` is always European decimal format. The external scraper converts from American/fractional before writing to CSV.
- `outcomes` keys are **canonical team names** matching Polymarket's outcome names (e.g., `"Toronto Maple Leafs"`). The external scraper normalizes sportsbook-native names before writing to CSV, so no name mapping is needed in the plugin.
- `events` keys come from the CSV `market` column (e.g., `"Stanley Cup Winner"`). Plugins match via their `scraper.event_key` config.

**Integration point:** Plugins receive `ScrapedOdds` from the engine and extract their slice using `scraper.event_key`. Since names are pre-normalized, the plugin just checks if a team exists on Polymarket — no name mapping needed.

### Step 2: Scraper Base Class (`scrapers/base.py`)

```python
class BaseScraper(ABC):
    def __init__(self, name: str, interval: int):
        self.name = name
        self.interval = interval  # seconds between runs

    @abstractmethod
    async def scrape(self) -> ScrapedOdds:
        """Run the scraper. Returns ScrapedOdds with all events this scraper covers."""

    def get_name(self) -> str:
        return self.name
```

**Why async:** Phase 3's engine will run each scraper as an `asyncio.create_task` loop. Making the interface async now avoids a breaking change later. The stub scraper's `scrape()` simply returns immediately.

### Step 3: CSV Scraper (`scrapers/csv_scraper.py`)

Reads normalized odds from a CSV file produced by an external scraper. CSV columns: `timestamp, sport, sportsbook, market, team, odds`.

```python
class CsvScraper(BaseScraper):
    def __init__(self, name: str, interval: int, path: str):
        super().__init__(name, interval)
        self.path = path

    async def scrape(self) -> ScrapedOdds:
        # Read CSV, lowercase sportsbook names, group by market column
        # Returns ScrapedOdds with events keyed by the "market" column value
```

**Key details:**
- Sportsbook names are **lowercased on read** to match `sportsbook_weight_defaults` keys in config
- The `market` column value (e.g., `"Stanley Cup Winner"`) becomes the event key in `ScrapedOdds.events`
- Team names are used as-is — the external scraper normalizes them to match Polymarket's canonical names
- Odds are read as decimal format (the external scraper converts from American/fractional before writing)

### Step 3b: Stub Scraper (`scrapers/stub_scraper.py`)

Returns hardcoded odds for pipeline testing without needing a real CSV file. Uses canonical team names and lowercase sportsbook names (matching the CSV scraper's output format). The event key is `"Stanley Cup Winner"` to match the plugin config.

### Step 4: Market Plugin Interface (`markets/base.py`)

Defines `MarketPlugin`, `OutcomeFairValue`, and `TradeParams`.

```python
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
```

**`TradeParams.from_config()`** follows the convention from CLAUDE.md: defaults must be loadable via a `from_config()` classmethod. It takes two dicts: `plugin_cfg` (the plugin's `trade_params` section) and `defaults` (the global `trade_defaults` section from root `config.yaml`). The plugin dict is merged on top of the global defaults — plugin values win, missing keys fall back to global defaults. This means you can set your baseline strategy once in `config.yaml` and only override specific values per-plugin.

### Step 5: NHL Stanley Cup Plugin Config (`markets/nhl_stanley_cup/config.yaml`)

```yaml
name: "2026 NHL Stanley Cup Champion"

polymarket:
  event_slug: "2026-nhl-stanley-cup-champion"
  neg_risk: true

# Outcomes are auto-discovered from Polymarket at startup.
# No need to list team names — the plugin queries the Gamma API for the event
# and maps all outcomes to token IDs automatically.

# Plugin-specific trade_params overrides. Keys here win over global
# trade_defaults in config.yaml. Keys not listed inherit the global default.
trade_params:
  max_outcome_exposure: 200

# Plugin-specific sportsbook weight overrides. Merged on top of global
# sportsbook_weight_defaults. Only list books where this market's weighting
# differs from the global baseline.
# sportsbook_weights:
#   pinnacle: 3.0  # example: if Pinnacle covered NHL futures

scraper:
  event_key: "Stanley Cup Winner"
```

**Auto-discovery strategy:** The plugin does NOT hardcode outcome names or token IDs. At startup:
1. Plugin loads its `event_slug` from config
2. Calls `PolymarketClient.get_event(slug)` to get `EventInfo` with all markets
3. Maps **every** market's `outcome_name` to its `yes_token_id`
4. At scrape time, `extract_odds()` intersects CSV team names with the auto-discovered Polymarket outcomes — only teams that exist on both sides are processed

This means adding a new team to Polymarket (or a sportsbook starting to cover a new team) requires zero config changes — the plugin picks it up automatically.

### Step 6: Fair Value Engine (`markets/nhl_stanley_cup/fair_value.py`)

Converts raw sportsbook odds into fair value probabilities via three steps:

**Step 6a: Implied Probability**
```
implied_prob = 1 / decimal_odds
```
Example: odds 4.50 → implied prob 22.2%

**Step 6b: Vig Removal (per sportsbook)**

For each sportsbook, sum the implied probs across ALL outcomes. This sum exceeds 100% — the excess is the vig (overround). Proportionally reduce each implied prob to sum to 100%.

```
overround = sum(implied_probs)  # e.g., 1.15
devigged_prob = implied_prob / overround
```

**Important:** Vig removal requires all outcomes from a single sportsbook in one batch. The method signature must accept the full outcome set per book, not individual outcomes.

**Step 6c: Weighted Aggregation (across sportsbooks)**

For each outcome, compute the weighted average of devigged probs across sportsbooks that contributed to **that outcome**, using the weights from `sportsbook_weights` config:

```
fair_value(outcome) = Σ(devigged_prob_i × weight_i) / Σ(weight_i)
                      for i in sportsbooks that have a line on this outcome
```

The denominator is the sum of weights for **contributing books only**, not all books in the event. If Book A covers outcomes 1-4 and Book B covers only 1-3, then outcome 4's fair value uses only Book A's weight in the denominator. Using all books with zeros for missing lines would collapse the fair value toward zero — this is the most important detail to get right in the implementation.

**Implementation structure:**

```python
class FairValueEngine:
    def __init__(self, sportsbook_weights: dict[str, float]):
        self.weights = sportsbook_weights

    def compute(self, mapped_odds: dict[str, list[BookOdds]]) -> dict[str, float]:
        """
        Args:
            mapped_odds: { canonical_outcome_name: [BookOdds, ...] }

        Returns:
            { canonical_outcome_name: fair_value_probability }
        """
        # 1. Group by sportsbook: { book: { outcome: decimal_odds } }
        by_book = {}
        for outcome, odds_list in mapped_odds.items():
            for bo in odds_list:
                by_book.setdefault(bo.sportsbook, {})[outcome] = bo.decimal_odds

        # 2. Devig each book: implied probs → divide by overround
        # devigged[book][outcome] = fair prob from that book's perspective
        devigged = {}
        for book, outcomes in by_book.items():
            implied = {o: 1.0 / odds for o, odds in outcomes.items()}
            overround = sum(implied.values())
            devigged[book] = {o: p / overround for o, p in implied.items()}

        # 3. Weighted average per outcome (contributing books only)
        fair_values = {}
        for outcome in mapped_odds:
            weighted_sum = 0.0
            weight_total = 0.0
            for book, probs in devigged.items():
                if outcome not in probs:
                    continue  # this book didn't cover this outcome
                w = self.weights.get(book, 1.0)
                weighted_sum += probs[outcome] * w
                weight_total += w
            if weight_total > 0:
                fair_values[outcome] = weighted_sum / weight_total

        # 4. Normalize so all fair values sum to 1.0
        total = sum(fair_values.values())
        if total > 0:
            fair_values = {o: p / total for o, p in fair_values.items()}

        return fair_values
```

**Edge case handling:**
- If a sportsbook covers only a subset of outcomes, its vig removal is computed on the subset only. This produces a less accurate devig, so outcomes with fewer sources naturally get fewer `sources_agreeing` and may not meet `min_sources`.
- If `sportsbook_weights` doesn't have a weight for a book, default to 1.0.
- If the final fair values don't sum to 1.0 across all outcomes (they won't after per-book devigging and weighting), normalize them. This ensures the fair values are coherent probabilities.
- **Known simplification:** Normalization runs over all outcomes in `mapped_odds`. With auto-discovery, all Polymarket outcomes have token IDs, so `compute_fair_values()` won't drop any. However, the sportsbook odds may cover teams not on Polymarket (or vice versa) — `extract_odds()` filters to the intersection, so fair values are normalized over the intersection set only.

### Step 7: NHL Stanley Cup Plugin (`markets/nhl_stanley_cup/plugin.py`)

Ties together config, fair value engine, and Polymarket token resolution.

```python
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
        self.token_map = {}  # { outcome_name: yes_token_id }
        self._resolve_tokens(client, plugin_config["polymarket"]["event_slug"])

    def _resolve_tokens(self, client, slug):
        """Query Gamma API and map all outcome names to token IDs."""
        event = client.get_event(slug)
        for market in event.markets:
            self.token_map[market.outcome_name] = market.yes_token_id
        logger.info(f"Auto-discovered {len(self.token_map)} outcomes for {self.name}")

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

        mapped = {}
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
```

**Simplified flow (name normalization handled by external scraper):**
1. CSV scraper reads pre-normalized odds with canonical team names and lowercased sportsbook names
2. `extract_odds()` intersects CSV team names with auto-discovered Polymarket outcomes
3. Teams not on Polymarket are silently skipped — no name mapping config needed

### Step 8: Signal Model (`core/models.py` modification)

Add `Signal` dataclass to existing models.py:

```python
@dataclass
class Signal:
    token_id: str
    outcome_name: str
    event_name: str
    side: str               # "BUY" or "SELL"
    edge: float             # relative edge (0.0 - 1.0)
    fair_value: float       # our computed probability
    market_price: float     # best_ask (BUY) or best_bid (SELL)
    size_usd: float         # bet size in USDC (from Kelly). Phase 3's executor
                            # converts to shares: shares = size_usd / market_price,
                            # then validates CLOB constraints (min $1 notional,
                            # tick precision on size * price).
    max_price: float | None = None  # for BUY: max price willing to pay
    min_price: float | None = None  # for SELL: min price willing to accept
    reason: str = "edge_detected"

    def to_dict(self) -> dict:
        return asdict(self)
```

**`size_usd` vs shares:** The signal carries USDC amount, not share count. The conversion `shares = size_usd / price` happens in Phase 3's executor, which also enforces CLOB constraints: minimum $1 notional (`shares * price >= 1.0`), tick size precision (max 2 decimal places on price, max 4 on `shares * price`). Phase 2 dry-run logs the USDC amount directly.

### Step 9: Signal Engine (`core/signal.py`)

Two public functions: `evaluate_signals()` and `kelly_bet_size()`. Plus `check_exits()` for sell logic.

**`kelly_bet_size()`:**
```python
def kelly_bet_size(
    fair_prob: float,
    market_price: float,
    bankroll: float,
    kelly_fraction: float = 0.25,
    min_bet: float = 5.0,
    max_bet: float = 50.0,
) -> float:
    if market_price <= 0 or market_price >= 1:
        return 0.0

    b = (1 / market_price) - 1       # net odds
    q = 1 - fair_prob
    kelly_pct = (fair_prob * b - q) / b

    if kelly_pct <= 0:
        return 0.0

    bet = bankroll * kelly_pct * kelly_fraction

    if bet < min_bet:
        return 0.0
    return min(bet, max_bet)
```

**`evaluate_signals()`:**
```python
def evaluate_signals(
    fair_values: list[OutcomeFairValue],
    polymarket_prices: dict[str, PriceInfo],
    trade_params: TradeParams,
    kelly_bankroll: float,
    event_name: str,
) -> list[Signal]:
    signals = []
    for fv in fair_values:
        pm = polymarket_prices.get(fv.token_id)
        if not pm or pm.best_ask is None or pm.best_bid is None:
            continue

        # Source count filter
        if fv.sources_agreeing < trade_params.min_sources:
            continue

        # Price range filter
        lo, hi = trade_params.price_range
        if pm.best_ask < lo or pm.best_ask > hi:
            continue

        # BUY edge: fair value > best ask
        if fv.fair_value > 0:
            buy_edge = (fv.fair_value - pm.best_ask) / fv.fair_value
        else:
            buy_edge = 0

        if buy_edge > trade_params.edge_threshold:
            bet_size = kelly_bet_size(
                fair_prob=fv.fair_value,
                market_price=pm.best_ask,
                bankroll=kelly_bankroll,
                kelly_fraction=trade_params.kelly_fraction,
                min_bet=trade_params.min_bet_size,
                max_bet=trade_params.max_bet_size,
            )
            if bet_size > 0:
                signals.append(Signal(
                    token_id=fv.token_id,
                    outcome_name=fv.outcome_name,
                    event_name=event_name,
                    side="BUY",
                    edge=buy_edge,
                    fair_value=fv.fair_value,
                    market_price=pm.best_ask,
                    size_usd=bet_size,
                    max_price=pm.best_ask,
                ))

        # SELL edge: best bid > fair value
        if fv.fair_value > 0:
            sell_edge = (pm.best_bid - fv.fair_value) / fv.fair_value
        else:
            sell_edge = 0

        if sell_edge > trade_params.edge_threshold:
            signals.append(Signal(
                token_id=fv.token_id,
                outcome_name=fv.outcome_name,
                event_name=event_name,
                side="SELL",
                edge=sell_edge,
                fair_value=fv.fair_value,
                market_price=pm.best_bid,
                size_usd=0,  # sell entire position — Phase 3's risk manager determines actual size
                min_price=fv.fair_value,
                reason="edge_disappeared",
            ))

    return signals
```

**`check_exits()`** is a thin wrapper that filters `evaluate_signals` output to SELL signals for tokens we actually hold. This is a convenience for Phase 3's engine; in Phase 2 dry-run mode, it's informational only.

### Step 10: Config Updates (`config.yaml`)

Add the following sections to the existing `config.yaml`. Note: `engine:` already exists with `default_order_type` — merge `dry_run` into the same mapping, don't duplicate the key.

```yaml
# Merge into existing engine: section
engine:
  default_order_type: "FOK"
  dry_run: false               # override with --dry-run CLI flag

# New sections:
risk:
  kelly_bankroll: 1000
  # Phase 3 risk manager limits (not read in Phase 2, listed here for forward-compat):
  max_event_exposure: 500      # USD per event — all outcomes combined
  max_portfolio_exposure: 1000 # USD total across all events
  min_balance: 50              # pause new trades below this USDC balance
  min_bankroll: 200            # emergency pause below this total bankroll
  # NOTE: per-outcome exposure lives in trade_defaults or per-plugin overrides,
  # NOT here. Don't duplicate it at the global level.

# Global defaults for trade params. Every plugin inherits these unless it
# overrides a specific key in its own trade_params section. This is the single
# place to tune your baseline strategy across all markets.
trade_defaults:
  edge_threshold: 0.10
  max_outcome_exposure: 300
  kelly_fraction: 0.25
  min_bet_size: 5
  max_bet_size: 50
  order_type: "FOK"
  min_sources: 3
  cooldown_minutes: 30
  price_range: [0.03, 0.95]

# Global defaults for sportsbook weights. Plugins inherit these and can
# override individual book weights in their own sportsbook_weights section.
# Higher weight = more influence on fair value. Sharp books should be higher.
sportsbook_weight_defaults:
  bet365: 2.0
  draftkings: 1.0
  fanduel: 1.0
  betmgm: 1.0
  betrivers: 1.0
  caesars: 1.0
  thescore: 1.0

scrapers:
  - name: csv
    interval: 60
    path: "data/normalized_odds.csv"

enabled_markets:
  - nhl_stanley_cup
```

`engine.dry_run` is the config-file equivalent of `--dry-run`. The CLI flag overrides it (if either is true, dry-run is active).

**Config authority — layered defaults + plugin overrides:**

Trade params and sportsbook weights use a two-layer merge: global defaults in `config.yaml` → plugin overrides in `markets/<name>/config.yaml`. Plugin values win; missing keys inherit the global default. This means you set your baseline strategy once globally and only override per-market where needed.

| Parameter | Where to set baseline | Where to override | Resolution |
|---|---|---|---|
| `edge_threshold`, `kelly_fraction`, `min/max_bet_size`, `min_sources`, `cooldown_minutes`, `price_range`, `max_outcome_exposure`, `order_type` | Global `trade_defaults.*` | Plugin `trade_params.*` | `{**trade_defaults, **plugin.trade_params}` |
| Sportsbook weights | Global `sportsbook_weight_defaults.*` | Plugin `sportsbook_weights.*` | `{**global_weights, **plugin_weights}` |
| `kelly_bankroll` | Global `risk.kelly_bankroll` | — | Account-wide, not per-market |
| `max_event_exposure`, `max_portfolio_exposure` | Global `risk.*` | — | Cross-market budget (Phase 3) |
| `min_balance`, `min_bankroll` | Global `risk.*` | — | Account-level safety (Phase 3) |

In Phase 2 dry-run, only `kelly_bankroll` (global) and the merged `trade_params` are used. The global risk limits are Phase 3 risk manager parameters — they exist in config now for forward-compatibility but aren't read yet.

### Step 11: Main Entry Point Update (`main.py`)

Add `--dry-run` argument parsing and the dry-run signal pipeline.

```python
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
    """Load enabled market plugins based on config.

    Passes the global config so plugins can merge trade_defaults and
    sportsbook_weight_defaults with their own overrides.
    """
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
                prices[fv.token_id] = client.get_prices(fv.token_id)

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
```

In Phase 2, `main.py` runs a **single** dry-run cycle (scrape once → evaluate → log). Phase 3 will replace this with the async engine loop that runs scrapers on their intervals.

### Step 12: Unit Tests

#### `tests/test_fair_value.py`

Test cases for `FairValueEngine.compute()`:

1. **Basic vig removal:** 3 outcomes from 1 sportsbook, implied probs sum to 115%. Verify devigged probs sum to 100% and each is proportionally reduced.
2. **Multi-book aggregation:** Same 3 outcomes, odds from 3 sportsbooks. Verify weighted average produces expected fair values.
3. **Unequal book weights:** Sharp book (weight 2.0) vs soft books (weight 1.0). Verify fair values are pulled toward the sharp book's devigged probs.
4. **Missing outcomes (partial book coverage):** 4 outcomes, 3 sportsbooks. Book A covers all 4, Book B covers only 3 (misses one team), Book C covers all 4. Verify: (a) per-book devig is computed on each book's own subset, (b) the missing team gets `sources_agreeing=2` not 3, (c) after global normalization, all 4 fair values still sum to 1.0, and (d) the missing team's fair value is still reasonable (not inflated or deflated by the normalization spreading mass).
5. **Normalization:** After aggregation, verify all fair values sum to 1.0 (or very close).
6. **Edge case — single book:** Only 1 sportsbook provides odds. Verify vig removal alone produces valid fair values.

#### `tests/test_signal.py`

Test cases for `kelly_bet_size()` and `evaluate_signals()`:

1. **Kelly positive edge:** fair_prob=0.20, market_price=0.18, bankroll=1000, kelly_fraction=0.25. Verify bet ≈ $6.00.
2. **Kelly zero edge:** fair_prob=0.20, market_price=0.20. Verify returns 0.
3. **Kelly negative edge:** fair_prob=0.18, market_price=0.20. Verify returns 0.
4. **Kelly min/max bounds:** Edge exists but Kelly suggests $3 (below $5 min). Verify returns 0. Edge exists, Kelly suggests $80 (above $50 max). Verify capped at $50.
5. **Signal generation — BUY:** fair_value=0.20, best_ask=0.18, threshold=0.10. Edge = 10% → meets threshold → signal emitted.
6. **Signal generation — no signal:** fair_value=0.20, best_ask=0.19, threshold=0.10. Edge = 5% → below threshold → no signal.
7. **Source filter:** Edge exists but only 2 sources (min_sources=3). Verify no signal.
8. **Price range filter:** Edge exists but best_ask=0.02 (below range [0.03, 0.95]). Verify no signal (entire outcome skipped). Same for best_ask=0.96 (above range).
9. **Missing side skips outcome:** best_ask is None or best_bid is None. Verify outcome is skipped entirely (no TypeError, no signal).

---

## Integration with Phase 1

### Phase 1 components used directly (no changes needed)

| Component | How Phase 2 Uses It |
|---|---|
| `PolymarketClient.get_event()` | Plugin startup: resolve event slug → token IDs |
| `PolymarketClient.get_prices()` | Fetch best bid/ask for each outcome during signal evaluation |
| `PriceInfo` dataclass | Returned by `get_prices()`, consumed by `evaluate_signals()` |
| `MarketInfo` dataclass | Used during token ID resolution (`.outcome_name`, `.yes_token_id`) |
| `EventInfo` dataclass | Used during token ID resolution (`.markets` list) |
| `StateManager` | Not used in Phase 2 dry-run; will be used in Phase 3 for position/cooldown persistence |
| `load_config()`, `setup_logging()` | Entry point setup, unchanged |

### Phase 1 components modified

| Component | Change |
|---|---|
| `core/models.py` | Add `Signal` dataclass |
| `config.yaml` | Add `risk`, `scrapers`, `enabled_markets`, `engine.dry_run` sections |
| `main.py` | Add `--dry-run` flag, plugin/scraper loading, dry-run cycle |

### New dependencies

None. All Phase 2 code uses Python stdlib + `pyyaml` (already installed). No new pip packages needed.

---

## Verification Checklist

### 1. Unit Tests Pass

```
.venv/Scripts/python.exe -m pytest tests/test_fair_value.py tests/test_signal.py -v
```

Verify:
- All vig removal tests pass (correct devigging, normalization)
- All Kelly sizing tests pass (edge cases, bounds)
- All signal generation tests pass (threshold, filters)

### 2. CSV Scraper → Fair Values (no Polymarket connection needed)

```python
# Quick check in a Python shell:
from scrapers.csv_scraper import CsvScraper
from markets.fair_value import FairValueEngine
import asyncio, yaml

csv_scraper = CsvScraper("csv", 60, "data/normalized_odds.csv")
odds = asyncio.run(csv_scraper.scrape())

# Load global config for sportsbook weights
with open("config.yaml") as f:
    cfg = yaml.safe_load(f)

engine = FairValueEngine(cfg.get("sportsbook_weight_defaults", {}))
# Extract odds for an event and pass to engine.compute()
event_odds = list(odds.events.values())[0]
result = engine.compute(event_odds.outcomes)
print(result, "sum:", sum(result.values()))
```

Verify: devigged fair values are sensible (sum ≈ 1.0, no negatives, no > 1.0).

Prerequisite: `data/normalized_odds.csv` must exist with columns: `timestamp, sport, sportsbook, market, team, odds`.

### 3. Full Dry Run (requires `.env` + Polymarket connection + CSV file)

```
.venv/Scripts/python.exe main.py --dry-run
```

Verify:
- Client initializes, exchange balance printed
- Plugin loads, token IDs resolved from Gamma API (log shows mapped outcomes)
- CSV scraper reads `data/normalized_odds.csv` without errors
- Fair values computed and logged per outcome
- Signals generated (or not) with edge %, Kelly size, direction
- No orders placed
- Any outcomes missing from Polymarket are logged as warnings

### 4. Manual Spreadsheet Cross-Check

Pick 2-3 outcomes from the CSV odds file. Manually compute:
1. Implied probs per sportsbook
2. Devigged probs per sportsbook
3. Weighted average across books
4. Compare to the bot's logged fair values — must match within rounding error

---

## What Phase 2 Does NOT Include

These are explicitly deferred to Phase 3:

- **Order execution** — no orders placed, signals are logged only
- **Risk manager** — no exposure/cooldown checks (signals are unfiltered except for source count and price range)
- **Position tracker** — no position tracking or P&L computation
- **Engine loop** — no continuous scraper scheduling; Phase 2 runs a single cycle
- **Real-time scrapers** — CSV scraper reads pre-normalized odds from file; live sportsbook scrapers are built independently
- **Telegram notifications** — no alerting
- **CSV trade log** — no trades to log (but the signal log format previews what Phase 3's trade log will look like)
- **WebSocket price feed** — Phase 2 uses REST `get_prices()` per token; Phase 3's engine will use the WebSocket cache for speed

---

## Post-Phase 2 Refactors

### Generalized market plugin structure (2026-04-04)

Per-market plugin folders (`markets/nhl_stanley_cup/`) replaced with a single generic `FuturesPlugin` class and flat config files:

- `markets/futures_plugin.py` — shared plugin class, all behavior driven by config
- `markets/fair_value.py` — shared devig + weighted averaging engine (was `nhl_stanley_cup/fair_value.py`)
- `markets/configs/<market_name>.yaml` — one yaml per market, referenced by name in `enabled_markets`

**Adding a new market** requires only a new yaml file in `markets/configs/`. No Python code needed. `main.py` auto-loads any market listed in `enabled_markets` by looking up `markets/configs/{name}.yaml`.

### Plugin type dispatch (2026-04-04)

Each market config has a `type` field (defaults to `futures`) that determines which plugin class handles it. `main.py` uses a `PLUGIN_TYPES` registry to map type strings to classes:

```yaml
# markets/configs/some_market.yaml
type: futures    # → FuturesPlugin
```

To add a new category of market (e.g., polling-based elections):
1. Create `markets/polling_plugin.py` implementing `MarketPlugin`
2. Register it in `_register_plugin_types()` in `main.py`: `PLUGIN_TYPES["polling"] = PollingPlugin`
3. Set `type: polling` in the market's config yaml
