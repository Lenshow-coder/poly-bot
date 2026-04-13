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
│  │ API (+WS*)   │  │ Positions,   │  │ restarts (JSON files)    │  │
│  │              │  │ P&L, fills   │  │                          │  │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

\* **WebSocket in the Polymarket client:** target architecture (Phase 3+). As of Phase 2 complete, prices for signal generation come from the **CLOB REST order book** (`get_prices` per token). Market WebSocket is verified separately via `test_connection.py --ws-test`.

### Flow

1. **Each scraper runs as an independent async task** on its own interval (e.g., scraper A every 30s, scraper B every 5 minutes). Scrapers are fully decoupled from each other. *(Phase 2 today: `main.py --dry-run` runs a **single pass** over scrapers; per-scraper `interval` is configured but **not yet** used to schedule repeating loops — that belongs in `core/engine.py` in Phase 3.)*
2. **When any scraper completes**, its `ScrapedOdds` is immediately processed — no waiting, no coordination with other scrapers.
3. **Each plugin** whose scraper event key appears in the scraper's results extracts its odds (outcome names aligned with Polymarket/Gamma) and computes fair values.
4. **The signal engine** compares each fair value to the current Polymarket price. **Phase 2:** best bid/ask from **CLOB REST** per token. **Later:** optional WebSocket-backed cache in the client/engine for lower latency.
5. When relative edge (and filters like `sportsbook_buffer`, `price_range`, `min_sources`) pass, a **trade signal** is emitted (BUY and/or SELL — see `core/signal.py`).
6. **Risk manager** gates the signal: checks position limits, portfolio exposure, cooldowns, balance thresholds *(Phase 3 — not wired yet; `config.yaml` already defines `risk` and `trade_defaults` for enforcement).*
7. **Executor** places the order on Polymarket via the CLOB API and tracks its lifecycle *(Phase 3 — `PolymarketClient.place_order` exists; dedicated executor module still to add).*

Scrapers are completely independent — each runs on its own schedule, and the latency from any individual scraper completing through signal generation is **under ~1 second** (REST order book fetches dominate). Full **scraper interval loops + live execution** are Phase 3.

---

## 2. Project Structure

**As implemented through Phase 2** (Phase 3 adds the commented “planned” files):

```
poly-bot/
├── main.py                      # Entry: PolymarketClient, load plugins/scrapers; --dry-run runs one pipeline pass; live mode = Phase 3
├── config.yaml                  # Global configuration (Polymarket, polygon, contracts, engine, risk, trade_defaults, scrapers, enabled_markets, …)
├── pyproject.toml               # Dependencies (managed with uv); no requirements.txt
├── uv.lock                      # Lockfile
├── test_connection.py           # Integration checks (Gamma, CLOB, balances, optional --ws-test, order smoke tests)
│
├── core/
│   ├── __init__.py
│   ├── polymarket_client.py     # CLOB + Gamma + data API (positions); Web3 balances/approvals; REST order book → PriceInfo; place_order (sync)
│   ├── signal.py                # evaluate_signals(), kelly_bet_size(), check_exits(); BUY/SELL signal emission
│   ├── sportsbook_signal.py     # Optional: flag outlier books vs consensus (diagnostics; config sportsbook_signals)
│   ├── state.py                 # StateManager — JSON persistence
│   ├── models.py                # PriceInfo, MarketInfo, EventInfo, OrderResult, Position, BankrollSnapshot, Signal, SportsbookSignal
│   └── utils.py                 # Logging, load_config, env credentials, data dir
│   # Planned Phase 3:
│   # ├── engine.py
│   # ├── executor.py
│   # ├── risk_manager.py
│   # ├── position_tracker.py
│   # └── notifier.py
│
├── scrapers/
│   ├── __init__.py
│   ├── base.py                  # BaseScraper(name, interval); async scrape()
│   ├── models.py                # ScrapedOdds, EventOdds, BookOdds
│   └── csv_scraper.py           # Production path: reads normalized odds CSV → ScrapedOdds
│
├── markets/
│   ├── __init__.py
│   ├── base.py                  # MarketPlugin, OutcomeFairValue, TradeParams
│   ├── fair_value.py            # FairValueEngine — vig removal, weighted aggregation (shared across futures markets)
│   ├── futures_plugin.py        # FuturesPlugin — config-driven; Gamma auto-discovery of outcomes → token IDs
│   └── configs/                 # One YAML per enabled market (filename = key in enabled_markets)
│       └── nhl_stanley_cup.yaml # Example: type futures, polymarket slug, scraper event_key, trade_params overrides
│
├── data/                        # Runtime (gitignored)
│   ├── state.json
│   ├── normalized_odds.csv      # Input to csv scraper (example path; configurable)
│   └── logs/
│
├── tests/
│   ├── test_fair_value.py
│   ├── test_signal.py
│   └── test_sportsbook_signal.py
│   # Planned: test_risk_manager.py, test_executor.py, …
│
├── .env                         # Secrets — gitignored (see .env.example)
└── .env.example                 # PK, BROWSER_ADDRESS (Telegram vars when notifier lands)
```

---

## 3. Core Engine

### 3.1 `engine.py` — Main Orchestrator *(Phase 3 — not present yet)*

**Target behavior:** the engine runs a single async event loop that:

1. **Initializes** the Polymarket client (authenticates, derives API keys)
2. **Loads enabled scrapers and market plugins** from config
3. **Optionally starts WebSocket connections** for a local order book cache (recommended Phase 3+; see §5.2)
4. **Launches each scraper as an independent async task**, each running on its own `interval`. When a scraper completes:
  - Its `ScrapedOdds` is passed to relevant plugins → `extract_odds()` → `compute_fair_values()`
  - Signal engine compares fair values to Polymarket prices
  - Signals pass through `risk_manager.approve()` → approved signals go to `executor.execute()`
5. **Runs a background task** that periodically syncs positions and balance from the API as a ground-truth check against local tracking

**Phase 2 stand-in:** `main.py` defines `dry_run_cycle()` which, for each scraper, awaits one `scrape()`, then runs plugins → `evaluate_signals()` → logs. There is **no** infinite `scraper_loop` yet and **no** `risk_manager` / `executor` calls.

```python
# Target pseudocode (Phase 3)
async def run():
    client = PolymarketClient(config)
    scrapers = load_scrapers(config)
    plugins = load_plugins(config, client)
    risk_mgr = RiskManager(config.risk)
    executor = Executor(client)
    position_tracker = PositionTracker(client)

    # Optional Phase 3+: asyncio.create_task(client.connect_market_ws(...))
    async def process_scraper_result(scraped_odds: ScrapedOdds):
        for plugin in plugins:
            if plugin.event_key not in scraped_odds.events:
                continue

            mapped_odds = plugin.extract_odds(scraped_odds)
            if not mapped_odds:
                continue
            fair_values = plugin.compute_fair_values(mapped_odds)
            books = {fv.token_id: client.get_order_book(fv.token_id) for fv in fair_values}
            prices = extract_prices(books)  # PriceInfo from book top-of-book

            signals = evaluate_signals(
                fair_values, prices, plugin.get_trade_params(),
                kelly_bankroll=config["risk"]["kelly_bankroll"],
                event_name=plugin.get_name(),
            )
            for signal in signals:
                # Book sweep: walk ask/bid ladder for depth-aware sizing
                book = books[signal.token_id]
                sweep = sweep_asks(book.asks, signal.fair_value, ...)  # or sweep_bids
                # Re-run Kelly against VWAP, cap shares to available depth
                signal.size_usd = recalculated_kelly
                signal.max_price = sweep.worst_price

                if risk_mgr.approve(signal, position_tracker):
                    if not config["engine"].get("dry_run"):
                        await executor.execute(signal)
                    else:
                        logger.info(f"[DRY RUN] Would execute: {signal}")

    async def scraper_loop(scraper: BaseScraper):
        while True:
            try:
                scraped_odds = await scraper.scrape()
                await process_scraper_result(scraped_odds)
            except Exception as e:
                logger.error(f"Scraper {scraper.get_name()} failed: {e}")
            await asyncio.sleep(scraper.interval)

    for scraper in scrapers:
        asyncio.create_task(scraper_loop(scraper))
    await asyncio.Event().wait()  # run until shutdown
```

### 3.1.1 Dry-Run Mode

The bot supports `--dry-run` and `engine.dry_run` in `config.yaml`. **Phase 2 behavior** (`main.py`):

- Runs **one pass** per invocation: each enabled scraper is scraped once, then each plugin processes results.
- Logs fair values (with best bid/ask from REST), optional sportsbook outlier lines (`sportsbook_signals`), and any **BUY** / **SELL** signals with edge and Kelly size.
- **No orders** are placed; live mode prints *“Live mode not yet implemented (Phase 3)”* and exits.

**Phase 3 extensions** (when engine + risk + executor exist):

- Risk manager approvals/rejections logged per signal
- Executor logs what would run (or runs for live)
- CSV trade log for executions and/or signal audit trail
- Position tracker updates only on real fills

```python
# Target (Phase 3 engine)
if not config["engine"].get("dry_run"):
    await executor.execute(signal)
else:
    logger.info(f"[DRY RUN] Would execute: {signal}")
```

### 3.2 `polymarket_client.py` — API client *(WebSocket: Phase 3 / hardening)*

**Implemented today (Phase 1–2):**

- Authentication: `PK`, `BROWSER_ADDRESS` from env; `ClobClient` with `signature_type` and `chain_id` from `config.yaml`
- Gamma: `get_event(slug)` → `EventInfo` / `MarketInfo` with YES/NO token IDs
- CLOB: `get_order_book`, `get_prices(token_id)` → `PriceInfo` (best bid/ask from REST; asks/bids sorted for correct best)
- Balances: `get_usdc_balance()` (wallet), `get_exchange_balance()` (collateral on exchange)
- Orders: synchronous `place_order(..., order_type="FOK"|"FAK")` → `OrderResult`
- On-chain: `approve_contracts()` for USDC + CTF approvals (exchange / neg-risk / CTF addresses from `config.yaml`)
- Data API: `get_positions()` (raw list from `polymarket.data_url`)

**Not in the client yet:** `connect_market_ws`, local `SortedDict` book cache, `cancel_order` wrapper. WebSocket smoke test lives in **`test_connection.py --ws-test`** only.

**Adapt (market-taking vs maker):**

- **FOK** / **FAK** via `py-clob-client` `OrderType` (default order type also in `trade_defaults.order_type` in config)
- **GTC** is discouraged for taking (see §14); aggressive limit at touch is still a limit order, not a standing quote

**Key methods (conceptual):**

```python
class PolymarketClient:
    def __init__(self, config: dict):
        # ClobClient, Web3 RPC fallback, gamma_url, data_url

    # Phase 3+: async def connect_market_ws(self, token_ids: list[str]): ...

    def get_prices(self, token_id: str) -> PriceInfo:
        # Today: REST order book. Future: optional cache fed by WS.

    def place_order(
        self, token_id, side, size, price, order_type: str = "FOK"
    ) -> OrderResult:
        # Synchronous post to CLOB

    def get_usdc_balance(self) -> float: ...
    def get_exchange_balance(self) -> float: ...
    def get_positions(self) -> list[dict]: ...
```

### 3.3 `executor.py` — Order Execution *(Phase 3 — not present yet)*

Responsible for translating a trade signal into an actual order and tracking the result. **`PolymarketClient.place_order`** is synchronous today; the executor can call it directly or wrap it in `asyncio.to_thread` if the engine stays async-first.

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

from scrapers.models import BookOdds, ScrapedOdds

@dataclass
class OutcomeFairValue:
    outcome_name: str       # e.g., "Toronto Maple Leafs"
    token_id: str           # Polymarket YES token ID for this outcome
    fair_value: float       # Devigged probability (0.0 - 1.0)
    sources_agreeing: int   # Count of contributing sportsbooks
    best_book_implied_prob: float  # raw implied prob from best decimal odds
    best_book_name: str
    book_devigged: dict[str, float] | None  # per-book devigged probs (for outlier diagnostics)

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
    def extract_odds(self, scraped_odds: ScrapedOdds) -> dict[str, list[BookOdds]]:
        """
        Filter ScrapedOdds to this plugin's event (via config scraper.event_key).
        Outcome keys must match Polymarket/Gamma outcome names for futures plugins.
        Returns: { outcome_name: [BookOdds, ...] }
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
        Merged global trade_defaults + plugin overrides — see markets/base.TradeParams:
        edge_threshold, max_outcome_exposure, kelly_fraction, min_bet_size, max_bet_size,
        order_type (FOK/FAK), min_sources, cooldown_minutes, price_range, sportsbook_buffer.
        """
```

### 4.2 Example: NHL Stanley Cup (`FuturesPlugin` + `markets/configs/nhl_stanley_cup.yaml`)

First production market uses the generic **`FuturesPlugin`** (`markets/futures_plugin.py`): no per-event Python package. Outcomes and YES token IDs are **auto-discovered** from Gamma at startup using `polymarket.event_slug`. The CSV / scraper must use **outcome names that match Polymarket’s `groupItemTitle`** for each team so `extract_odds()` can align books to tokens.

**`markets/configs/nhl_stanley_cup.yaml`** (illustrative; see repo for exact file):

```yaml
name: "2026 NHL Stanley Cup Champion"
type: futures

polymarket:
  event_slug: "2026-nhl-stanley-cup-champion"
  neg_risk: true

# Outcomes discovered from Polymarket — no manual token list.

trade_params:
  max_outcome_exposure: 200   # overrides global trade_defaults for this market only

scraper:
  event_key: "Stanley Cup Winner"   # must match CSV `market` column / ScrapedOdds.events key
```

Shared **`markets/fair_value.py`** (`FairValueEngine`) performs vig removal and weighted aggregation; weights come from `sportsbook_weight_defaults` in `config.yaml` plus optional `sportsbook_weights` in the plugin YAML.

> **Sportsbook data acquisition:** The **`csv` scraper** reads a normalized file (default `data/normalized_odds.csv`) with columns including `market`, `team`, `sportsbook`, `odds`. Each row is one book’s decimal odds for one outcome. Additional scrapers can be added under `scrapers/` and registered in `main.py` / config.

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

For **`FuturesPlugin`**, `extract_odds()` selects `scraped_odds.events[event_key]` and keeps only outcomes that exist in the Gamma-derived `token_map`. No separate `sportsbook_keys` table — **CSV team names must match Polymarket outcome labels**.

- **Adding a new futures event:** add `markets/configs/<name>.yaml`, set `type: futures`, slug, `scraper.event_key`, optional `trade_params` / `sportsbook_weights`, and add `<name>` to `enabled_markets`.
- **Adding a new scraper:** implement `BaseScraper`, register in `main.py` (`KNOWN_SCRAPERS` / loader), add a `scrapers:` entry in `config.yaml`.
- **Scrapers stay decoupled** from plugin internals as long as the odds contract (`ScrapedOdds`) is honored.

**Scraper base class:**

```python
class BaseScraper(ABC):
    interval: int  # seconds between scrape runs (configured per scraper; scheduling = Phase 3 engine)

    @abstractmethod
    async def scrape(self) -> ScrapedOdds:
        """Run the scraper. Returns ScrapedOdds with all events this scraper covers."""

    @abstractmethod
    def get_name(self) -> str:
        """Identifier for this scraper, e.g. 'csv'"""
```

Each scraper's output is processed when the engine invokes it. If multiple scrapers cover the same event, each completion re-runs the pipeline for that data.

**Fair value (`markets/fair_value.py`)**

```python
class FairValueEngine:
    """
    Per-outcome vig removal and weighted aggregation across books.
    Weights: global sportsbook_weight_defaults + plugin overrides.
    """
    def compute(self, mapped_odds: dict[str, list[BookOdds]]) -> dict[str, FairValueResult]:
        ...
```

**Vig removal example:**

```
Sportsbook has: Leafs +350 (4.50), Panthers +200 (3.00), Bruins +500 (6.00), ...
Implied probs:  22.2%,              33.3%,              16.7%, ... = sum 115% (15% vig)
Devigged:       19.3%,              29.0%,              14.5%, ... = sum 100%
```

### 4.3 Adding a New Event

To add a new **futures** event (same pattern as NHL Stanley Cup):

1. Copy `markets/configs/nhl_stanley_cup.yaml` to `markets/configs/<your_market>.yaml`.
2. Set `name`, `type: futures`, `polymarket.event_slug`, and `scraper.event_key` (must match the `market` column / `ScrapedOdds.events` key your scraper emits).
3. Optionally override `trade_params` or `sportsbook_weights`.
4. Ensure the odds file or scraper uses **outcome names identical to Polymarket** for that event.
5. Add `<your_market>` to `enabled_markets` in `config.yaml`.

No new Python is required unless you introduce a **new plugin type** (then add a class and register it in `main.PLUGIN_TYPES`). Shared `FairValueEngine` covers all futures-style multi-outcome markets.

No `data_sources.py` — the plugin filters from shared `ScrapedOdds`.

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

**Phase 2 (current):**

1. **CLOB REST order book** — `PolymarketClient.get_order_book` / `get_prices(token_id)` supply `PriceInfo` (best bid, best ask, midpoint) for signal evaluation. Each outcome YES token is queried over HTTP when the dry-run (or future engine) cycle runs.
2. **Gamma API** — Used at plugin startup to resolve `event_slug` → markets → YES/NO `clobTokenIds` (`get_event`). Not used per tick for prices in the current code path.
3. **Data API** — `get_positions()` for wallet positions (raw JSON); deeper position tracking is Phase 3.

**Phase 3+ (target):**

- **Market WebSocket** — Subscribe to monitored token IDs; maintain a local order book cache (e.g., `sortedcontainers.SortedDict`) for faster reads and depth walks. Auto-reconnect with backoff (Phase 4).
- **WS-triggered scraping (hybrid, Phase 4)** — In addition to the local book cache, use WebSocket ask updates above a configurable size threshold (e.g., $50) to trigger an immediate scraper run for that specific market. This supplements periodic polling — it catches Polymarket-side moves faster, while the polling loop still catches edge created by sportsbook-side line movement (where the Polymarket book doesn't change). Requires debouncing/throttling to avoid hammering scrapers on chatty markets. Depends on stable WS connection and live scraper loops from Phase 3.
- **Neg-risk note:** For some multi-outcome / neg-risk setups, effective prices may differ from naive CLOB display; if production behavior diverges, validate against Gamma `bestBid`/`bestAsk` or Polymarket docs and adjust the price source in the client or executor.

**Execution-time validation:** Even with WS cache, the **executor** should confirm liquidity at the limit price on the live book before posting FOK/FAK.

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
                    │         (REST now; WS cache Phase 3+)
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

**Implemented in `core/signal.py` (`evaluate_signals`, etc.):** compares each `OutcomeFairValue` to `PriceInfo` per token (same math as below), plus **`min_sources`**, **`price_range`** on bid/ask, **`sportsbook_buffer`** (relative gap between Polymarket ask and best raw book implied prob), and emits **`Signal`** objects (`core/models.py`). **SELL** signals set `size_usd=0` and `reason="edge_disappeared"` until Phase 3 assigns size from positions. **`check_exits()`** filters SELL signals to held token IDs. Optional book-vs-consensus flags live in **`core/sportsbook_signal.py`** (`config.sportsbook_signals`).

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

The exit strategy is simple: **sell when we can get a favorable price** relative to our fair value (market bid meaningfully above fair). Otherwise, hold to resolution.

**Phase 2 implementation:** `core/signal.py` treats **SELL** like BUY but inverted: `sell_edge = (best_bid - fair_value) / fair_value` must exceed `edge_threshold` (and `price_range` / `min_sources` must pass). **`check_exits()`** restricts SELL signals to **`held_token_ids`**. Execution and position-sized sells are **Phase 3**.

**Sell condition (conceptual — checked whenever fresh odds + prices run, for outcomes we hold):**

- **Edge on exit:** market bid high enough vs fair value (same relative threshold family as entry) **and** sufficient liquidity → SELL (FOK/FAK at best bid). The mispricing has corrected; take the profit.
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

**Authoritative file is the repo’s `config.yaml`.** Below is a structural summary aligned with the current project (values are examples).

```yaml
# PK and BROWSER_ADDRESS always from .env (see core/utils.py)
polymarket:
  clob_url: "https://clob.polymarket.com"
  gamma_url: "https://gamma-api.polymarket.com"
  data_url: "https://data-api.polymarket.com"   # positions / user data
  chain_id: 137
  signature_type: 1   # 0=EOA, 1=POLY_PROXY, 2=POLY_GNOSIS_SAFE — must match account type

polygon:
  rpc_urls:           # tried in order until one connects; may use ${ALCHEMY_API_KEY} etc.
    - "https://polygon-mainnet.g.alchemy.com/v2/${ALCHEMY_API_KEY}"
  usdc_address: "0x..."

contracts:
  exchange: "0x..."
  neg_risk_exchange: "0x..."
  ctf: "0x..."

engine:
  default_order_type: "FOK"    # informational; per-trade type from trade_defaults.order_type
  dry_run: false               # or pass --dry-run

# Used for Kelly sizing in dry-run; Phase 3 risk manager enforces the rest
risk:
  kelly_bankroll: 2000
  max_event_exposure: 200
  max_portfolio_exposure: 500
  min_balance: 50
  min_bankroll: 200

# Global trade defaults; plugins override per-key via markets/configs/*.yaml trade_params
trade_defaults:
  edge_threshold: 0.10
  max_outcome_exposure: 200
  kelly_fraction: 0.25
  min_bet_size: 5
  max_bet_size: 100
  order_type: "FAK"          # or FOK — py-clob-client OrderType
  min_sources: 2
  cooldown_minutes: 30
  price_range: [0.01, 0.66]
  sportsbook_buffer: 0.05    # min relative gap: (poly_ask - best_book_implied) / best_book_implied

sportsbook_weight_defaults:
  draftkings: 1.0
  fanduel: 2.0
  # ...

# Optional diagnostics in dry-run / future logs
sportsbook_signals:
  enabled: true
  edge_threshold: 0.1
  abs_edge_threshold: 0.01
  min_sources: 3

book_sweep:
  max_levels: 10              # max ask/bid levels to walk
  max_sweep_price: 0.85       # absolute price cap regardless of edge

scrapers:
  - name: csv
    interval: 60              # seconds — used when Phase 3 engine schedules loops
    path: "data/normalized_odds.csv"

enabled_markets:              # basenames of markets/configs/<name>.yaml
  - nhl_stanley_cup

logging:
  level: "INFO"
  console: true
```

**Phase 3 / not in YAML yet:** `position_sync_interval`, `ws_ping_interval`, dedicated `telegram:` block — add when `notifier` and engine land. Per-market `max_outcome_exposure` today lives under each plugin config’s `trade_params`.

### 10.2 Environment Variables (`.env`)

**Current `.env.example` (repo):**

```
PK=...
BROWSER_ADDRESS=...
```

**Optional / Phase 3:** `ALCHEMY_API_KEY` (or similar) if referenced inside `polygon.rpc_urls`; **`TELEGRAM_BOT_TOKEN`**, **`TELEGRAM_CHAT_ID`** when Telegram notifier is implemented.

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

A compact CSV log for quick P&L review *(**Phase 3** — file not written yet; schema target below)*:

```csv
timestamp,event,outcome,side,shares,price,usd,edge_pct,fair_value,kelly_usd,sources,odds_scrape_ts,odds_fanduel,odds_draftkings,odds_betmgm,odds_betrivers,odds_bet365,odds_caesars,odds_thescore,odds_ozoon,odds_bol,odds_betano,odds_pinnacle,fill,reason
2026-03-19T14:30:00Z,Stanley Cup,Leafs,BUY,33,0.18,5.94,10.0,0.200,6.00,3,2026-03-19T14:29:45Z,5.55,5.60,,,5.50,,,,,,FILLED,edge_detected
2026-03-20T09:15:00Z,Stanley Cup,Leafs,SELL,33,0.21,6.93,0.0,0.210,0,3,2026-03-20T09:14:50Z,4.55,4.45,,,4.50,,,,,,FILLED,edge_disappeared
```

**Per-sportsbook odds columns:** Fixed set of columns for all known sportsbooks: `odds_fanduel`, `odds_draftkings`, `odds_betmgm`, `odds_betrivers`, `odds_bet365`, `odds_caesars`, `odds_thescore`, `odds_ozoon`, `odds_bol`, `odds_betano`, `odds_pinnacle`. Each contains the **decimal odds** that fed the fair value calculation for this outcome. Empty if that book didn't have odds for the outcome. Most will be empty until scrapers are added for those books.

**`odds_scrape_ts`:** Timestamp of the most recent scrape cycle that produced the odds row (i.e., the CSV file's last-modified time or an explicit timestamp from the scraper). Comparing this to `timestamp` (trade placement time) shows scrape-to-trade latency.

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
| **Authentication** (PK, BROWSER_ADDRESS, `signature_type` from config) | Required for any Polymarket trading   | Direct reuse — type must match account (EOA / proxy / Safe)                                                             |
| **ClobClient initialization**                              | API key derivation, HTTP client setup | Direct reuse                                                                                                            |
| **WebSocket connections**                                  | Real-time price data                  | **Target:** Market WS in client/engine (Phase 3+). **Today:** smoke test only in `test_connection.py --ws-test`         |
| **Order book storage** (SortedDict)                        | Need to know available liquidity      | **Target:** local cache with WS. **Today:** REST `get_order_book`; `sortedcontainers` is a dependency but unused in app code yet |
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

### Implementation status (April 2026)

| Phase | Status | Notes |
| ----- | ------ | ----- |
| **1** | **Done** | `pyproject.toml` + `uv`, `core/models.py`, `core/polymarket_client.py` (REST CLOB/Gamma/data API, Web3 balances + approvals, `place_order`), `core/state.py`, `core/utils.py`, `test_connection.py`. Market WebSocket **not** integrated into the client — verified via `test_connection.py --ws-test` only. |
| **2** | **Done** | `scrapers/models.py`, `scrapers/base.py`, `scrapers/csv_scraper.py`, `markets/base.py`, `markets/fair_value.py`, `markets/futures_plugin.py`, `markets/configs/nhl_stanley_cup.yaml`, `core/signal.py`, `core/sportsbook_signal.py`, `main.py` dry-run pipeline, tests `test_fair_value`, `test_signal`, `test_sportsbook_signal`. No stub scraper in repo. Scraper `interval` is stored but **not** used until Phase 3 loops. |
| **3** | **Done** | `core/engine.py` (async scraper loops, reconciliation, signal handlers), `core/executor.py`, `core/risk_manager.py`, `core/position_tracker.py`, `core/book_sweep.py` (order book sweep for depth-aware sizing), `runner/dashboard.py`, CSV trade logging. Book sweep walks ask/bid ladder to find fillable size within edge, re-runs Kelly against VWAP, and caps shares to available depth. |
| **4–5** | Planned | As below. |

### Phase 1: Foundation (Polymarket Client + Data Models) — complete

**Goal:** Connect to Polymarket, authenticate, read prices, and place a test order.

1. ~~Set up project structure and dependencies~~ (`pyproject.toml`, `uv`)
2. ~~Implement `core/models.py`~~ — `PriceInfo`, `MarketInfo`, `EventInfo`, `OrderResult`, `Position`, `BankrollSnapshot`, `Signal`, `SportsbookSignal`
3. ~~Implement `core/polymarket_client.py`~~:
  - Authentication (PK, `BROWSER_ADDRESS`, `ClobClient`, `signature_type` from YAML)
  - Gamma: `get_event(slug)` → token IDs
  - CLOB: `get_order_book`, `get_prices`, `place_order` (FOK/FAK), exchange balance
  - `get_usdc_balance()` (wallet), `get_exchange_balance()`, `get_positions()` (data API)
  - **Deferred:** WebSocket inside client; **cancel_order** wrapper (add if executor needs it)
4. ~~Implement `core/state.py`~~ — `StateManager`, JSON persistence
5. **Test:** `test_connection.py` (including optional `--place-order` / `--fill-order` / `--ws-test`)

### Phase 2: Scraper Interface + First Market (NHL Stanley Cup) — complete

**Goal:** Scraper contract, fair values, signals, dry-run end-to-end.

1. ~~`scrapers/models.py`~~ — `ScrapedOdds`, `EventOdds`, `BookOdds`
2. ~~`scrapers/base.py`~~ — `BaseScraper`
3. **Stub scraper** — not shipped; CSV path is the test/prod input
4. ~~`markets/base.py`~~ — `MarketPlugin`, `OutcomeFairValue`, `TradeParams.from_config`
5. ~~First market~~ — **`FuturesPlugin`** + **`markets/configs/nhl_stanley_cup.yaml`** (not a per-event Python package). **`markets/fair_value.py`** — `FairValueEngine`. Outcomes auto-discovered from Gamma; CSV outcome names must match Polymarket.
6. ~~`core/signal.py`~~ — edge filters, Kelly sizing, BUY/SELL signals, `check_exits`. Config-driven `sportsbook_buffer`, `price_range`, etc.
7. ~~`core/sportsbook_signal.py`~~ — optional outlier diagnostics (`sportsbook_signals` in YAML)
8. ~~`--dry-run` / `engine.dry_run`~~ — single pass over scrapers in `main.py`
9. **Tests:** ~~`tests/test_fair_value.py`, `test_signal.py`, `test_sportsbook_signal.py`~~

### Phase 3: Execution, Risk & Monitoring — done

**Goal:** Long-running bot, real execution, risk enforcement, observability.

1. **`core/executor.py`** — Wrap existing `PolymarketClient.place_order`: map `Signal` → size in shares, FOK/FAK from `TradeParams`, **liquidity / depth checks** before submit; handle partial fills for FAK; neg-risk nuances per §7.3 if needed.
2. **`core/risk_manager.py`** — Enforce `risk.*` and `trade_defaults` / per-plugin caps: `max_outcome_exposure`, `max_event_exposure`, `max_portfolio_exposure`, `min_balance`, `min_bankroll`, **cooldowns** (persist via `state.json`).
3. **`core/position_tracker.py`** — Reconcile `get_positions()` + fills; average cost; P&L hooks.
4. **Exit path** — **`check_exits()`** and SELL **`Signal`** generation already exist; Phase 3 **sizes** SELLs from holdings and **routes** through executor.
5. **CSV trade logging** — e.g. `data/trades.csv` (schema per §11.3).
6. **`core/notifier.py`** — Telegram (or similar); env vars documented in `.env.example` when added.
7. **`core/engine.py`** — `asyncio.create_task` **per-scraper** with `await asyncio.sleep(scraper.interval)`; on each scrape completion run plugin → signal → risk → execute; optional **WS** task updating a price cache; background position/balance sync.
8. **`main.py`** — Live mode entry: start engine instead of exiting after dry-run.
9. **Tests:** add `test_risk_manager`, `test_executor`, integration tests as appropriate.
10. **Test (live):** small sizes ($5–10), monitor fills and logs.

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

1. Add **`markets/configs/<new>.yaml`** + `enabled_markets` entry (same `FuturesPlugin` pattern) — no scraper changes if the CSV already includes the new `market` key
2. Potentially expand to other sports or non-sports events; new **plugin types** only if behavior diverges from futures
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
| `web3`             | Polygon RPC for on-chain operations (balance checks, approvals)       |
| `requests`         | HTTP client for Gamma / data-api endpoints in `polymarket_client`       |
| `websockets`       | Used by `test_connection.py` WS smoke test; engine/client WS in Phase 3+ |
| `curl_cffi`        | Reserved for TLS-fingerprinted HTTP (future sportsbook scrapers)      |
| `pyyaml`           | Configuration file parsing                                              |
| `sortedcontainers` | Planned for local order book / depth (`SortedDict`); not used in app code yet |
| `python-dotenv`    | Load `.env`                                                             |
| `pytest` / `pytest-asyncio` | Dev — tests in `tests/`                                         |


### Why Not Google Sheets?

The market-maker uses Sheets because a human operator frequently adjusts which markets to trade and tweaks hyperparameters. A market-taking bot's config changes rarely — new markets are added occasionally, and thresholds are tuned after analysis, not in real-time. YAML files are simpler, version-controlled, and don't require Google API credentials.

### Why Polling Instead of Pure WebSocket?

Sportsbook odds for futures markets change a few times per day, not sub-second. Polling on scraper-specific intervals (30s to 5min depending on the source) is more than sufficient. Each scraper will run independently on its own schedule once **`core/engine.py`** exists; Phase 2 dry-run triggers scrapers **once per process** without sleeping on `interval`.

On the **Polymarket** side, **Phase 2** uses **REST** order books for best bid/ask. A **Market WebSocket** (with optional local `SortedDict` book) is the natural Phase 3/4 upgrade for fresher prices and depth — not required to close the first live execution milestone.

### Order Type Choice: FOK / FAK over GTC

A GTC order that doesn't immediately fill sits on the order book as a passive limit order. This is problematic because:

1. The fair value may change while the order sits
2. We become a de facto market maker, exposed to adverse selection
3. We'd need to manage order lifecycle (cancel stale orders, etc.)

**FOK** eliminates all of this: fill now or don't. **FAK** fills what it can and cancels the rest — useful when the book is thin. Default **`trade_defaults.order_type`** in config may be **FAK** or **FOK** depending on deployment; both map to `py-clob-client` `OrderType`. If the price we want isn't available, wait for the next scraper cycle.