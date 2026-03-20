# Poly-Bot: Automated Market-Taking Bot for Polymarket

## Executive Summary

An automated market-taking bot that identifies mispricings on Polymarket by comparing prediction market prices against fair values derived from sportsbook odds. When a Polymarket price diverges from fair value beyond a configurable threshold (covering fees + safety buffer), the bot places a directional bet on the mispriced side.

The architecture is **market-plugin based**: a core engine handles Polymarket interaction, execution, and risk management, while each market (e.g., "2026 NHL Stanley Cup Champion") is a self-contained plugin with its own data sources, fair value logic, and trade parameters. Adding a new market means adding a new plugin — no changes to the core.

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
│                         MARKET PLUGINS                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌─────────────────┐   │
│  │ NHL Stanley Cup  │  │ NHL Atlantic Div │  │  Future Market   │   │
│  │                  │  │                  │  │                  │   │
│  │ - Data Sources   │  │ - Data Sources   │  │ - Data Sources   │   │
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
│  │ Compare fair │  │ Place orders │  │ Position limits          │  │
│  │ value to mkt │  │ via CLOB API │  │ Portfolio exposure       │  │
│  │ price, emit  │  │ Track fills  │  │ Cooldowns                │  │
│  │ trade signal │  │ Handle fails │  │ Max loss per market/day  │  │
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

1. **Each market plugin** periodically fetches external data (sportsbook odds) and computes a fair value for each outcome
2. **The signal engine** compares each fair value to the current Polymarket price (from WebSocket or API)
3. When `|fair_value - market_price|` exceeds the edge threshold (fees + buffer), a **trade signal** is emitted
4. **Risk manager** gates the signal: checks position limits, portfolio exposure, cooldowns, daily loss limits
5. **Executor** places the order on Polymarket via the CLOB API and tracks its lifecycle

---

## 2. Project Structure

```
poly-bot/
├── main.py                      # Entry point: starts core engine + all enabled plugins
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
│   └── utils.py                 # Shared helpers (logging setup, retry logic, etc.)
│
├── markets/                     # Market plugins (one subpackage per market)
│   ├── __init__.py
│   ├── base.py                  # Abstract base class: MarketPlugin interface
│   ├── nhl_stanley_cup/            # Example: first market to implement
│   │   ├── __init__.py
│   │   ├── plugin.py            # Implements MarketPlugin — ties everything together
│   │   ├── data_sources.py      # Fetch sportsbook odds for this market
│   │   ├── fair_value.py        # Vig removal, aggregation, sharp-book weighting
│   │   └── config.yaml          # Market-specific params (teams, token IDs, thresholds)
│   └── _template/               # Copy this to create a new market plugin
│       ├── __init__.py
│       ├── plugin.py
│       ├── data_sources.py
│       ├── fair_value.py
│       └── config.yaml
│
├── data/                        # Runtime data (gitignored)
│   ├── state.json               # Persisted bot state (positions, cooldowns)
│   └── logs/                    # Log files
│
├── tests/                       # Test suite
│   ├── test_fair_value.py       # Unit tests for vig removal, aggregation
│   ├── test_signal.py           # Unit tests for signal generation
│   ├── test_risk_manager.py     # Unit tests for risk gates
│   └── test_executor.py         # Unit tests for order logic
│
├── .env                         # Secrets (PK, BROWSER_ADDRESS) — gitignored
├── .env.example                 # Template for .env
├── requirements.txt             # Python dependencies
└── README.md                    # Setup and usage instructions
```

---

## 3. Core Engine

### 3.1 `engine.py` — Main Orchestrator

The engine runs a single async event loop that:

1. **Initializes** the Polymarket client (authenticates, derives API keys)
2. **Loads enabled market plugins** from config
3. **Starts WebSocket connections** for real-time Polymarket prices
4. **Runs a polling loop** (one iteration every 30–60 seconds):
   - Calls each plugin's `fetch_data()` → gets latest sportsbook odds
   - Calls each plugin's `compute_fair_values()` → gets fair probability per outcome
   - Calls `signal_engine.evaluate()` → compares fair values to Polymarket prices
   - For each trade signal, passes through `risk_manager.check()` → approve or reject
   - Approved signals go to `executor.execute()` → places the order
5. **Runs a background task** that periodically syncs positions from the API (every 60s) as a ground-truth check against local tracking

```python
# Pseudocode
async def run():
    client = PolymarketClient(config)
    plugins = load_plugins(config.enabled_markets)
    risk_mgr = RiskManager(config.risk)
    executor = Executor(client)
    position_tracker = PositionTracker(client)

    # Start WebSocket for live Polymarket prices
    asyncio.create_task(client.connect_market_ws(all_token_ids(plugins)))
    asyncio.create_task(client.connect_user_ws())  # track own fills

    while True:
        for plugin in plugins:
            external_data = await plugin.fetch_data()
            fair_values = plugin.compute_fair_values(external_data)
            polymarket_prices = client.get_prices(plugin.token_ids)

            signals = evaluate_signals(fair_values, polymarket_prices, plugin.config)

            for signal in signals:
                if risk_mgr.approve(signal, position_tracker):
                    await executor.execute(signal)

        await asyncio.sleep(config.poll_interval)  # 30-60s
```

### 3.2 `polymarket_client.py` — API & WebSocket Client

**Keep from market-maker bot:**
- Authentication flow (PK + BROWSER_ADDRESS → ClobClient with signature_type=2)
- API key derivation
- WebSocket connection management (Market WS + User WS) with auto-reconnect
- On-chain contract approvals (`approveContracts()`)
- Position merging (YES + NO → USDC recovery)
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

    async def connect_user_ws(self):
        # Authenticated — receive fill/order events for own account

    def get_prices(self, token_ids: list[str]) -> dict[str, PriceInfo]:
        # Return best bid, best ask, midpoint from local order book cache

    def get_book_depth(self, token_id: str, side: str, levels: int) -> list[PriceLevel]:
        # Walk the order book to find available liquidity at each price level

    async def place_order(self, token_id, side, size, price, order_type="FOK") -> OrderResult:
        # Place an order via CLOB API

    async def cancel_order(self, order_id: str) -> bool:
        # Cancel an open order

    def get_positions(self) -> dict[str, Position]:
        # Fetch current positions from data API

    async def merge_positions(self, condition_id, amount, neg_risk) -> bool:
        # Merge YES+NO tokens back to USDC (via Node.js subprocess)
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
    confidence: float       # 0.0 - 1.0, how confident we are in this estimate
    sources_agreeing: int   # How many sportsbooks contributed to this estimate

class MarketPlugin(ABC):
    """
    Each market implements this interface.
    The core engine calls these methods in order during each polling cycle.
    """

    @abstractmethod
    def get_name(self) -> str:
        """Human-readable market name, e.g. '2026 NHL Stanley Cup Champion'"""

    @abstractmethod
    def get_token_ids(self) -> list[str]:
        """All Polymarket token IDs this plugin monitors"""

    @abstractmethod
    def get_condition_ids(self) -> list[str]:
        """Condition IDs for position merging"""

    @abstractmethod
    async def fetch_data(self) -> dict:
        """
        Fetch external data (sportsbook odds, etc.).
        Returns raw data dict — structure is plugin-defined.
        """

    @abstractmethod
    def compute_fair_values(self, raw_data: dict) -> list[OutcomeFairValue]:
        """
        Transform raw external data into fair value probabilities.
        Includes vig removal, aggregation, and weighting.
        """

    @abstractmethod
    def get_trade_params(self) -> TradeParams:
        """
        Return market-specific trading parameters:
        - edge_threshold: minimum edge required to trade (e.g., 0.05)
        - max_position_size: max USDC exposure per outcome
        - kelly_fraction: fraction of full Kelly to use (e.g., 0.25 for quarter-Kelly)
        - order_type: FOK or FAK
        - min_sources: minimum sportsbooks required to generate a signal
        - neg_risk: whether this is a neg-risk market
        - tick_size: minimum price increment
        """
```

### 4.2 Example Plugin: `markets/nhl_stanley_cup/`

**`config.yaml`**
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
  edge_threshold: 0.05        # 5% edge required to trade
  max_position_size: 100      # max $100 per outcome
  kelly_fraction: 0.25        # use quarter-Kelly (conservative)
  min_bet_size: 5             # minimum bet in USDC (skip if Kelly suggests less)
  max_bet_size: 50            # cap per-trade size regardless of Kelly
  order_type: "FOK"
  min_sources: 3              # need at least 3 books agreeing
  min_confidence: 0.7         # minimum confidence score
  cooldown_minutes: 30        # wait 30 min after a trade before re-evaluating same outcome
  price_range: [0.03, 0.95]   # only trade in this price range

data_sources:
  poll_interval: 60           # fetch sportsbook odds every 60 seconds
  sportsbooks:
    - betano
    - bet365
    - draftkings
    - fanduel
    - betmgm
    - caesars
```

**`data_sources.py`**
```python
class NHLAtlanticDataSource:
    """
    Fetches odds for 2026 NHL Stanley Cup Champion from multiple sportsbooks.

    Input: sportsbook websites/APIs
    Output: dict mapping team_name -> list of (sportsbook, decimal_odds)

    Format:
    {
        "Toronto Maple Leafs": [
            ("betano", 3.50),
            ("bet365", 3.40),
            ("draftkings", 3.45),
            ("fanduel", 3.55),
            ...
        ],
        "Florida Panthers": [...],
        ...
    }
    """

    async def fetch(self) -> dict:
        # Fetch from each configured sportsbook in parallel
        # Each sportsbook fetcher returns odds for all teams
        # Merge results by team name using the sportsbook_keys mapping
        pass
```

> **Note on sportsbook data acquisition:** The data source is TBD — either an existing sportsbook scraper or a third-party service like The Odds API. The plugin interface is designed to be agnostic: regardless of how odds are fetched, the `data_sources.py` module must return a standardized dict of `team → [(sportsbook, decimal_odds), ...]`. This means swapping from scraper to API (or vice versa) only requires changing the internals of `data_sources.py`, not any other part of the system.

**`fair_value.py`**
```python
class NHLAtlanticFairValue:
    """
    Converts raw sportsbook odds into fair value probabilities.

    Steps:
    1. Convert decimal odds to implied probabilities: prob = 1 / odds
    2. Remove vig from each sportsbook's odds (all outcomes sum to >100%)
       Method: proportional reduction — divide each prob by the overround
    3. Aggregate across sportsbooks:
       - Option A: Simple average of devigged probabilities
       - Option B: Sharp-book weighted average (Bet365/Pinnacle get higher weight)
       - Option C: Median (robust to outlier books)
       Start with Option B.
    4. Compute confidence score based on:
       - Number of contributing sportsbooks
       - Agreement between books (low variance = high confidence)
       - Staleness (if a book's line hasn't moved in hours, lower its weight)
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

### 4.3 Adding a New Market

To add a new market (e.g., "NHL Atlantic Division Winner"):

1. Copy `markets/_template/` to `markets/nhl_atlantic/`
2. Fill in `config.yaml` with the Polymarket event slug, outcome-to-sportsbook mappings, and trade parameters
3. Implement `data_sources.py` — this can often reuse an existing sportsbook fetcher if the data source is the same (just different market within the same sport)
4. Implement `fair_value.py` — may be identical to another market's if the vig removal logic is the same
5. Add the market to `config.yaml`'s `enabled_markets` list
6. Restart the bot

For markets with **different types of data sources** (not sportsbooks — e.g., weather data for a weather market, polling data for an election market), the same plugin interface works. The `data_sources.py` fetches from whatever source is relevant, and `fair_value.py` converts it to a probability.

---

## 5. Data Pipeline

### 5.1 External Data (Sportsbook Odds)

**Fetch cycle:**
```
Every poll_interval seconds (per plugin):
  1. For each configured sportsbook (in parallel):
     - HTTP request to fetch odds page/API
     - Parse response → extract decimal odds per outcome
     - Handle errors: timeout, parsing failure, rate limit
  2. Merge results into standardized format:
     { outcome_name: [(sportsbook, decimal_odds), ...] }
  3. Pass to fair_value.compute()
```

**Data format standardization:**
- All odds converted to **decimal format** (European odds) internally
- American odds (+350, -150) converted: positive = (odds/100) + 1, negative = (100/|odds|) + 1
- Fractional odds (7/2) converted: (num/denom) + 1

**Staleness handling:**
- Track `last_updated` timestamp per sportsbook per market
- If a sportsbook hasn't returned new data in > 10 minutes, flag it as stale
- Stale sources get reduced weight (or excluded if > 30 min stale)

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
Sportsbook A ──┐
Sportsbook B ──┤  (parallel HTTP fetches, every 30-60s)
Sportsbook C ──┤
Sportsbook D ──┼──► data_sources.fetch() ──► raw_odds dict
Sportsbook E ──┤
Sportsbook F ──┘
                                                │
                                                ▼
                                    fair_value.compute()
                                                │
                                                ▼
                                    OutcomeFairValue per team
                                    (fair prob + confidence)
                                                │
                        ┌───────────────────────┤
                        ▼                       ▼
              Polymarket Price            Compare: edge?
              (from WS or Gamma)                │
                        │                       ▼
                        └──────────► Signal (BUY YES @ team X)
                                                │
                                                ▼
                                        Risk Manager
                                          (approve?)
                                                │
                                                ▼
                                           Executor
                                        (place FOK order)
```

---

## 6. Signal Generation & Trade Decision

### 6.1 Signal Evaluation

```python
def evaluate_signals(fair_values, polymarket_prices, trade_params) -> list[Signal]:
    signals = []
    for fv in fair_values:
        pm_price = polymarket_prices[fv.token_id]  # PriceInfo with best_bid, best_ask

        # Edge calculation: fair value vs what we'd pay/receive
        # To BUY: we pay the best ask. Edge = fair_value - best_ask
        buy_edge = fv.fair_value - pm_price.best_ask
        # To SELL: we receive the best bid. Edge = best_bid - fair_value
        sell_edge = pm_price.best_bid - fv.fair_value

        if buy_edge > trade_params.edge_threshold:
            if fv.confidence >= trade_params.min_confidence:
                if fv.sources_agreeing >= trade_params.min_sources:
                    bet_size = kelly_bet_size(
                        fair_prob=fv.fair_value,
                        market_price=pm_price.best_ask,
                        bankroll=trade_params.current_bankroll,
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
                            confidence=fv.confidence,
                        ))

        elif sell_edge > trade_params.edge_threshold:
            # Only sell if we hold a position in this token
            if fv.confidence >= trade_params.min_confidence:
                if fv.sources_agreeing >= trade_params.min_sources:
                    signals.append(Signal(
                        token_id=fv.token_id,
                        side="SELL",
                        edge=sell_edge,
                        fair_value=fv.fair_value,
                        market_price=pm_price.best_bid,
                        min_price=pm_price.best_bid,
                        size=None,  # sell entire position (or available liquidity)
                        confidence=fv.confidence,
                    ))

    return signals
```

### 6.2 Edge Threshold Considerations

The edge threshold must cover:
1. **Taker fees** (if applicable): up to 0.44% for sports markets at 50% price, less at extremes
2. **Slippage**: the price may move slightly between signal and execution
3. **Model uncertainty**: our fair value estimate isn't perfect
4. **Safety buffer**: additional margin for profitability

Starting recommendation: **5% edge threshold** (0.05 in probability terms). This is conservative — can be tightened as the model proves accurate.

Example: if our fair value for the Leafs is 25% and Polymarket asks 19 cents, edge = 0.06 (6%) > 0.05 threshold → BUY signal.

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
        bankroll: current available bankroll in USDC
        kelly_fraction: fraction of full Kelly to use (0.25 = quarter-Kelly)
        min_bet: minimum bet size in USDC (skip if Kelly suggests less)
        max_bet: maximum bet size in USDC per trade

    Returns:
        Bet size in USDC, or 0 if Kelly says don't bet
    """
    if market_price <= 0 or market_price >= 1:
        return 0

    b = (1 / market_price) - 1  # net odds
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
Fair value: 0.25 (25% chance)
Market ask: 0.19 ($0.19 per share)
Bankroll:   $1000
Net odds:   1/0.19 - 1 = 4.26

Full Kelly: (0.25 * 4.26 - 0.75) / 4.26 = 0.074 (7.4% of bankroll = $74)
Quarter-Kelly: $74 * 0.25 = $18.50
→ Bet $18.50 on this outcome
```

**Why fractional Kelly:**
- Full Kelly assumes our fair value estimates are perfectly calibrated — they won't be, especially early on
- Quarter-Kelly provides ~75% of the long-term growth rate of full Kelly but with far less variance
- Can increase the fraction (to half-Kelly, etc.) as the model proves accurate over time

**Bankroll definition:** `current_bankroll` = total USDC balance + mark-to-market value of all positions. Updated each polling cycle.

### 6.4 Sell Strategy: Edge Disappearance

The primary exit strategy is to **sell when the edge disappears** — i.e., when the Polymarket price converges to (or exceeds) our fair value estimate, meaning the mispricing we bet on has corrected.

**Sell conditions (checked every polling cycle for all open positions):**

1. **Edge gone + liquidity available:** `polymarket_best_bid >= fair_value` AND sufficient liquidity at the bid → SELL entire position at market (FOK at best bid). The mispricing has corrected; take the profit.

2. **Edge reversed:** `fair_value < avg_cost` (our model now says the outcome is less likely than what we paid) → this is a model-driven exit, regardless of whether the Polymarket price is favorable. Sell at market if liquidity allows.

3. **Stop-loss:** P&L exceeds loss threshold → forced exit (see Risk Management section).

4. **Hold otherwise:** If the edge still exists (fair value > market price) or if there's no liquidity at a reasonable price, continue holding. The position pays $1 if the outcome occurs, so holding to resolution is the fallback.

```python
def check_exits(positions, fair_values, polymarket_prices, trade_params):
    """Check all open positions for exit conditions."""
    exit_signals = []
    for pos in positions:
        fv = fair_values.get(pos.token_id)
        pm = polymarket_prices.get(pos.token_id)
        if not fv or not pm:
            continue

        # Condition 1: Edge disappeared — fair value ≤ market bid (we can sell at or above fair value)
        if pm.best_bid >= fv.fair_value and pm.bid_liquidity >= pos.size:
            exit_signals.append(Signal(
                token_id=pos.token_id, side="SELL", edge=0,
                fair_value=fv.fair_value, market_price=pm.best_bid,
                min_price=fv.fair_value,  # don't sell below fair value
                size=None,  # sell entire position
                confidence=fv.confidence, reason="edge_disappeared"
            ))

        # Condition 2: Edge reversed — model says we're wrong
        elif fv.fair_value < pos.avg_cost * 0.90:  # fair value dropped >10% below entry
            exit_signals.append(Signal(
                token_id=pos.token_id, side="SELL", edge=0,
                fair_value=fv.fair_value, market_price=pm.best_bid,
                min_price=0,  # willing to sell at any price (cut losses)
                size=None,
                confidence=fv.confidence, reason="edge_reversed"
            ))

    return exit_signals
```

### 6.5 When NOT to Trade

Even with sufficient edge, skip if:
- Confidence score is too low (< `min_confidence`)
- Too few sportsbooks have data (< `min_sources`)
- Price is outside the configured `price_range` (avoid extremes near 0 or 1)
- Available liquidity at the target price is too thin (< Kelly bet size)
- The signal is for SELL but we hold no position
- Cooldown is active for this outcome (recently traded)

---

## 7. Order Execution

### 7.1 Order Types for Market-Taking

| Order Type | When to Use | Behavior |
|---|---|---|
| **FOK** (Fill-or-Kill) | Default for clean execution | Entire order fills at target price or is cancelled. No partial fills, no stale orders. |
| **FAK** (Fill-and-Kill) | When partial fills are OK | Fills whatever is available at target price, cancels the rest. Use when liquidity is thin but any fill is better than none. |

**Never use GTC** for taking signals. A GTC order that doesn't immediately fill becomes a passive limit order sitting on the book — this turns us into a market maker, which is not the intent and creates adverse selection risk.

### 7.2 Execution Flow

```
Signal received (BUY YES token for team X at price ≤ 0.19, Kelly size = $18.50)
  │
  ├── 1. Check order book liquidity at 0.19 or better
  │      Walk asks from best ask upward, accumulate size
  │      Available: 200 shares at ≤ 0.19
  │
  ├── 2. Size the order: Kelly says $18.50 → 18.50 / 0.19 = ~97 shares
  │      200 available at price → can fill 97 shares.
  │
  ├── 3. Place FOK order:
  │      token_id=X, side=BUY, size=97, price=0.19
  │
  ├── 4a. If filled:
  │       - Save market snapshot (all sportsbook odds at time of trade)
  │       - Update position tracker: +97 shares @ avg 0.19
  │       - Log: "BUY 97 shares of Leafs YES @ 0.19 (edge: 6%, Kelly: $18.50)"
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

| Limit | Scope | Default | Purpose |
|---|---|---|---|
| `max_position_size` | Per outcome | $100 | Cap exposure to any single team/outcome |
| `max_market_exposure` | Per market (all outcomes combined) | $300 | Cap total exposure in one event |
| `max_portfolio_exposure` | All markets | $1000 | Cap total bot exposure |
| `max_daily_loss` | Per day | $50 | Stop trading if daily P&L hits this |

### 8.2 Risk Gates (checked before every trade)

```python
class RiskManager:
    def approve(self, signal: Signal, tracker: PositionTracker) -> bool:
        # 1. Position limit: would this trade exceed max_position_size?
        current = tracker.get_position(signal.token_id)
        if current.size_usd + signal.size > self.max_position_size:
            return False  # or reduce size to fit

        # 2. Market exposure: total across all outcomes in this market
        market_exposure = tracker.get_market_exposure(signal.market_id)
        if market_exposure + signal.size > self.max_market_exposure:
            return False

        # 3. Portfolio exposure
        total_exposure = tracker.get_total_exposure()
        if total_exposure + signal.size > self.max_portfolio_exposure:
            return False

        # 4. Daily loss limit
        daily_pnl = tracker.get_daily_pnl()
        if daily_pnl < -self.max_daily_loss:
            return False

        # 5. Cooldown: recently traded this outcome?
        if tracker.is_on_cooldown(signal.token_id):
            return False

        # 6. SELL signals only if we hold the position
        if signal.side == "SELL" and current.size <= 0:
            return False

        return True
```

### 8.3 Stop-Loss

Unlike the market-maker which has a complex stop-loss tied to order management, the taker bot's stop-loss is simpler:

- **Per-position stop-loss:** If a position's mark-to-market P&L falls below a threshold (e.g., -30% of entry value), generate a SELL signal at market price
- **Implementation:** During each polling cycle, check all open positions against current Polymarket prices. If a position is down beyond the threshold, create a forced SELL signal that bypasses normal edge requirements
- **Cooldown after stop-loss:** Don't re-enter the same position for a configurable period (e.g., 2 hours)

### 8.4 Position Merging

Keep from the market-maker bot: if the bot ends up holding both YES and NO tokens in the same market (possible in multi-outcome neg-risk markets where different outcomes are bought at different times), merge them on-chain to recover USDC.

- Check for mergeable positions every polling cycle
- If `min(YES_position, NO_position) > 10` for any condition_id, trigger merge
- Reuse the Node.js merger subprocess approach from the market-maker bot

---

## 9. Position & Portfolio Tracking

### 9.1 Position Tracker

```python
@dataclass
class Position:
    token_id: str
    outcome_name: str
    market_name: str
    condition_id: str
    side: str              # "YES" or "NO"
    size: float            # number of shares held
    avg_cost: float        # average entry price
    current_price: float   # latest mark-to-market price
    unrealized_pnl: float  # (current_price - avg_cost) * size
    realized_pnl: float    # from closed trades
    last_trade_time: datetime
```

**Tracking approach:**
- **Primary:** Local tracking updated on every fill event from User WebSocket (optimistic, instant)
- **Secondary:** Periodic sync from Polymarket Data API (every 60s) as ground truth
- **Reconciliation:** If API position differs from local by > 5%, log a warning and adopt API values

### 9.2 P&L Calculation

```
Unrealized P&L = (current_price - avg_cost) × shares_held
Realized P&L   = Σ (sell_price - avg_cost) × shares_sold  [for each closed trade]
Total P&L      = Unrealized + Realized
Daily P&L      = Total P&L since midnight UTC
```

### 9.3 State Persistence

Bot state is saved to `data/state.json` on every position change and loaded on startup:

```json
{
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
  },
  "daily_pnl": {
    "2026-03-19": -5.20
  },
  "trade_history": [
    {
      "timestamp": "2026-03-19T14:30:00Z",
      "token_id": "token_abc123",
      "side": "BUY",
      "size": 97,
      "price": 0.19,
      "edge": 0.06,
      "fair_value": 0.25,
      "kelly_fraction_used": 0.25,
      "kelly_bet_usd": 18.50,
      "confidence": 0.85,
      "market_name": "2026 NHL Stanley Cup Champion",
      "outcome": "Toronto Maple Leafs",
      "reason": "edge_detected",
      "odds_snapshot": {
        "betano": 4.50,
        "bet365": 4.40,
        "draftkings": 4.45,
        "fanduel": 4.55,
        "betmgm": 4.60,
        "caesars": 4.50
      },
      "polymarket_snapshot": {
        "best_bid": 0.18,
        "best_ask": 0.19,
        "bid_liquidity": 500,
        "ask_liquidity": 200
      }
    }
  ]
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
  poll_interval: 60          # seconds between evaluation cycles
  position_sync_interval: 60 # seconds between API position syncs
  ws_ping_interval: 5        # WebSocket keepalive interval

# Risk management defaults (can be overridden per market)
risk:
  max_position_size: 100     # USD per outcome
  max_market_exposure: 300   # USD per market (all outcomes)
  max_portfolio_exposure: 1000  # USD total
  max_daily_loss: 50         # USD — stop trading for the day
  default_cooldown_minutes: 30

# Enabled markets (list of plugin directory names)
enabled_markets:
  - nhl_stanley_cup

# Logging
logging:
  level: INFO
  file: data/logs/bot.log
  console: true
  trade_log: data/logs/trades.log  # separate file for trade events only
```

### 10.2 Environment Variables (`.env`)

```
PK=0x...                          # Private key of Safe owner EOA
BROWSER_ADDRESS=0x...             # Gnosis Safe contract address
```

---

## 11. Logging & Monitoring

### 11.1 Log Levels

| Level | What Gets Logged |
|---|---|
| **DEBUG** | Raw sportsbook responses, order book snapshots, all price calculations |
| **INFO** | Trade signals (generated, approved, rejected), order placements, fills, position changes |
| **WARNING** | Data source failures, stale data, position reconciliation mismatches, partial fills |
| **ERROR** | API errors, WebSocket disconnects, order rejections, authentication failures |

### 11.2 Key Log Events

```
[INFO]  Signal: BUY Leafs YES | fair=0.250 ask=0.190 edge=0.060 conf=0.85 sources=5 kelly=$18.50
[INFO]  Risk: APPROVED | pos=$0/$100 | market=$0/$300 | portfolio=$0/$1000
[INFO]  Order: FOK BUY 97 shares @ 0.19 | token=abc123
[INFO]  Fill: FILLED 97 shares @ 0.19 | cost=$18.43 | pos now 97 @ 0.19
[INFO]  P&L: Leafs YES | unrealized=$0.00 | realized=$0.00 | daily=$-18.43
[INFO]  Exit: SELL Leafs YES | reason=edge_disappeared | fair=0.19 bid=0.21 | +$1.94

[WARNING] Stale data: bet365 NHL Stanley Cup last updated 15m ago
[WARNING] Liquidity: only 50 shares available at 0.19, need 97

[ERROR] WebSocket disconnected, reconnecting in 5s...
[ERROR] Order rejected: insufficient balance
```

### 11.3 Trade Snapshot Log

Every trade (entry or exit) saves a **full market snapshot** — the odds from every data source at the time of execution. This is critical for later analysis of model accuracy and edge decay.

**Saved as JSON, one file per trade:** `data/snapshots/{timestamp}_{outcome}_{side}.json`

```json
{
  "timestamp": "2026-03-19T14:30:00Z",
  "market": "2026 NHL Stanley Cup Champion",
  "outcome": "Toronto Maple Leafs",
  "side": "BUY",
  "execution": {
    "size_shares": 97,
    "price": 0.19,
    "size_usd": 18.43,
    "fill_status": "FILLED",
    "order_type": "FOK"
  },
  "signal": {
    "fair_value": 0.25,
    "edge": 0.06,
    "confidence": 0.85,
    "kelly_bet_usd": 18.50,
    "kelly_fraction": 0.25
  },
  "odds_snapshot": {
    "betano": {"decimal": 4.50, "implied_prob": 0.222, "devigged_prob": 0.193, "timestamp": "2026-03-19T14:29:45Z"},
    "bet365": {"decimal": 4.40, "implied_prob": 0.227, "devigged_prob": 0.198, "timestamp": "2026-03-19T14:29:50Z"},
    "draftkings": {"decimal": 4.45, "implied_prob": 0.225, "devigged_prob": 0.195, "timestamp": "2026-03-19T14:29:48Z"},
    "fanduel": {"decimal": 4.55, "implied_prob": 0.220, "devigged_prob": 0.191, "timestamp": "2026-03-19T14:29:52Z"},
    "betmgm": {"decimal": 4.60, "implied_prob": 0.217, "devigged_prob": 0.189, "timestamp": "2026-03-19T14:29:47Z"},
    "caesars": {"decimal": 4.50, "implied_prob": 0.222, "devigged_prob": 0.193, "timestamp": "2026-03-19T14:29:49Z"}
  },
  "polymarket_snapshot": {
    "best_bid": 0.18,
    "best_ask": 0.19,
    "bid_depth_100": 500,
    "ask_depth_100": 200,
    "gamma_best_bid": 0.18,
    "gamma_best_ask": 0.19
  },
  "position_after": {
    "size": 97,
    "avg_cost": 0.19,
    "total_exposure_usd": 18.43
  }
}
```

This snapshot enables post-hoc analysis: did the sportsbooks agree? Was the edge real? How much did the price move after our trade? Did the model's fair value prove accurate?

### 11.4 Trade Summary Log

A compact CSV log for quick P&L review:

```csv
timestamp,market,outcome,side,shares,price,usd,edge,fair_value,kelly_usd,confidence,sources,fill,reason
2026-03-19T14:30:00Z,Stanley Cup,Leafs,BUY,97,0.19,18.43,0.060,0.250,18.50,0.85,5,FILLED,edge_detected
2026-03-20T09:15:00Z,Stanley Cup,Leafs,SELL,97,0.21,20.37,0.000,0.190,0,0.82,5,FILLED,edge_disappeared
```

---

## 12. What to Keep vs Discard from the Market-Maker Bot

### Keep (adapt for taker)

| Component | Why | Adaptation |
|---|---|---|
| **Authentication** (PK, BROWSER_ADDRESS, signature_type=2) | Required for any Polymarket trading | Direct reuse |
| **ClobClient initialization** | API key derivation, HTTP client setup | Direct reuse |
| **WebSocket connections** | Real-time price data + fill tracking | Keep both Market WS and User WS |
| **Order book storage** (SortedDict) | Need to know available liquidity | Keep, but read-only (we don't post passive orders) |
| **Position tracking** (local + API sync) | Must know what we hold | Simplify — no need for `performing` set complexity |
| **Position merging** (Node.js subprocess) | Recover capital from YES+NO overlap | Direct reuse |
| **On-chain approvals** | Required before first trade | Direct reuse |
| **Auto-reconnect for WebSockets** | Must stay connected 24/7 | Direct reuse |
| **User WS trade lifecycle** (MATCHED → CONFIRMED → MINED) | Track fill status | Simplify — we mainly care about MATCHED for position updates |

### Discard

| Component | Why |
|---|---|
| **Google Sheets for config** | Overkill for a taker bot — use YAML config files instead. Sheets made sense for the maker because a human was constantly adjusting markets and hyperparameters. The taker bot's config changes rarely. |
| **Market discovery / reward calculation** (`update_markets.py`, `find_markets.py`) | We don't earn maker rewards. Market selection is manual (config-driven), not algorithmic. |
| **Spread management / order pricing at inside of spread** | We don't post passive orders. We take existing liquidity. |
| **Dual-sided order management** (maintain bid AND ask) | We make directional bets, not market-making spreads. |
| **Order replacement optimization** (only cancel if >0.5c / >10% change) | We don't maintain standing orders. Each trade is one-shot. |
| **Volatility calculation and gating** | Volatility matters for market makers who risk getting picked off. As takers, we only trade when we see edge — volatility is handled implicitly by the edge threshold. |
| **Stats updater** (`update_stats.py`) | Replace with simple logging. No need for a separate process writing to Sheets. |
| **Sentiment ratio** (bid/ask volume comparison) | Market-making signal, not relevant for directional taking. |
| **Price sanity check vs sheet reference** | Replace with confidence score and multi-source agreement. |
| **Sleep period / cooldown after stop-loss** (file-based) | Replace with in-memory cooldown tracker (persisted to state.json). |
| **`performing` set** | Needed for the maker's optimistic updates during rapid order cycling. The taker executes FOK orders and gets an immediate result — no in-flight ambiguity. |
| **Multiplier for cheap tokens** | Maker-specific sizing heuristic. Taker sizing is driven by edge and risk limits. |

### Transform

| Market-Maker Concept | Market-Taker Equivalent |
|---|---|
| Continuously quote both sides | Place one-shot directional orders when edge exists |
| React to every order book tick | Poll on a 30-60s cycle (futures odds change slowly) |
| Earn the spread + maker rewards | Earn from mispricing edge (fair value vs market price) |
| Manage inventory risk (building too much of one side) | Manage directional risk (position limits, stop-loss) |
| Price based on order book (inside the spread) | Price based on external data (sportsbook-derived fair value) |
| GTC limit orders (passive, wait for fill) | FOK/FAK orders (aggressive, immediate fill or cancel) |

---

## 13. Implementation Phases

### Phase 1: Foundation (Core Engine + Polymarket Client)

**Goal:** Connect to Polymarket, authenticate, read prices, and place a test order.

1. Set up project structure and dependencies
2. Implement `polymarket_client.py`:
   - Authentication (PK, BROWSER_ADDRESS, ClobClient)
   - Gamma API: fetch event by slug → get token IDs, prices
   - CLOB API: fetch order book, place order, cancel order
   - Basic WebSocket: connect to Market WS, receive price updates
3. Implement `state.py`: save/load state to JSON
4. Implement `models.py`: data classes
5. **Test:** manually place a small FOK order on a test market to verify the full auth + execution flow works

### Phase 2: First Market Plugin (NHL Stanley Cup)

**Goal:** Fetch sportsbook odds, compute fair values, generate signals.

1. Implement `markets/base.py` (plugin interface)
2. Implement `markets/nhl_stanley_cup/`:
   - `data_sources.py`: fetch odds from configured sportsbooks
   - `fair_value.py`: vig removal, aggregation, sharp-book weighting
   - `plugin.py`: tie it together
   - `config.yaml`: team mappings, trade params
3. Implement `core/signal.py`: signal evaluation logic + Kelly criterion bet sizing
4. **Test:** verify fair value calculations against manual spreadsheet. Verify Kelly sizing produces sensible bet sizes across a range of edges/prices. Run signal generation in dry-run mode (log signals but don't execute).

### Phase 3: Execution & Risk Management

**Goal:** Execute real trades with proper risk controls.

1. Implement `core/executor.py`: order placement with FOK, liquidity checks, trade snapshot logging
2. Implement `core/risk_manager.py`: all risk gates
3. Implement `core/position_tracker.py`: position tracking, P&L
4. Implement exit logic: edge-disappearance sell strategy, stop-loss
5. Connect User WebSocket for fill tracking
6. Implement `core/engine.py`: main loop tying everything together (entry signals + exit checks each cycle)
7. **Test:** run with very small position sizes ($5-10) on the live market. Monitor fills, P&L, and signal accuracy.

### Phase 4: Monitoring & Hardening

**Goal:** Production-ready with proper logging and error handling.

1. Structured logging with trade snapshot logs (full odds from all sources at time of each trade)
2. Graceful shutdown (save state on SIGINT)
3. Error recovery (API failures, WebSocket disconnects, stale data)
4. Position merging integration
5. **Validate:** run for 2+ weeks, analyze trade snapshots for:
   - Signal accuracy (% of trades that end profitable)
   - Edge decay (does edge disappear by the time we execute?)
   - P&L after fees
   - Data source reliability

### Phase 5: Expand to More Markets

**Goal:** Add more markets using the plugin system.

1. Create plugins for additional markets (other NHL divisions, conferences, Stanley Cup)
2. Potentially expand to other sports or non-sports markets
3. Refine edge thresholds and position sizing based on Phase 4 learnings
4. Consider shared data source modules (e.g., one sportsbook fetcher reused across all NHL markets)

---

## 14. Technical Decisions

### Language & Runtime
- **Python 3.11+** — async/await for WebSockets, rich ecosystem for data work
- **`uv`** for package management (fast, reliable)

### Key Dependencies
| Package | Purpose |
|---|---|
| `py-clob-client` | Polymarket CLOB API client (order signing, placement) |
| `web3` | Polygon RPC for on-chain operations (balance checks, merging) |
| `websockets` | Real-time market data + user event streams |
| `curl_cffi` | HTTP requests with browser TLS fingerprinting (for sportsbook scraping) |
| `pyyaml` | Configuration file parsing |
| `sortedcontainers` | Efficient order book storage (SortedDict) |
| `eth-account` | Ethereum key management |
| `python-dotenv` | Load .env file |

### Why Not Google Sheets?
The market-maker uses Sheets because a human operator frequently adjusts which markets to trade and tweaks hyperparameters. A market-taking bot's config changes rarely — new markets are added occasionally, and thresholds are tuned after analysis, not in real-time. YAML files are simpler, version-controlled, and don't require Google API credentials.

### Why Polling Instead of Pure WebSocket?
Sportsbook odds for futures markets change a few times per day, not sub-second. A 30-60 second polling cycle is more than sufficient and vastly simpler than reverse-engineering proprietary WebSocket protocols from 6 sportsbooks. The Polymarket side uses WebSockets for real-time price data, but signal generation is driven by the slower sportsbook polling cadence.

### Order Type Choice: FOK over GTC
A GTC order that doesn't immediately fill sits on the order book as a passive limit order. This is problematic because:
1. The fair value may change while the order sits
2. We become a de facto market maker, exposed to adverse selection
3. We'd need to manage order lifecycle (cancel stale orders, etc.)

FOK eliminates all of this: fill now or don't. If the price we want isn't available, we simply wait for the next polling cycle.
