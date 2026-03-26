# Poly-Bot: Automated Market-Taking Bot for Polymarket

## Executive Summary

An automated market-taking bot that identifies mispricings on Polymarket by comparing prediction market prices against fair values derived from sportsbook odds. When a Polymarket price diverges from fair value beyond a configurable threshold (covering fees + safety buffer), the bot places a directional bet on the mispriced side.

The architecture is **market-plugin based**: a core engine handles Polymarket interaction, execution, and risk management, while each market (e.g., "2026 NHL Stanley Cup Champion") is a self-contained plugin with its own data sources, fair value logic, and trade parameters. Adding a new market means adding a new plugin — no changes to the core.

### Terminology


| Term                                                                                                                                     | Meaning                                                                           | Example                         |
| ---------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- | ------------------------------- |
| **Event**                                                                                                                                | The overall market grouping on Polymarket                                         | "2026 NHL Stanley Cup Champion" |
| **Outcome**                                                                                                                              | A single team/option within an event — each outcome has its own YES/NO token pair | "Toronto Maple Leafs"           |
| **Token**                                                                                                                                | The specific YES or NO contract for an outcome                                    | Leafs YES token, Leafs NO token |
| **Token ID**                                                                                                                             | The unique identifier for a YES or NO token on the CLOB                           | `abc123`                        |
| Throughout this document, "market" is used loosely but generally refers to an **event**. When precision matters, we use the terms above. |                                                                                   |                                 |


---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Project Structure](#2-project-structure)
3. [Core Engine](#3-core-engine)
4. [Market Plugin System](#4-market-plugin-system)
5. [Data Pipeline](#5-data-pipeline)
6. [Signal Generation & Trade Decision](#6-signal-generation--trade-decision)
7. [Order Execution](#7-order-execution)
8. [Risk Management](#8-risk-management)
9. [Position & Portfolio Tracking](#9-position--portfolio-tracking)
10. [Configuration](#10-configuration)
11. [Logging & Monitoring](#11-logging--monitoring)
12. [What to Keep vs Discard from the Market-Maker Bot](#12-what-to-keep-vs-discard-from-the-market-maker-bot)
13. [Implementation Phases](#13-implementation-phases)
14. [Technical Decisions](#14-technical-decisions)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                           SCRAPERS                                   │
│  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐   │
│  │ Scraper A         │  │ Scraper B         │  │ Scraper C      │   │
│  │ (NHL, NBA)        │  │ (soccer)          │  │ (NFL)          │   │
│  │ every 60s         │  │ every 300s        │  │ every 120s     │   │
│  └────────┬──────────┘  └────────┬──────────┘  └───────┬────────┘   │
│           │                      │                     │            │
│  Independent async loops — each runs on its own interval            │
│  Results processed immediately on arrival, no coordination          │
└───────────┼──────────────────────┼─────────────────────┼────────────┘
            │                      │                     │
            ▼                      ▼                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         MARKET PLUGINS                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌─────────────────┐   │
│  │ NHL Stanley Cup  │  │ NHL Atlantic Div │  │  Future Market   │   │
│  │                  │  │                  │  │                  │   │
│  │ - Event Filter   │  │ - Event Filter   │  │ - Event Filter   │   │
│  │ - Name Mapping   │  │ - Name Mapping   │  │ - Name Mapping   │   │
│  │ - Fair Value Calc│  │ - Fair Value Calc│  │ - Fair Value Calc│   │
│  │ - Trade Params   │  │ - Trade Params   │  │ - Trade Params   │   │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
│           │                     │                      │            │
└───────────┼─────────────────────┼──────────────────────┼────────────┘
            │                     │                      │
            ▼                     ▼                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          CORE ENGINE                                │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │ Signal Engine │  │   Executor   │  │    Risk Manager          │  │
│  │              │  │              │  │                          │  │
│  │ Compare fair │  │ Place orders │  │ Outcome/event limits     │  │
│  │ value to mkt │  │ via CLOB API │  │ Portfolio exposure       │  │
│  │ price, emit  │  │ Track fills  │  │ Cooldowns                │  │
│  │ trade signal │  │ Handle fails │  │ Balance & bankroll       │  │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘  │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │  Polymarket  │  │  Position    │  │    State Manager         │  │
│  │  Client      │  │  Tracker     │  │                          │  │
│  │              │  │              │  │ Persist state across     │  │
│  │ API + WS     │  │ Positions,   │  │ restarts (JSON files)    │  │
│  │ connections  │  │ P&L, fills   │  │                          │  │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### Flow

1. **Each scraper runs as an independent async task** on its own interval (e.g., scraper A every 30s, scraper B every 5 minutes). Scrapers are fully decoupled from each other.
2. **When any scraper completes**, its `ScrapedOdds` is immediately processed — no waiting, no coordination with other scrapers.
3. **Each plugin** whose `event_key` appears in the scraper's results extracts its odds, maps names, and computes fair values
4. **The signal engine** compares each fair value to the current Polymarket price (from WebSocket or API)
5. When `|fair_value - market_price|` exceeds the edge threshold (fees + buffer), a **trade signal** is emitted
6. **Risk manager** gates the signal: checks position limits, portfolio exposure, cooldowns, balance thresholds
7. **Executor** places the order on Polymarket via the CLOB API and tracks its lifecycle

Scrapers are completely independent — each runs on its own schedule, and the latency from any individual scraper completing to a trade being placed is **under 1 second**.

---

## 2. Project Structure

```
poly-bot/
├── main.py                      # Entry point: starts core engine + all enabled plugins (supports --dry-run flag)
├── config.yaml                  # Global configuration (credentials, risk params, enabled markets)
│
├── core/                        # Core engine (market-agnostic)
│   ├── __init__.py
│   ├── engine.py                # Main loop: orchestrates plugins, signals, execution
│   ├── polymarket_client.py     # Polymarket API client (CLOB + Gamma + WebSocket)
│   ├── executor.py              # Order placement, cancellation, fill tracking
│   ├── signal.py                # Signal data class + signal evaluation logic
│   ├── risk_manager.py          # Position limits, exposure checks, cooldowns
│   ├── position_tracker.py      # Track positions, average cost, P&L per market
│   ├── state.py                 # Persist/load bot state to/from disk (JSON)
│   ├── models.py                # Data classes: Signal, Position, Order, MarketInfo, etc.
│   ├── notifier.py              # Telegram alert notifications
│   └── utils.py                 # Shared helpers (logging setup, retry logic, etc.)
│
├── scrapers/                    # Odds scrapers (each can cover one or many events)
│   ├── __init__.py
│   ├── base.py                  # Abstract scraper interface (BaseScraper)
│   ├── models.py                # ScrapedOdds, EventOdds, BookOdds data classes
│   ├── odds_aggregator.py       # Example: scrapes an aggregator site covering NHL, NBA, etc.
│
├── markets/                     # Market plugins (one subpackage per event)
│   ├── __init__.py
│   ├── base.py                  # Abstract base class: MarketPlugin interface
│   ├── nhl_stanley_cup/         # Example: first event to implement
│   │   ├── __init__.py
│   │   ├── plugin.py            # Implements MarketPlugin — filters ScrapedOdds, maps names
│   │   ├── fair_value.py        # Vig removal, aggregation, sharp-book weighting
│   │   └── config.yaml          # Event-specific params (outcome mappings, token IDs, thresholds)
│
├── data/                        # Runtime data (gitignored)
│   ├── state.json               # Persisted bot state (positions, cooldowns)
│   ├── trades.csv               # CSV trade summary log
│   └── logs/                    # Log files
│
├── tests/                       # Test suite
│   ├── test_fair_value.py       # Unit tests for vig removal, aggregation
│   ├── test_signal.py           # Unit tests for signal generation
│   ├── test_risk_manager.py     # Unit tests for risk gates
│   └── test_executor.py         # Unit tests for order logic
│
├── .env                         # Secrets (PK, BROWSER_ADDRESS, TELEGRAM_*) — gitignored
├── .env.example                 # Template for .env
├── requirements.txt             # Python dependencies
└── README.md                    # Setup and usage instructions
```

---

## 3. Core Engine

### 3.1 `engine.py` — Main Orchestrator

The engine runs a single async event loop that:

1. **Initializes** the Polymarket client (authenticates, derives API keys)
2. **Loads enabled scrapers and market plugins** from config
3. **Starts WebSocket connections** for real-time Polymarket prices
4. **Launches each scraper as an independent async task**, each running on its own interval. When a scraper completes:
  - Its `ScrapedOdds` is passed to relevant plugins → `extract_odds()` → `compute_fair_values()`
  - Signal engine compares fair values to Polymarket prices
  - Signals pass through `risk_manager.approve()` → approved signals go to `executor.execute()`
5. **Runs a background task** that periodically syncs positions and USDC balance from the API (every 60s) as a ground-truth check against local tracking

```python
# Pseudocode
async def run():
    client = PolymarketClient(config)
    scrapers = load_scrapers(config.enabled_scrapers)
    plugins = load_plugins(config.enabled_markets)
    risk_mgr = RiskManager(config.risk)
    executor = Executor(client)
    position_tracker = PositionTracker(client)

    # Start WebSocket for live Polymarket prices
    asyncio.create_task(client.connect_market_ws(all_token_ids(plugins)))
    async def process_scraper_result(scraped_odds: ScrapedOdds):
        """Called whenever a scraper completes — processes immediately."""
        for plugin in plugins:
            if plugin.event_key not in scraped_odds.events:
                continue  # this scraper doesn't cover this plugin's event

            event_odds = plugin.extract_odds(scraped_odds)
            fair_values = plugin.compute_fair_values(event_odds)
            polymarket_prices = client.get_prices(plugin.token_ids)

            signals = evaluate_signals(fair_values, polymarket_prices, plugin.config)

            for signal in signals:
                if risk_mgr.approve(signal, position_tracker):
                    if not config.dry_run:
                        await executor.execute(signal)
                    else:
                        logger.info(f"[DRY RUN] Would execute: {signal}")

    async def scraper_loop(scraper: BaseScraper):
        """Each scraper runs independently on its own interval."""
        while True:
            try:
                scraped_odds = await scraper.scrape()
                await process_scraper_result(scraped_odds)
            except Exception as e:
                logger.error(f"Scraper {scraper.get_name()} failed: {e}")
            await asyncio.sleep(scraper.interval)

    # Start each scraper as an independent async task
    for scraper in scrapers:
        asyncio.create_task(scraper_loop(scraper))
```

### 3.1.1 Dry-Run Mode

The bot supports a `--dry-run` flag that runs the full pipeline (fetch odds, compute fair values, generate signals, check risk gates) but **stops before placing any orders**. In dry-run mode:

- All signals are logged with full detail (edge, Kelly size, fair value, market price)
- Risk manager approvals/rejections are logged
- No orders are placed — the executor logs what *would* have been executed
- Position tracker is not updated (no phantom positions)
- CSV trade log is still written (for offline analysis of signal quality)

This is essential for validating the model and tuning thresholds before risking real money.

```python
# In engine.py
if not config.dry_run:
    await executor.execute(signal)
else:
    logger.info(f"[DRY RUN] Would execute: {signal}")
```

### 3.2 `polymarket_client.py` — API & WebSocket Client

**Keep from market-maker bot:**

- Authentication flow (PK + BROWSER_ADDRESS → ClobClient with signature_type=2)
- API key derivation
- WebSocket connection management (Market WS) with auto-reconnect
- On-chain contract approvals (`approveContracts()`)
- Position/balance queries

**Adapt:**

- Order placement: the market-maker always posts limit orders at computed prices. For market-taking, we need to support:
  - **FOK (Fill-or-Kill)**: attempt to fill entire order immediately or cancel — best for larger orders where partial fills aren't useful
  - **FAK (Fill-and-Kill)**: fill what's available immediately, cancel the rest — good for grabbing available liquidity
  - **GTC with aggressive price**: post a limit order at/above the best ask (to buy) or at/below the best bid (to sell) — functionally a market order that fills immediately if liquidity exists

**Key methods:**

```python
class PolymarketClient:
    def __init__(self, pk, browser_address, ...):
        # Initialize ClobClient, Web3, contract interfaces

    async def connect_market_ws(self, token_ids: list[str]):
        # Subscribe to order book updates for monitored tokens

    def get_prices(self, token_ids: list[str]) -> dict[str, PriceInfo]:
        # Return best bid, best ask, midpoint from local order book cache

    def get_book_depth(self, token_id: str, side: str, levels: int) -> list[PriceLevel]:
        # Walk the order book to find available liquidity at each price level

    async def place_order(self, token_id, side, size, price, order_type="FOK") -> OrderResult:
        # Place an order via CLOB API

    async def cancel_order(self, order_id: str) -> bool:
        # Cancel an open order

    def get_usdc_balance(self) -> float:
        # Query USDC ERC-20 balanceOf(wallet) on Polygon via web3
        # Returns balance in USD (raw / 1e6)

    def get_positions(self) -> dict[str, Position]:
        # Fetch current positions from data API
```

### 3.3 `executor.py` — Order Execution

Responsible for translating a trade signal into an actual order and tracking the result.

```python
class Executor:
    async def execute(self, signal: Signal) -> ExecutionResult:
        """
        1. Determine order parameters from signal:
           - token_id: which token to buy/sell
           - side: BUY or SELL
           - size: how many shares (from signal.size)
           - price: the limit price (signal.max_price for buys, signal.min_price for sells)
           - order_type: FOK for standard execution

        2. Check available liquidity at the target price:
           - Walk the order book to verify enough size exists
           - If insufficient liquidity, reduce order size or skip

        3. Place the order via client.place_order()

        4. Track result:
           - If filled: update position tracker, log execution
           - If partially filled (FAK): update with actual fill size
           - If rejected/failed: log reason, don't update positions

        5. Return ExecutionResult with fill details
        """
```

**Execution strategy for market-taking:**

Unlike the market-maker which posts passive limit orders and waits, a market-taker wants **immediate fills**. The approach:

1. **Check the order book depth** at the signal's price or better
2. **Size the order** to match available liquidity (don't try to buy 1000 shares if only 200 exist at the target price)
3. **Use FOK** for clean execution: either the full order fills at the target price or nothing happens
4. **Fall back to FAK** if partial fills are acceptable (configurable per market plugin)
5. **Never use GTC** for taking signals — stale unfilled orders sitting on the book would defeat the purpose and create risk

---

## 4. Market Plugin System

### 4.1 `base.py` — Plugin Interface

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class OutcomeFairValue:
    outcome_name: str       # e.g., "Toronto Maple Leafs"
    token_id: str           # Polymarket token ID for YES on this outcome
    fair_value: float       # Computed fair probability (0.0 - 1.0)
    sources_agreeing: int   # How many sportsbooks contributed to this estimate

class MarketPlugin(ABC):
    """
    Each market implements this interface.
    The core engine calls these methods whenever a scraper delivers new data.
    """

    @abstractmethod
    def get_name(self) -> str:
        """Human-readable market name, e.g. '2026 NHL Stanley Cup Champion'"""

    @abstractmethod
    def get_token_ids(self) -> list[str]:
        """All Polymarket token IDs this plugin monitors"""

    @abstractmethod
    def extract_odds(self, scraped_odds: ScrapedOdds) -> dict:
        """
        Filter the full ScrapedOdds to this plugin's event and map outcome names
        using the sportsbook_keys config. Returns the standardized internal format:
        { outcome_name: [(sportsbook, decimal_odds), ...] }
        """

    @abstractmethod
    def compute_fair_values(self, mapped_odds: dict) -> list[OutcomeFairValue]:
        """
        Transform mapped odds into fair value probabilities.
        Includes vig removal, aggregation, and weighting.
        """

    @abstractmethod
    def get_trade_params(self) -> TradeParams:
        """
        Return event-specific trading parameters:
        - edge_threshold: minimum relative edge required to trade (e.g., 0.10 = 10%)
        - max_outcome_exposure: max USDC exposure per outcome
        - kelly_fraction: fraction of full Kelly to use (e.g., 0.25 for quarter-Kelly)
        - order_type: FOK or FAK
        - min_sources: minimum sportsbooks required to generate a signal
        - neg_risk: whether this is a neg-risk event
        - tick_size: minimum price increment
        """
```

### 4.2 Example Plugin: `markets/nhl_stanley_cup/`

`**config.yaml**`

```yaml
name: "2026 NHL Stanley Cup Champion"
polymarket:
  event_slug: "2026-nhl-stanley-cup-champion"  # verify exact slug via Gamma API
  neg_risk: true
  # Token IDs populated at startup by querying the Gamma API with the slug
  # This avoids hardcoding IDs that could change

outcomes:
  # Maps team names to their sportsbook identifiers across books
  - name: "Toronto Maple Leafs"
    sportsbook_keys:
      betano: "Toronto Maple Leafs"
      bet365: "Toronto Maple Leafs"
      draftkings: "TOR Maple Leafs"
      fanduel: "Toronto Maple Leafs"
      betmgm: "Toronto Maple Leafs"
      caesars: "Toronto Maple Leafs"
  - name: "Florida Panthers"
    sportsbook_keys:
      # ... etc for each team
  # ... all teams

trade_params:
  edge_threshold: 0.10        # 10% relative edge required (e.g., fair value 20% → buy at 18c or better)
  max_outcome_exposure: 200   # max $200 per outcome
  kelly_fraction: 0.25        # use quarter-Kelly (conservative)
  min_bet_size: 5             # minimum bet in USDC (skip if Kelly suggests less)
  max_bet_size: 50            # cap per-trade size regardless of Kelly
  order_type: "FOK"
  min_sources: 3              # need at least 3 books agreeing
  cooldown_minutes: 30        # wait 30 min after a trade before re-evaluating same outcome
  price_range: [0.03, 0.95]   # only trade in this price range

scraper:
  event_key: "2026 NHL Stanley Cup Champion"  # must match key in ScrapedOdds.events
```

The plugin no longer has a `data_sources.py` — odds come from the shared scraper. The plugin's `extract_odds()` method uses the `outcomes` name mappings and `scraper.event_key` to pull its slice of the `ScrapedOdds` data.

> **Sportsbook data acquisition:** Custom scrapers fetch odds from sportsbooks. Each scraper can cover one or many events (e.g., one scraper might cover all NHL and NBA events from an aggregator site, while another covers soccer from a different source). All enabled scrapers run in parallel once per cycle, and their outputs are merged into a single `ScrapedOdds` shared across all plugins.

**Scraper output format:** The scraper returns a single `ScrapedOdds` object containing all events and all sportsbooks:

```python
@dataclass
class BookOdds:
    sportsbook: str              # e.g., "bet365"
    decimal_odds: float          # e.g., 4.50

@dataclass
class EventOdds:
    event_name: str              # e.g., "2026 NHL Stanley Cup Champion"
    outcomes: dict[str, list[BookOdds]]  # outcome_name → list of odds from different books

@dataclass
class ScrapedOdds:
    timestamp: datetime
    events: dict[str, EventOdds]  # event_name → EventOdds

# Example output from one scrape run:
# ScrapedOdds(
#     timestamp=datetime(2026, 3, 19, 14, 29, 50),
#     events={
#         "2026 NHL Stanley Cup Champion": EventOdds(
#             event_name="2026 NHL Stanley Cup Champion",
#             outcomes={
#                 "Toronto Maple Leafs": [
#                     BookOdds("bet365", 4.50),
#                     BookOdds("draftkings", 4.45),
#                     BookOdds("fanduel", 4.55),
#                 ],
#                 "Florida Panthers": [
#                     BookOdds("bet365", 3.00),
#                     BookOdds("draftkings", 3.10),
#                     ...
#                 ],
#             }
#         ),
#         "2026 NHL Atlantic Division": EventOdds(...),
#         "2026 NBA Champion": EventOdds(...),
#     }
# )
```

Each plugin's `extract_odds()` method pulls its event from `ScrapedOdds.events` using the `scraper.event_key` from its config, then maps outcome names using `sportsbook_keys`. This means:

- **Adding a new event** only requires a new plugin config — no scraper changes needed if the event is already scraped by an existing scraper
- **Adding a new sport/source** only requires a new scraper — its output gets merged automatically
- **Scrapers are fully decoupled** from plugins — build and test them independently

**Scraper base class:**

```python
class BaseScraper(ABC):
    interval: int  # seconds between scrape runs (configured per scraper)

    @abstractmethod
    async def scrape(self) -> ScrapedOdds:
        """Run the scraper. Returns ScrapedOdds with all events this scraper covers."""

    @abstractmethod
    def get_name(self) -> str:
        """Identifier for this scraper, e.g. 'odds_aggregator'"""
```

Each scraper's output is processed independently on arrival — no merging step. If two scrapers happen to cover the same event, each triggers its own evaluation cycle for that event's plugins (the later one simply re-evaluates with fresher data).

`**fair_value.py**`

```python
class NHLAtlanticFairValue:
    """
    Converts raw sportsbook odds into fair value probabilities.

    Steps:
    1. Convert decimal odds to implied probabilities: prob = 1 / odds
    2. Remove vig from each sportsbook's odds (all outcomes sum to >100%)
       Method: proportional reduction — divide each prob by the overround
    3. Aggregate across sportsbooks using a weighted average where each book
       has a configurable weight (e.g., sharp books like Bet365/Pinnacle get
       higher weight than softer books). Weights are defined in the plugin's
       config.yaml.
    """

    def compute(self, raw_odds: dict) -> list[OutcomeFairValue]:
        pass
```

**Vig removal example:**

```
Sportsbook has: Leafs +350 (4.50), Panthers +200 (3.00), Bruins +500 (6.00), ...
Implied probs:  22.2%,              33.3%,              16.7%, ... = sum 115% (15% vig)
Devigged:       19.3%,              29.0%,              14.5%, ... = sum 100%
```

### 4.3 Adding a New Event

To add a new event (e.g., "NHL Atlantic Division Winner"):

1. Create a new directory `markets/nhl_atlantic/` (use an existing plugin like `nhl_stanley_cup/` as a reference)
2. Fill in `config.yaml` with:
  - Polymarket event slug
  - `scraper.event_key` matching the key the scraper uses for this event
  - Outcome name mappings (sportsbook names → canonical names)
  - Trade parameters
3. Implement `fair_value.py` — may be identical to another event's if the vig removal logic is the same
4. Add the event to `config.yaml`'s `enabled_markets` list
5. Restart the bot

No scraper changes needed if the event is already being scraped. No `data_sources.py` needed — the plugin just filters from the shared `ScrapedOdds`.

---

## 5. Data Pipeline

### 5.1 External Data (Sportsbook Odds)

**Fetch cycle:**

```
Each scraper runs as an independent async loop:
  Every scraper.interval seconds:
    1. Scraper fetches odds for the events it covers
    2. Returns ScrapedOdds → immediately passed to relevant plugins
    3. Plugins filter to their event, map names, compute fair values
    4. Signals evaluated and trades executed — all within milliseconds
    5. Errors caught per-scraper (one failing doesn't affect others)
    6. Sleep for scraper.interval, then repeat
```

Scrapers are fully independent — each has its own interval and lifecycle. A fast-changing data source can poll every 30s while a slow one polls every 5 minutes.

**Data format standardization:**

- All odds stored as **decimal format** (European odds) in the ScrapedOdds output
- The scraper is responsible for converting from whatever format the source uses:
  - American odds (+350, -150): positive = (odds/100) + 1, negative = (100/|odds|) + 1
  - Fractional odds (7/2): (num/denom) + 1

**Staleness handling:**

- `ScrapedOdds.timestamp` tracks when the scrape ran
- If the scraper fails or returns no data for an event, plugins skip that cycle
- Individual sportsbook staleness is harder to detect with a single scraper — if the source doesn't update a book's odds, the scraper returns the same values. Consider logging when odds haven't changed across multiple cycles.

### 5.2 Polymarket Data

**Two sources, used together:**

1. **WebSocket (real-time):** Market WS provides live order book updates. The bot maintains a local order book cache (same `SortedDict` approach as the market-maker bot). This gives instant access to current best bid, best ask, and available liquidity at each price level.
2. **Gamma API (on-demand):** Used at startup to discover token IDs from event slugs, and periodically to cross-check prices. The `bestBid`/`bestAsk` fields from Gamma are the correct effective prices for neg-risk markets.

**Price used for signal generation:**

- For neg-risk markets (multi-outcome like division winners): use **Gamma API** `bestBid`/`bestAsk` as the primary price source. The CLOB raw orderbook for neg-risk markets shows misleading prices (~0.001 bids / ~0.999 asks) that don't reflect the effective market.
- For standard binary markets: use the **WebSocket-maintained local order book** for best bid/ask.
- In both cases, verify with the **CLOB order book** when executing to confirm liquidity exists at the target price.

### 5.3 Data Flow Diagram

```
          ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
          │  Scraper A   │  │  Scraper B   │  │  Scraper C   │
          │ (NHL, NBA)   │  │  (soccer)    │  │  (NFL)       │
          │ every 60s    │  │ every 300s   │  │ every 120s   │
          └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
                 │                 │                  │
          independent loops — each triggers pipeline on completion
                 │                 │                  │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
             Plugin A        Plugin B        Plugin C
          extract_odds()   extract_odds()   extract_odds()
          (filter event,   (filter event,   (filter event,
           map names)       map names)       map names)
                    │              │              │
                    ▼              ▼              ▼
             fair_value       fair_value      fair_value
             .compute()       .compute()      .compute()
                    │              │              │
                    ▼              ▼              ▼
             OutcomeFairValues (fair prob per outcome)
                    │
                    │         Polymarket Price
                    │         (from WS or Gamma)
                    │              │
                    ▼              ▼
                  Compare: edge > threshold?
                           │
                           ▼
                    Signal (BUY YES @ team X)
                           │
                           ▼
                     Risk Manager (approve?)
                           │
                           ▼
                      Executor (place FOK order)
```

---

## 6. Signal Generation & Trade Decision

### 6.1 Signal Evaluation

```python
def evaluate_signals(fair_values, polymarket_prices, trade_params) -> list[Signal]:
    signals = []
    for fv in fair_values:
        pm_price = polymarket_prices[fv.token_id]  # PriceInfo with best_bid, best_ask

        # Edge calculation: relative difference between fair value and market price
        # To BUY: we pay the best ask. Relative edge = (fair_value - best_ask) / fair_value
        buy_edge = (fv.fair_value - pm_price.best_ask) / fv.fair_value if fv.fair_value > 0 else 0
        # To SELL: we receive the best bid. Relative edge = (best_bid - fair_value) / fair_value
        sell_edge = (pm_price.best_bid - fv.fair_value) / fv.fair_value if fv.fair_value > 0 else 0

        if buy_edge > trade_params.edge_threshold:
            if fv.sources_agreeing >= trade_params.min_sources:
                bet_size = kelly_bet_size(
                    fair_prob=fv.fair_value,
                    market_price=pm_price.best_ask,
                    bankroll=trade_params.kelly_bankroll,
                    kelly_fraction=trade_params.kelly_fraction,
                    min_bet=trade_params.min_bet_size,
                    max_bet=trade_params.max_bet_size,
                )
                if bet_size > 0:
                    signals.append(Signal(
                        token_id=fv.token_id,
                        side="BUY",
                        edge=buy_edge,
                        fair_value=fv.fair_value,
                        market_price=pm_price.best_ask,
                        max_price=pm_price.best_ask,
                        size=bet_size,  # Kelly-computed USDC amount
                    ))

        elif sell_edge > trade_params.edge_threshold:
            # Only sell if we hold a position in this token
            if fv.sources_agreeing >= trade_params.min_sources:
                signals.append(Signal(
                    token_id=fv.token_id,
                    side="SELL",
                    edge=sell_edge,
                    fair_value=fv.fair_value,
                    market_price=pm_price.best_bid,
                    min_price=pm_price.best_bid,
                    size=None,  # sell entire position (or available liquidity)
                ))

    return signals
```

### 6.2 Edge Threshold Considerations

The edge threshold is a **relative** percentage — the market price must be at least this percentage below (for buys) or above (for sells) the fair value.

The threshold must cover:

1. **Taker fees** (if applicable): up to 0.44% for sports markets at 50% price, less at extremes
2. **Model uncertainty**: our fair value estimate isn't perfect
3. **Safety buffer**: additional margin for profitability

Starting recommendation: **10% relative edge threshold** (0.10). This is conservative — can be tightened as the model proves accurate.

Example: if our fair value for the Leafs is 20%, the threshold requires the market price to be at least 10% lower → buy at 18 cents or better. `(0.20 - 0.18) / 0.20 = 0.10` → meets threshold → BUY signal.

Another example: fair value 50% → buy at 45 cents or better. `(0.50 - 0.45) / 0.50 = 0.10` → meets threshold.

Note: relative edge means the absolute price gap required scales with the fair value. A 10% relative edge on a 5-cent outcome is only 0.5 cents, while on a 50-cent outcome it's 5 cents. This naturally adapts to the price level.

### 6.3 Bet Sizing: Kelly Criterion

Position sizes are computed using the **Kelly criterion**, which maximizes long-term bankroll growth by sizing bets proportionally to edge and probability.

**Full Kelly formula for a binary bet:**

```
Kelly % = (p * b - q) / b

where:
  p = fair probability of winning (our fair value estimate)
  q = 1 - p (probability of losing)
  b = net odds received = (1 / market_price) - 1
      (for a share priced at 0.19 that pays $1: b = 1/0.19 - 1 = 4.26)
```

**Fractional Kelly:** Full Kelly is aggressive and assumes perfect probability estimates. In practice, use a fraction (e.g., quarter-Kelly) to account for model uncertainty:

```python
def kelly_bet_size(fair_prob, market_price, bankroll, kelly_fraction=0.25,
                   min_bet=5, max_bet=50):
    """
    Compute bet size in USDC using fractional Kelly criterion.

    Args:
        fair_prob: our estimated true probability (0-1)
        market_price: price we'd pay per share on Polymarket (0-1)
        bankroll: fixed bankroll set in config (for Kelly sizing)
        kelly_fraction: fraction of full Kelly to use (0.25 = quarter-Kelly)
        min_bet: minimum bet size in USDC (skip if Kelly suggests less)
        max_bet: maximum bet size in USDC per trade

    Returns:
        Bet size in USDC, or 0 if Kelly says don't bet
    """
    if market_price <= 0 or market_price >= 1:
        return 0

    b = (1 / market_price) - 1  # net odds (fees ignored for now — revisit if edge is tight)
    q = 1 - fair_prob

    kelly_pct = (fair_prob * b - q) / b  # full Kelly fraction of bankroll

    if kelly_pct <= 0:
        return 0  # no edge — Kelly says don't bet

    bet = bankroll * kelly_pct * kelly_fraction

    if bet < min_bet:
        return 0  # too small to bother
    return min(bet, max_bet)
```

**Example:**

```
Fair value: 0.20 (20% chance)
Market ask: 0.18 ($0.18 per share)
Relative edge: (0.20 - 0.18) / 0.20 = 0.10 (10%) → meets threshold
Bankroll:   $1000
Net odds:   1/0.18 - 1 = 4.56

Full Kelly: (0.20 * 4.56 - 0.80) / 4.56 = 0.024 (2.4% of bankroll = $24)
Quarter-Kelly: $24 * 0.25 = $6.00
→ Bet $6.00 on this outcome
```

**Why fractional Kelly:**

- Full Kelly assumes our fair value estimates are perfectly calibrated — they won't be, especially early on
- Quarter-Kelly provides ~75% of the long-term growth rate of full Kelly but with far less variance
- Can increase the fraction (to half-Kelly, etc.) as the model proves accurate over time

**Bankroll definition:** `kelly_bankroll` is a fixed value set in config (e.g., $1000). Unlike a dynamic bankroll that fluctuates with balance and positions, this is a manually configured number that determines bet sizing. Adjust it up or down as you see fit based on your overall risk appetite — Kelly will scale bets proportionally.

### 6.4 Sell Strategy: Edge Disappearance

The exit strategy is simple: **sell when we can get a favorable price** (at or above fair value). Otherwise, hold to resolution.

**Sell condition (checked whenever a scraper delivers new data, for all open positions):**

- **Fair price available:** `polymarket_best_bid >= fair_value` AND sufficient liquidity at the bid → SELL entire position (FOK at best bid). The mispricing has corrected; take the profit.
- **Hold otherwise:** If the market bid is below fair value, continue holding. The position pays $1 if the outcome occurs, so holding to resolution is always an option. We entered the trade for a reason — no stop-loss, no panic selling.

```python
def check_exits(positions, fair_values, polymarket_prices, trade_params):
    """Check all open positions for exit conditions."""
    exit_signals = []
    for pos in positions:
        fv = fair_values.get(pos.token_id)
        pm = polymarket_prices.get(pos.token_id)
        if not fv or not pm:
            continue

        # Can we sell at or above fair value?
        if pm.best_bid >= fv.fair_value and pm.bid_liquidity >= pos.size:
            exit_signals.append(Signal(
                token_id=pos.token_id, side="SELL", edge=0,
                fair_value=fv.fair_value, market_price=pm.best_bid,
                min_price=fv.fair_value,  # don't sell below fair value
                size=None,  # sell entire position
                reason="edge_disappeared"
            ))

    return exit_signals
```

### 6.5 When NOT to Trade

Even with sufficient edge, skip if:

- Too few sportsbooks have data (< `min_sources`)
- Price is outside the configured `price_range` (avoid extremes near 0 or 1)
- Available liquidity at the target price is too thin (< Kelly bet size)
- The signal is for SELL but we hold no position
- Cooldown is active for this outcome (recently traded)

---

## 7. Order Execution

### 7.1 Order Types for Market-Taking


| Order Type              | When to Use                 | Behavior                                                                                                                    |
| ----------------------- | --------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| **FOK** (Fill-or-Kill)  | Default for clean execution | Entire order fills at target price or is cancelled. No partial fills, no stale orders.                                      |
| **FAK** (Fill-and-Kill) | When partial fills are OK   | Fills whatever is available at target price, cancels the rest. Use when liquidity is thin but any fill is better than none. |


**Never use GTC** for taking signals. A GTC order that doesn't immediately fill becomes a passive limit order sitting on the book — this turns us into a market maker, which is not the intent and creates adverse selection risk.

### 7.2 Execution Flow

```
Signal received (BUY YES token for team X at price ≤ 0.18, Kelly size = $6.00)
  │
  ├── 1. Check order book liquidity at 0.18 or better
  │      Walk asks from best ask upward, accumulate size
  │      Available: 200 shares at ≤ 0.18
  │
  ├── 2. Size the order: Kelly says $6.00 → 6.00 / 0.18 = ~33 shares
  │      200 available at price → can fill 33 shares.
  │
  ├── 3. Place FOK order:
  │      token_id=X, side=BUY, size=33, price=0.18
  │
  ├── 4a. If filled:
  │       - Append row to CSV trade log
  │       - Update position tracker: +33 shares @ avg 0.18
  │       - Log: "BUY 33 shares of Leafs YES @ 0.18 (edge: 10%, Kelly: $6.00)"
  │       - Start cooldown timer for this outcome
  │
  └── 4b. If rejected (no fill):
          - Log: "FOK rejected — insufficient liquidity"
          - No position change, no cooldown
```

### 7.3 Neg-Risk Market Execution

For neg-risk multi-outcome markets (like division winners), there's a nuance:

- Each outcome's YES token trades on its own CLOB order book
- The CLOB raw prices may not match Gamma effective prices
- **Use Gamma API bestAsk** to determine if the edge exists
- **Use CLOB order book** to find actual resting orders to match against
- The CLOB ask side (reversed) shows effective ask prices that match Gamma

When executing a buy on a neg-risk market:

1. Verify the edge using Gamma bestAsk
2. Walk CLOB asks (reversed) to find liquidity at that price level
3. Place the order at the CLOB ask price level where liquidity exists

---

## 8. Risk Management

### 8.1 Position Limits


| Limit                    | Scope                             | Default | Purpose                                                       |
| ------------------------ | --------------------------------- | ------- | ------------------------------------------------------------- |
| `max_outcome_exposure`   | Per outcome                       | $200    | Cap exposure to any single outcome (e.g., one team)           |
| `max_event_exposure`     | Per event (all outcomes combined) | $500    | Cap total exposure in one event (e.g., all Stanley Cup teams) |
| `max_portfolio_exposure` | All events                        | $1000   | Cap total bot exposure                                        |


### 8.2 Risk Gates (checked before every trade)

```python
class RiskManager:
    def approve(self, signal: Signal, tracker: PositionTracker) -> bool:
        # 1. Outcome limit: would this trade exceed max_outcome_exposure?
        current = tracker.get_position(signal.token_id)
        if current.size_usd + signal.size > self.max_outcome_exposure:
            return False  # or reduce size to fit

        # 2. Event exposure: total across all outcomes in this event
        event_exposure = tracker.get_event_exposure(signal.event_id)
        if event_exposure + signal.size > self.max_event_exposure:
            return False

        # 3. Portfolio exposure
        total_exposure = tracker.get_total_exposure()
        if total_exposure + signal.size > self.max_portfolio_exposure:
            return False

        # 4. Balance checks
        if tracker.get_usdc_balance() < self.min_balance:
            return False  # insufficient cash to trade
        if tracker.get_total_bankroll() < self.min_bankroll:
            return False  # emergency pause

        # 5. Cooldown: recently traded this outcome?
        if tracker.is_on_cooldown(signal.token_id):
            return False

        # 6. SELL signals only if we hold the position
        if signal.side == "SELL" and current.size <= 0:
            return False

        return True
```

---

## 9. Position & Portfolio Tracking

### 9.1 Position Tracker

```python
@dataclass
class Position:
    token_id: str
    outcome_name: str
    market_name: str
    side: str              # "YES" or "NO"
    size: float            # number of shares held
    avg_cost: float        # average entry price
    current_price: float   # latest mark-to-market price
    unrealized_pnl: float  # (current_price - avg_cost) * size
    realized_pnl: float    # from closed trades
    last_trade_time: datetime
```

**Tracking approach:**

- **Primary:** Local tracking updated on each FOK/FAK API response (immediate fill confirmation)
- **Secondary:** Periodic sync from Polymarket Data API (every 60s) as ground-truth backup
- **Reconciliation:** If API position differs from local by > 5%, log a warning and adopt API values

### 9.2 Balance & Bankroll Tracking

The bot tracks USDC balance and computes total portfolio value for monitoring and risk gate purposes. Kelly bet sizing uses a separate fixed `kelly_bankroll` from config (not the live balance).

```python
@dataclass
class BankrollSnapshot:
    usdc_balance: float        # USDC in wallet (on Polygon)
    positions_value: float     # mark-to-market value of all open positions
    total_bankroll: float      # usdc_balance + positions_value
    timestamp: datetime
```

**Balance fetching:**

- Query USDC balance on Polygon via `web3.py`: read the USDC ERC-20 contract's `balanceOf(wallet_address)`
- USDC on Polygon uses 6 decimals (`raw_balance / 1e6`)
- Fetched every polling cycle (alongside position sync) — lightweight RPC call

**Bankroll calculation:**

```
usdc_balance    = web3.call(USDC_contract.balanceOf(wallet))
positions_value = Σ (position.size × position.current_price) for all open positions
total_bankroll  = usdc_balance + positions_value
```

The `total_bankroll` is used for monitoring and risk gate checks (e.g., `min_bankroll` emergency pause). Kelly bet sizing uses the fixed `kelly_bankroll` from config instead.

**Balance logging:**

- Logged every cycle at DEBUG level: `Balance: $847.20 | Positions: $152.80 | Bankroll: $1000.00`
- Included in Telegram daily summary: `Bankroll: $1012.40 (+1.2%) | Cash: $860.20 | Positions: $152.20`
- Saved to state.json for persistence across restarts

**Alerts:**

- If `usdc_balance` drops below a configurable `min_balance` threshold (e.g., $50), send a Telegram alert and pause new trades (existing positions are kept)
- If `total_bankroll` drops below `min_bankroll` (e.g., $200), emergency alert — bot pauses all trading

### 9.3 P&L Calculation

```
Unrealized P&L = (current_price - avg_cost) × shares_held
Realized P&L   = Σ (sell_price - avg_cost) × shares_sold  [for each closed trade]
Total P&L      = Unrealized + Realized
```

### 9.4 State Persistence

Bot state is saved to `data/state.json` on every position change and loaded on startup:

```json
{
  "bankroll": {
    "usdc_balance": 847.20,
    "positions_value": 152.80,
    "total_bankroll": 1000.00,
    "last_updated": "2026-03-19T14:35:00Z"
  },
  "positions": {
    "token_abc123": {
      "size": 131,
      "avg_cost": 0.19,
      "realized_pnl": 0,
      "last_trade_time": "2026-03-19T14:30:00Z"
    }
  },
  "cooldowns": {
    "token_abc123": "2026-03-19T15:00:00Z"
  }
}
```

---

## 10. Configuration

### 10.1 `config.yaml` — Global Config

```yaml
# Polymarket credentials (override with .env)
polymarket:
  # PK and BROWSER_ADDRESS loaded from .env
  signature_type: 2  # Gnosis Safe proxy wallet
  chain_id: 137      # Polygon

# Polling and execution
engine:
  dry_run: false             # set to true (or use --dry-run flag) to log signals without executing
  position_sync_interval: 60 # seconds between API position syncs + balance checks
  ws_ping_interval: 5        # WebSocket keepalive interval
  # Note: scraper intervals are configured per-scraper (see scrapers section)

# Risk management defaults (can be overridden per market)
risk:
  kelly_bankroll: 1000         # USD — fixed bankroll for Kelly bet sizing (set manually)
  max_outcome_exposure: 200    # USD per outcome (e.g., one team)
  max_event_exposure: 500      # USD per event (all outcomes combined)
  max_portfolio_exposure: 1000 # USD total
  min_balance: 50              # USD — pause new trades if USDC balance drops below this
  min_bankroll: 200            # USD — emergency pause if total bankroll drops below this
  default_cooldown_minutes: 30

# Enabled scrapers (each runs independently on its own interval)
scrapers:
  - name: odds_aggregator        # covers NHL, NBA events
    interval: 60                  # scrape every 60 seconds
  # - name: soccer_scraper
  #   interval: 300               # scrape every 5 minutes

# Enabled markets (list of plugin directory names)
enabled_markets:
  - nhl_stanley_cup

# Logging
logging:
  level: INFO
  file: data/logs/bot.log
  console: true
  trade_log: data/logs/trades.log  # separate file for trade events only

# Telegram alerts
telegram:
  enabled: true
  # TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID loaded from .env
  send_trades: true            # alert on every trade execution
  send_errors: true            # alert on failures
  send_daily_summary: true     # P&L summary at midnight UTC
```

### 10.2 Environment Variables (`.env`)

```
PK=0x...                          # Private key of Safe owner EOA
BROWSER_ADDRESS=0x...             # Gnosis Safe contract address
TELEGRAM_BOT_TOKEN=...            # Telegram bot token from @BotFather
TELEGRAM_CHAT_ID=...              # Telegram chat ID for alerts
```

---

## 11. Logging & Monitoring

### 11.1 Log Levels


| Level       | What Gets Logged                                                                         |
| ----------- | ---------------------------------------------------------------------------------------- |
| **DEBUG**   | Raw sportsbook responses, order book snapshots, all price calculations                   |
| **INFO**    | Trade signals (generated, approved, rejected), order placements, fills, position changes |
| **WARNING** | Data source failures, stale data, position reconciliation mismatches, partial fills      |
| **ERROR**   | API errors, WebSocket disconnects, order rejections, authentication failures             |


### 11.2 Key Log Events

```
[INFO]  Signal: BUY Leafs YES | fair=0.200 ask=0.180 edge=10.0% sources=5 kelly=$6.00
[INFO]  Risk: APPROVED | pos=$0/$200 | event=$0/$500 | portfolio=$0/$1000
[INFO]  Order: FOK BUY 33 shares @ 0.18 | token=abc123
[INFO]  Fill: FILLED 33 shares @ 0.18 | cost=$5.94 | pos now 33 @ 0.18
[INFO]  P&L: Leafs YES | unrealized=$0.00 | realized=$0.00 | daily=$-5.94
[INFO]  Exit: SELL Leafs YES | reason=edge_disappeared | fair=0.19 bid=0.21 | +$1.94

[WARNING] Stale data: bet365 NHL Stanley Cup last updated 15m ago
[WARNING] Liquidity: only 50 shares available at 0.19, need 97

[ERROR] WebSocket disconnected, reconnecting in 5s...
[ERROR] Order rejected: insufficient balance
```

### 11.3 Trade Summary Log

A compact CSV log for quick P&L review:

```csv
timestamp,event,outcome,side,shares,price,usd,edge_pct,fair_value,kelly_usd,sources,fill,reason
2026-03-19T14:30:00Z,Stanley Cup,Leafs,BUY,33,0.18,5.94,10.0,0.200,6.00,5,FILLED,edge_detected
2026-03-20T09:15:00Z,Stanley Cup,Leafs,SELL,33,0.21,6.93,0.0,0.210,0,5,FILLED,edge_disappeared
```

### 11.4 Telegram Alerts

Real-time push notifications via a Telegram bot for critical events. The bot sends messages to a configured Telegram chat (personal or group).

**Setup:**

- Create a Telegram bot via @BotFather → get bot token
- Get your chat ID
- Add credentials to `.env`:
  ```
  TELEGRAM_BOT_TOKEN=...
  TELEGRAM_CHAT_ID=...
  ```

**Alert types:**


| Event                    | Priority | Message                                            |
| ------------------------ | -------- | -------------------------------------------------- |
| **Trade executed**       | Normal   | `BUY 97 Leafs YES @ 0.19                           |
| **Trade failed**         | High     | `FOK REJECTED: Leafs YES — insufficient liquidity` |
| **Daily P&L summary**    | Normal   | `Daily P&L: +$12.40 | 3 trades | 2 wins`           |
| **Exit trade**           | Normal   | `SELL 33 Leafs YES @ 0.21 — edge disappeared (+$0.99)` |
| **Data source down**     | Medium   | `WARNING: bet365 NHL odds stale for 30+ min`       |
| **WebSocket disconnect** | Medium   | `WS disconnected — reconnecting...`                |
| **Bot startup/shutdown** | Normal   | `Bot started (dry-run: false)                      |


**Implementation:** A lightweight `TelegramNotifier` class using the Telegram Bot API (simple HTTP POST, no heavy SDK needed):

```python
class TelegramNotifier:
    async def send(self, message: str, priority: str = "normal"):
        """Send a message via Telegram Bot API."""
        # POST to https://api.telegram.org/bot{token}/sendMessage
        # High priority messages use parse_mode=HTML for bold formatting
```

Alerts are **non-blocking** — if Telegram is unreachable, log the failure and continue trading. Never let notification failures affect the trading loop.

---

## 12. What to Keep vs Discard from the Market-Maker Bot

### Keep (adapt for taker)


| Component                                                  | Why                                   | Adaptation                                                                                                              |
| ---------------------------------------------------------- | ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| **Authentication** (PK, BROWSER_ADDRESS, signature_type=2) | Required for any Polymarket trading   | Direct reuse                                                                                                            |
| **ClobClient initialization**                              | API key derivation, HTTP client setup | Direct reuse                                                                                                            |
| **WebSocket connections**                                  | Real-time price data                  | Keep Market WS for live order book                                                                                      |
| **Order book storage** (SortedDict)                        | Need to know available liquidity      | Keep, but read-only (we don't post passive orders)                                                                      |
| **Position tracking** (local + API sync)                   | Must know what we hold                | Simplify — FOK/FAK API responses give immediate fill confirmation; periodic API sync (every 60s) as ground-truth backup |
| **On-chain approvals**                                     | Required before first trade           | Direct reuse                                                                                                            |
| **Auto-reconnect for WebSockets**                          | Must stay connected 24/7              | Direct reuse                                                                                                            |


### Discard


| Component                                                                          | Why                                                                                                                                                                                                    |
| ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Google Sheets for config**                                                       | Overkill for a taker bot — use YAML config files instead. Sheets made sense for the maker because a human was constantly adjusting markets and hyperparameters. The taker bot's config changes rarely. |
| **Market discovery / reward calculation** (`update_markets.py`, `find_markets.py`) | We don't earn maker rewards. Market selection is manual (config-driven), not algorithmic.                                                                                                              |
| **Spread management / order pricing at inside of spread**                          | We don't post passive orders. We take existing liquidity.                                                                                                                                              |
| **Dual-sided order management** (maintain bid AND ask)                             | We make directional bets, not market-making spreads.                                                                                                                                                   |
| **Order replacement optimization** (only cancel if >0.5c / >10% change)            | We don't maintain standing orders. Each trade is one-shot.                                                                                                                                             |
| **Volatility calculation and gating**                                              | Volatility matters for market makers who risk getting picked off. As takers, we only trade when we see edge — volatility is handled implicitly by the edge threshold.                                  |
| **Stats updater** (`update_stats.py`)                                              | Replace with simple logging + Telegram alerts. No need for a separate process writing to Sheets.                                                                                                       |
| **Sentiment ratio** (bid/ask volume comparison)                                    | Market-making signal, not relevant for directional taking.                                                                                                                                             |
| **Price sanity check vs sheet reference**                                          | Replace with multi-source agreement (`min_sources`).                                                                                                                                                   |
| **Sleep period / cooldown after stop-loss** (file-based)                           | No stop-loss. Cooldown after trades uses in-memory tracker (persisted to state.json).                                                                                                                   |
| `**performing` set**                                                               | Needed for the maker's optimistic updates during rapid order cycling. The taker executes FOK orders and gets an immediate result — no in-flight ambiguity.                                             |
| **Multiplier for cheap tokens**                                                    | Maker-specific sizing heuristic. Taker sizing is driven by edge and risk limits.                                                                                                                       |


### Transform


| Market-Maker Concept                                  | Market-Taker Equivalent                                      |
| ----------------------------------------------------- | ------------------------------------------------------------ |
| Continuously quote both sides                         | Place one-shot directional orders when edge exists           |
| React to every order book tick                        | Poll on a 30-60s cycle (futures odds change slowly)          |
| Earn the spread + maker rewards                       | Earn from mispricing edge (fair value vs market price)       |
| Manage inventory risk (building too much of one side) | Manage directional risk (position limits, hold to resolution) |
| Price based on order book (inside the spread)         | Price based on external data (sportsbook-derived fair value) |
| GTC limit orders (passive, wait for fill)             | FOK/FAK orders (aggressive, immediate fill or cancel)        |


---

## 13. Implementation Phases

### Phase 1: Foundation (Polymarket Client + Data Models)

**Goal:** Connect to Polymarket, authenticate, read prices, and place a test order.

1. Set up project structure and dependencies
2. Implement `core/models.py`: data classes (Signal, Position, Order, PriceInfo, etc.)
3. Implement `core/polymarket_client.py`:
  - Authentication (PK, BROWSER_ADDRESS, ClobClient)
  - Gamma API: fetch event by slug → get token IDs, prices
  - CLOB API: fetch order book, place order, cancel order
  - `get_usdc_balance()`: query USDC balance on Polygon via web3
  - Market WebSocket: connect to Market WS, receive price updates
4. Implement `core/state.py`: save/load state to JSON
5. **Test:** manually place a small FOK order on a test market to verify the full auth + execution flow works

### Phase 2: Scraper Interface + First Market Plugin (NHL Stanley Cup)

**Goal:** Define scraper output format, compute fair values, generate signals.

1. Implement `scrapers/models.py`: `ScrapedOdds`, `EventOdds`, `BookOdds` data classes
2. Implement `scrapers/base.py`: abstract scraper interface (`BaseScraper`)
3. Implement a **stub scraper** that returns hardcoded/mock `ScrapedOdds` for testing (real scrapers built separately)
4. Implement `markets/base.py` (plugin interface with `extract_odds()`)
5. Implement `markets/nhl_stanley_cup/`:
  - `fair_value.py`: vig removal, aggregation, configurable book weighting
  - `plugin.py`: `extract_odds()` filtering + name mapping, ties everything together
  - `config.yaml`: outcome mappings, event key, trade params
6. Implement `core/signal.py`: signal evaluation (relative edge) + Kelly criterion bet sizing
7. Implement `--dry-run` flag (CLI argument + config option)
8. **Test:** verify fair value calculations against manual spreadsheet. Verify Kelly sizing produces sensible bet sizes across a range of edges/prices. Run signal generation in dry-run mode (log signals but don't execute).

### Phase 3: Execution, Risk & Monitoring

**Goal:** Execute real trades with proper risk controls, logging, and alerts.

1. Implement `core/executor.py`: order placement with FOK (FAK fallback), liquidity checks
2. Implement `core/risk_manager.py`: all risk gates (exposure limits, balance checks, cooldowns)
3. Implement `core/position_tracker.py`: position tracking, P&L, balance/bankroll monitoring
4. Implement exit logic: sell when market bid reaches fair value (edge disappeared)
5. Implement CSV trade logging
6. Implement `core/notifier.py`: Telegram alerts (trades, errors, daily summary)
7. Implement `core/engine.py`: independent scraper loops (`asyncio.create_task` per scraper), each triggering the signal → risk → execute pipeline on completion
8. **Test:** run with very small position sizes ($5-10) on the live market. Monitor fills, P&L, and signal accuracy via CSV log and Telegram alerts.

### Phase 4: Hardening

**Goal:** Production-ready with robust error handling.

1. Graceful shutdown (save state on SIGINT)
2. Error recovery (API failures, WebSocket disconnects, scraper failures)
3. Auto-reconnect for WebSocket with backoff
4. **Validate:** run for 2+ weeks, analyze trade logs for:
  - Signal accuracy (% of trades that end profitable)
  - Edge decay (does edge disappear by the time we execute?)
  - P&L after fees
  - Data source reliability

### Phase 5: Expand to More Events

**Goal:** Add more events using the plugin system.

1. Create plugins for additional events (other NHL divisions, conferences) — no scraper changes needed if already scraped
2. Potentially expand to other sports or non-sports events
3. Refine edge thresholds and position sizing based on Phase 4 learnings

---

## 14. Technical Decisions

### Language & Runtime

- **Python 3.11+** — async/await for WebSockets, rich ecosystem for data work
- `**uv`** for package management (fast, reliable)

### Key Dependencies


| Package            | Purpose                                                                 |
| ------------------ | ----------------------------------------------------------------------- |
| `py-clob-client`   | Polymarket CLOB API client (order signing, placement)                   |
| `web3`             | Polygon RPC for on-chain operations (balance checks, approvals)         |
| `websockets`       | Real-time market data stream                                            |
| `curl_cffi`        | HTTP requests with browser TLS fingerprinting (for sportsbook scraping) |
| `pyyaml`           | Configuration file parsing                                              |
| `sortedcontainers` | Efficient order book storage (SortedDict)                               |
| `python-dotenv`    | Load .env file                                                          |


### Why Not Google Sheets?

The market-maker uses Sheets because a human operator frequently adjusts which markets to trade and tweaks hyperparameters. A market-taking bot's config changes rarely — new markets are added occasionally, and thresholds are tuned after analysis, not in real-time. YAML files are simpler, version-controlled, and don't require Google API credentials.

### Why Polling Instead of Pure WebSocket?

Sportsbook odds for futures markets change a few times per day, not sub-second. Polling on scraper-specific intervals (30s to 5min depending on the source) is more than sufficient. Each scraper runs independently on its own schedule, and the downstream pipeline (fair value computation, signal evaluation, order placement) completes in under 1 second. The Polymarket side uses WebSockets for real-time price data, but signal generation is driven by the slower sportsbook polling cadence.

### Order Type Choice: FOK over GTC

A GTC order that doesn't immediately fill sits on the order book as a passive limit order. This is problematic because:

1. The fair value may change while the order sits
2. We become a de facto market maker, exposed to adverse selection
3. We'd need to manage order lifecycle (cancel stale orders, etc.)

FOK eliminates all of this: fill now or don't. If the price we want isn't available, we simply wait for the next polling cycle.