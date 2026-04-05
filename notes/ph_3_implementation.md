# Phase 3: Execution, Risk, and Monitoring - Implementation Plan

**Prerequisite:** [Phase 2 - Scraper + Plugin + Signal Pipeline](ph_2_implementation.md)

**Source of truth:** `notes/plan.md` (up to date) and the current codebase state in `main.py`, `core/signal.py`, `core/polymarket_client.py`, `core/state.py`, `markets/*`, and `config.yaml`.

## Goal

Move from single-pass dry-run signal logging to a long-running live bot that:

1. Runs each scraper continuously on its own interval
2. Applies risk gates before every trade
3. Executes approved signals using FOK/FAK via existing `PolymarketClient.place_order`
4. Tracks positions/bankroll with periodic API reconciliation
5. Produces operational logs (including CSV trade log) and optional notifications

Phase 3 closes the first live execution milestone. WebSocket price cache is optional in this phase and can be deferred if it risks schedule.

---

## Current Baseline (Verified)

The repo already has the key Phase 2 foundations:

- `main.py` supports `--dry-run`, loads plugins/scrapers, runs one async `dry_run_cycle`, and exits in live mode with "not yet implemented."
- `core/signal.py` implements:
  - `kelly_bet_size()`
  - `evaluate_signals()` with `min_sources`, `price_range`, `sportsbook_buffer`
  - SELL signal emission with `size_usd=0` placeholder for Phase 3 sizing
  - `check_exits()` filtering SELLs to held token IDs
- `core/polymarket_client.py` already supports:
  - Gamma event/outcome discovery
  - CLOB REST book + `get_prices()`
  - `place_order()` with FOK/FAK mapping to `OrderType`
  - `get_exchange_balance()`, `get_usdc_balance()`, `get_positions()`
- `core/state.py` already persists `bankroll`, `positions`, and `cooldowns` atomically in `data/state.json`.
- `config.yaml` already contains:
  - `risk.*` (kelly bankroll, event/portfolio caps, min balance/bankroll)
  - `trade_defaults.*` including cooldown/order type/source/price filters
  - scraper definitions with per-scraper `interval`

What is missing is orchestration and enforcement: `engine`, `risk_manager`, `executor`, `position_tracker`, trade CSV, notifier, and live-mode entry wiring.

---

## Scope for Phase 3

### In Scope (must deliver)

1. `core/engine.py` long-running async orchestrator
2. `core/risk_manager.py` trade approval + optional size clipping
3. `core/executor.py` signal-to-order mapping and execution result handling
4. `core/position_tracker.py` local positions + API reconciliation + cooldown tracking
5. CSV trade log writer (`data/trades.csv`) and structured execution logging
6. `main.py` live-mode path into engine
7. Unit tests for risk, executor, and position tracker plus integration-smoke tests

### Optional in Phase 3 (only if low-risk after core path is stable)

1. `core/notifier.py` (Telegram, non-blocking)
2. WS price cache integration in client/engine (REST remains fallback)

### Explicitly Out of Scope

1. Phase 4 hardening (reconnect/backoff deepening, long soak production tuning)
2. New market/plugin types
3. Strategy redesign beyond existing signal math

---

## Deliverables by File

| File | Action | Purpose |
|---|---|---|
| `core/engine.py` | Create | Main async runtime: scraper loops, pipeline routing, background sync |
| `core/risk_manager.py` | Create | Enforce exposure/cooldown/balance/bankroll gates |
| `core/executor.py` | Create | Execute BUY/SELL signals via `PolymarketClient.place_order` |
| `core/position_tracker.py` | Create | Position state, fill application, bankroll snapshots, reconciliation |
| `core/notifier.py` | Create (optional) | Non-blocking alerts for fills/failures/critical warnings |
| `core/models.py` | Modify | Add `ExecutionResult` and optional `RiskDecision` dataclasses |
| `core/state.py` | Modify (minor) | Keep compatibility with richer position fields, if needed |
| `main.py` | Modify | Start `Engine` in live mode; keep dry-run behavior |
| `config.yaml` | Modify | Add engine/runtime/logging/notifier knobs (all configurable) |
| `tests/test_risk_manager.py` | Create | Unit tests for risk gates and cooldown logic |
| `tests/test_executor.py` | Create | Unit tests for sizing, price/side mapping, fill/reject handling |
| `tests/test_position_tracker.py` | Create | Position updates, avg cost, sell reduction, reconciliation |
| `tests/test_engine.py` | Create | Pipeline smoke with mocked components |

---

## Architecture for Phase 3

### 1) Engine (`core/engine.py`)

Responsibilities:

1. Initialize runtime components from config and existing loaders
2. Create one async task per scraper (`while True` + `await asyncio.sleep(scraper.interval)`)
3. For each scrape result:
   - route to relevant plugins
   - compute fair values
   - fetch prices
   - evaluate BUY/SELL signals
   - run risk checks
   - execute approved signals (or dry-run log)
4. Run periodic reconciliation task (`position_sync_interval`) to sync positions/balance
5. Handle graceful shutdown (cancel tasks, final state save)

Target public interface:

```python
class Engine:
    def __init__(self, config: dict, client: PolymarketClient, plugins: list, scrapers: list): ...
    async def run_forever(self) -> None: ...
```

Implementation notes:

- Use `asyncio.TaskGroup` (Py3.11) or explicit task tracking.
- Each scraper loop catches and logs exceptions so one bad scraper does not stop the process.
- Risk/execution errors are isolated per-signal; never crash the engine loop for one failure.
- Keep REST price fetch path as default.
- If WS cache is enabled later, `client.get_prices()` can read cache first then REST fallback.

### 2) Position Tracker (`core/position_tracker.py`)

Responsibilities:

1. Maintain in-memory positions keyed by `token_id`
2. Apply fill updates from executor responses
3. Track cooldown timestamps by token
4. Compute exposure views used by risk manager:
   - outcome exposure
   - event exposure
   - portfolio exposure
5. Track bankroll snapshot:
   - exchange/wallet cash source policy (documented and configurable)
   - mark-to-market position value
6. Reconcile local state with `client.get_positions()` periodically
7. Persist through `StateManager`

Recommended approach:

- Treat `get_exchange_balance()` as primary available trading cash for risk checks.
- Keep `get_usdc_balance()` for diagnostics/monitoring.
- Store position `event_name` and `outcome_name` from signal context when opening.

### 3) Risk Manager (`core/risk_manager.py`)

Responsibilities:

1. Gate BUY/SELL signals before execution
2. Enforce:
   - `trade_params.max_outcome_exposure`
   - `risk.max_event_exposure`
   - `risk.max_portfolio_exposure`
   - `risk.min_balance`
   - `risk.min_bankroll`
   - `trade_params.cooldown_minutes`
3. SELL policy:
   - reject if no held position
   - size SELL from current held shares/position value (not from Kelly)
4. Optional behavior: clip BUY size down to allowed remaining cap instead of full reject

Decision shape:

```python
@dataclass
class RiskDecision:
    approved: bool
    adjusted_size_usd: float
    reason: str
```

### 4) Executor (`core/executor.py`)

Responsibilities:

1. Convert signal USD size to shares for BUY:
   - `shares = size_usd / limit_price`
2. Determine SELL shares from tracker holdings (or risk-adjusted size)
3. Enforce execution preconditions:
   - limit price exists (`max_price` for BUY, `market_price`/`min_price` policy for SELL)
   - minimum notional (`shares * price >= 1`)
   - sane precision before submit
4. Call `client.place_order(...)`
5. Return normalized execution result and update tracker on fills
6. Append one CSV row per attempt/outcome

Order type source:

- Use `signal`/plugin `trade_params.order_type` (FOK/FAK), fallback to config default.

Execution simplification for initial Phase 3:

- First live milestone can skip full depth walk and rely on FOK/FAK result from venue.
- Add optional pre-check depth walk behind config flag (`engine.precheck_liquidity`) later.

### 5) Logging and CSV

Add a lightweight writer for `data/trades.csv` with schema aligned to `plan.md`:

`timestamp,event,outcome,token_id,side,shares,price,usd,edge_pct,fair_value,kelly_usd,sources,odds_scrape_ts,odds_fanduel,odds_draftkings,odds_betmgm,odds_betrivers,odds_bet365,odds_caesars,odds_thescore,odds_ozoon,odds_bol,odds_betano,odds_pinnacle,order_type,status,order_id,reason`

Fixed sportsbook columns: `odds_fanduel`, `odds_draftkings`, `odds_betmgm`, `odds_betrivers`, `odds_bet365`, `odds_caesars`, `odds_thescore`, `odds_ozoon`, `odds_bol`, `odds_betano`, `odds_pinnacle`. Values are decimal odds from each book for this outcome (empty if book didn't cover it). Source: raw `BookOdds.odds` values keyed by book name. `odds_scrape_ts` is the timestamp of the scrape cycle that produced the odds (CSV last-modified or explicit scraper timestamp); diff against `timestamp` to measure scrape-to-trade latency.

Rules:

- Always log attempted executions (approved + rejected by venue), not only fills.
- Include risk-rejection logs at INFO/WARNING with structured reason strings.

### 6) Notifier (Optional)

`core/notifier.py` can be introduced once core execution is stable:

- Async `send(message, priority="normal")`
- Non-blocking and failure-tolerant (never interrupt engine)
- Controlled by `config.yaml` + env vars

---

## Config Changes (`config.yaml`)

All thresholds/intervals/tunable behavior must live in config (no hardcoded runtime knobs).

Add or extend:

```yaml
engine:
  dry_run: false
  position_sync_interval: 60
  loop_error_backoff_seconds: 5
  precheck_liquidity: false
  trade_log_path: "data/trades.csv"
  ws_enabled: false
  ws_ping_interval: 20

risk:
  kelly_bankroll: 2000
  max_event_exposure: 200
  max_portfolio_exposure: 500
  min_balance: 50
  min_bankroll: 200
  reconcile_tolerance_pct: 0.05
  size_clip_enabled: true

notifier:  # optional if implemented in Phase 3
  enabled: false
  provider: "telegram"
  min_level: "WARNING"
```

Env vars (only if notifier lands):

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

---

## Step-by-Step Implementation Sequence

### Step 1: Extend Models for Execution and Risk Outcomes

File: `core/models.py`

Add:

1. `ExecutionResult` dataclass (status, requested/filled shares, avg fill price, order id, reason)
2. `RiskDecision` dataclass (approved, adjusted size, reason)

Keep existing `Signal` unchanged except optional helper methods if needed.

### Step 2: Build Position Tracker

File: `core/position_tracker.py`

Implement:

1. State load/save integration with `StateManager`
2. `apply_fill(...)` for BUY/SELL updates and average cost math
3. `get_position(token_id)`, `get_event_exposure(event_name)`, `get_total_exposure()`
4. `is_on_cooldown(token_id)` and `mark_traded(token_id, cooldown_minutes)`
5. `sync_from_api(client.get_positions())` reconciliation routine
6. `snapshot_bankroll(...)` utility

### Step 3: Implement Risk Manager

File: `core/risk_manager.py`

Implement `approve(signal, tracker, trade_params) -> RiskDecision` with:

1. Balance/bankroll emergency checks first
2. Cooldown checks
3. Side-specific sizing logic:
   - BUY: from signal Kelly size, optionally clipped
   - SELL: derive from held position (full or clipped)
4. Exposure checks at outcome/event/portfolio levels
5. Clear rejection reasons for observability

### Step 4: Implement Executor

File: `core/executor.py`

Implement:

1. `execute(signal, trade_params, tracker) -> ExecutionResult`
2. USD to shares conversion and validation
3. `client.place_order(...)` call (sync or `asyncio.to_thread`)
4. Post-trade handling:
   - apply fill to tracker
   - persist state
   - append CSV row
   - return normalized result

### Step 5: Implement Engine Runtime

File: `core/engine.py`

Implement:

1. Startup wiring for tracker/risk/executor
2. `process_scraper_result(...)` pipeline
3. `scraper_loop(scraper)` per interval
4. `reconcile_loop()` background task
5. Shutdown hooks and final state flush

Dry-run in engine:

- If `engine.dry_run` true, still run full pipeline including risk decisions, but skip `place_order` and log "would execute".

### Step 6: Wire Live Path in Main

File: `main.py`

Changes:

1. Keep current dry-run single cycle available for quick diagnostics if desired
2. For live mode, instantiate `Engine` and run `asyncio.run(engine.run_forever())`
3. Ensure startup logging prints mode and enabled markets/scrapers clearly

### Step 7: Optional Notifier

File: `core/notifier.py` (+ engine hooks)

Only after core flow passes tests:

1. Implement provider client
2. Send on fills/rejections/critical failures
3. Wrap all send calls in try/except and never raise to caller

---

## Testing Plan

### Unit Tests (new)

1. `tests/test_risk_manager.py`
   - outcome cap pass/fail
   - event/portfolio cap pass/fail
   - cooldown rejection
   - min balance/min bankroll pause
   - SELL rejected without holdings
   - size clipping behavior when enabled

2. `tests/test_executor.py`
   - BUY USD->shares conversion
   - SELL uses held size
   - invalid price/notional blocked before placement
   - FOK/FAK mapping
   - filled/partial/rejected response handling
   - tracker update called only on fill

3. `tests/test_position_tracker.py`
   - average cost after multiple BUY fills
   - SELL reduces size and realizes pnl
   - zeroed position cleanup policy
   - cooldown lifecycle
   - reconciliation delta handling

4. `tests/test_engine.py`
   - scraper result routes to matching plugin
   - risk rejection prevents executor call
   - approved signal calls executor
   - scraper exception isolation

### Regression Tests (existing)

Run existing Phase 2 suite:

- `tests/test_fair_value.py`
- `tests/test_signal.py`
- `tests/test_sportsbook_signal.py`

### Integration / Manual Verification

1. Dry-run engine loop with real CSV:
   - confirms recurring intervals
   - confirms risk decisions are logged
2. Live test with small size (`$5-$10`) in one market:
   - one BUY fill path end-to-end
   - one SELL path from held position
3. Verify:
   - `data/state.json` updates
   - `data/trades.csv` rows append
   - exposure values and cooldown enforcement behave as configured

---

## Rollout Plan

### Milestone A: Internal readiness

1. New tests green
2. Existing tests green
3. Dry-run loop stable for 2-4 hours

### Milestone B: Controlled live canary

1. Single market only (`enabled_markets` minimal)
2. Low caps (`max_outcome_exposure`, `max_event_exposure`, `max_portfolio_exposure`)
3. Min trade size and strict threshold unchanged
4. Observe fills/rejections and reconciliation logs

### Milestone C: Full Phase 3 complete

1. Expand runtime duration
2. Enable optional notifier (if implemented)
3. Decide whether WS cache belongs in Phase 3 close or deferred to Phase 4

---

## Risks and Mitigations

1. **Incorrect SELL sizing from placeholder signals**
   - Mitigation: compute SELL size only from tracker holdings inside risk/executor.
2. **State drift between local fills and API truth**
   - Mitigation: periodic reconciliation loop with tolerance and explicit warning logs.
3. **Order response parsing variability**
   - Mitigation: normalize response in executor and test dict/object forms.
4. **Loop fragility from scraper or API exceptions**
   - Mitigation: per-task exception boundaries, backoff, never fail entire engine.
5. **Over-trading due to fast repeat signals**
   - Mitigation: enforce cooldown at risk layer and persist cooldowns in state.

---

## Acceptance Criteria for Phase 3

Phase 3 is complete when all are true:

1. `main.py` live mode starts a persistent engine loop.
2. Each scraper runs repeatedly on configured interval.
3. Every candidate signal is risk-approved or risk-rejected with explicit reason.
4. Approved signals execute through `place_order` and return normalized execution results.
5. Position/cooldown/bankroll state persists and survives restart.
6. `data/trades.csv` captures execution attempts with status.
7. Unit + regression tests pass.
8. At least one controlled live buy and sell path verified with small size.

---

## Suggested Build Order (Practical)

If implemented in focused PRs, use:

1. PR1: models + position tracker + tests
2. PR2: risk manager + tests
3. PR3: executor + csv logging + tests
4. PR4: engine + main wiring + engine tests
5. PR5 (optional): notifier and WS cache hooks

This keeps each change reviewable and reduces live-trading risk.
