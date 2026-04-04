# Phase 1: Foundation — Implementation Summary

11 files created. All imports verified, `uv sync` installs 64 packages cleanly, state manager round-trip tested.

## Files

| # | File | Purpose |
|---|------|---------|
| 1 | `pyproject.toml` | Project config with all 7 deps + dev deps |
| 2 | `.env.example` | Documents `PK` and `BROWSER_ADDRESS` secrets |
| 3 | `.gitignore` | Excludes `.env`, `data/`, `__pycache__/`, `.venv/` |
| 4 | `config.yaml` | Polymarket URLs, chain_id, USDC address, engine params |
| 5 | `core/__init__.py` | Package marker |
| 6 | `core/models.py` | 6 dataclasses: `PriceInfo`, `MarketInfo`, `EventInfo`, `OrderResult`, `Position`, `BankrollSnapshot` |
| 7 | `core/utils.py` | `setup_logging`, `load_config`, `load_env_credentials`, `ensure_data_dir` |
| 8 | `core/state.py` | `StateManager` with atomic JSON save, corruption backup, datetime serialization |
| 9 | `core/polymarket_client.py` | REST client: CLOB auth, Gamma event fetch, order book (ask sort fix), prices, wallet + exchange balance, order placement, contract approvals, positions. RPC fallback across multiple endpoints. |
| 10 | `main.py` | Minimal entry point |
| 11 | `test_connection.py` | 8-step verification with `--place-order`, `--fill-order`, and `--ws-test` flags |

## Phase 1 Verification Checklist

Add `.env` with `PK` and `BROWSER_ADDRESS`, then run the tests below. All must pass before starting Phase 2.

### 1. Client Init + Event Fetch + Order Book + Balance + State (Steps 1-5)

```
.venv/Scripts/python.exe test_connection.py --slug 2026-nhl-stanley-cup-champion
```

Verify:
- **Step 1:** Client initializes, API credentials derived. Logs show which RPC endpoint connected (fallback working).
- **Step 2:** Event title prints, `neg_risk=True`, multiple markets returned. Each market has YES/NO token IDs. Markets with != 2 tokens are skipped with a warning.
- **Step 3:** Order book shows bid/ask levels. Best bid, best ask, and midpoint are non-None and reasonable.
- **Step 4:** Shows wallet balance (un-deposited USDC) and exchange balance (available for trading). Exchange balance should match what you see on Polymarket.
- **Step 5:** State save/load round-trip passes assertions.

### 2. WebSocket (Step 8)

```
.venv/Scripts/python.exe test_connection.py --slug 2026-nhl-stanley-cup-champion --ws-test
```

Verify:
- Connects and subscribes to market tokens.
- Receives `book` snapshot and/or `price_change` messages within 30s.
- If no messages, the market may be inactive — try a more active event slug.

### 3. Order Placement — Safe (Step 6, no cost)

```
.venv/Scripts/python.exe test_connection.py --slug 2026-nhl-stanley-cup-champion --place-order
```

Verify:
- Places a $1 FOK BUY 20 cents below best ask.
- Order is NOT filled (status shows no fill). This confirms auth + order signing works without spending money.

### 4. Order Placement — Live (Step 7, costs ~$1)

```
.venv/Scripts/python.exe test_connection.py --slug 2026-nhl-stanley-cup-champion --fill-order
```

Verify:
- Places a $1 FOK BUY at best ask.
- Order fills. Confirms the full execution path works end-to-end.

### 5. Contract Approvals (manual, one-time)

Only needed if you haven't approved contracts for this wallet before. Call `client.approve_contracts()` from a Python shell or add a `--approve` flag. Verify transactions succeed on Polygonscan. Only needs to be done once per wallet.

### What Must Work Before Phase 2

Phase 2 (scraper interface, NHL plugin, fair value computation, signal generation) depends on:

| Dependency | Verified By | Required |
|---|---|---|
| `PolymarketClient` initializes and authenticates | Step 1 | Yes |
| Gamma API returns events with token IDs | Step 2 | Yes |
| CLOB order book returns bid/ask prices | Step 3 | Yes |
| Exchange balance reads correctly via CLOB API | Step 4 | Yes |
| State persistence round-trips cleanly | Step 5 | Yes |
| WebSocket receives real-time price updates | Step 8 | Yes |
| Order placement works (auth + signing) | Step 6 | Yes |
| Order fills execute successfully | Step 7 | Recommended |
| Contract approvals completed | Manual | Before live trading |

---

**Next:** [Phase 2 — Scraper interface, plugins, signals, dry-run](ph_2_implementation.md).
