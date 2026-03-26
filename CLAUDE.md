# Poly-Bot Development Guide

## Configuration Rule

All numeric thresholds, addresses, URLs, and tunable parameters must live in `config.yaml`, never hardcoded in Python source. When adding new configurable values, add them to `config.yaml` first, then read them from the config dict in code. If a Python file needs a default value (e.g., dataclass defaults), it must match what's in `config.yaml` and be loadable from config via a `from_config()` classmethod or similar pattern.

### Files that contain configurable values

- **`config.yaml`** — Single source of truth for all configuration. Sections: `polymarket` (API URLs, chain ID, signature type), `polygon` (RPC endpoints, USDC address), `contracts` (exchange, neg-risk exchange, CTF addresses), `engine` (default order type), `logging` (level, console flag). Trading parameters (`TradeParams`, `Signal`) will be added in Phase 2.
- **`core/models.py`** — Data models for Phase 1: `PriceInfo`, `MarketInfo`, `EventInfo`, `OrderResult`, `Position`, `BankrollSnapshot`. Trading-specific models (`TradeParams`, `Signal`) will be added in Phase 2 — when added, their defaults must match `config.yaml` and be loadable via a `from_config()` classmethod.
- **`core/polymarket_client.py`** — Reads all values from config. Contains hardcoded constants that are intrinsic to the protocol (USDC ABI, `1e6` decimal divisor, `2**256 - 1` max approval, gas limit of `100000`, transaction timeout of `120s`). These are not user-tunable but should be documented if changed.
- **`core/utils.py`** — Hardcoded paths: default config file `"config.yaml"`, data directory `"data"`, logs subdirectory `"data/logs"`. Environment variable names: `PK`, `BROWSER_ADDRESS`.
- **`core/state.py`** — Hardcoded state file path `"data/state.json"`, backup suffix `".json.bak"`.
- **`.env`** — Secrets only: `PK` (private key), `BROWSER_ADDRESS` (Safe address). Never commit this file.

## Project Structure

- `main.py` — Entry point
- `core/polymarket_client.py` — REST API client (CLOB, Gamma, Web3). WebSocket will be added in Phase 2.
- `core/models.py` — Data models (Phase 1: prices, markets, orders, positions, state)
- `core/state.py` — JSON state persistence
- `core/utils.py` — Config loading, logging setup, env credentials
- `test_connection.py` — Integration test script
- `config.yaml` — All configuration
- `.env` — Secrets (not committed)

## Key Conventions

- Python virtual environment is at `.venv/` — run via `.venv/Scripts/python.exe`
- Polymarket binary markets always have exactly 2 tokens (YES/NO) per market; multi-outcome events use multiple markets
- USDC on Polygon has 6 decimals (`1e6`)
- Polygon chain ID is `137`
- RPC connection uses fallback list — tries each URL in order until one connects
- WebSocket is not yet integrated into the client; will be added in Phase 2 with exponential backoff reconnection

## Polymarket API Gotchas

- **Signature type depends on account creation method**: email/social login = type `1` (POLY_PROXY), MetaMask/hardware wallet with Gnosis Safe = type `2` (POLY_GNOSIS_SAFE), direct EOA = type `0`. See `py_order_utils.model.signatures` for definitions. Getting this wrong produces `invalid signature` on order placement but auth/API key derivation still succeeds — misleading.
- **USDC balance via `balanceOf` returns $0 for active Polymarket users**: Deposited funds live inside the exchange contracts, not as a raw token balance on the proxy wallet. `balanceOf` only shows un-deposited USDC sitting in the wallet.
- **Order size precision**: Tick size `0.01` means maker amount max 2 decimals, taker amount max 4 decimals. `size * price` must not produce more decimals than allowed. Use whole share counts or validate `size * price` rounds cleanly.
- **Minimum order size is $1**: `size * price` must be >= $1.00 or the CLOB rejects it.
- **Market WebSocket returns a list, not a dict**: The initial book snapshot message is a JSON array of book objects, not a single object. Code must check `isinstance(msg, list)` before calling `.get()`.
- **Free public Polygon RPCs are unreliable**: `polygon-rpc.com`, `ankr.com/polygon`, and `llamarpc.com` all require API keys or are frequently down. Use Alchemy (free tier is more than sufficient).

## Before Writing Code

- Run an assumptions audit after each implementation phase to catch hardcoded values, stale URLs, and misconfigurations
- Verify blockchain addresses and API endpoints against current documentation before using them
- When in doubt about a Polymarket-specific value (address, API shape, token ordering), check against the py-clob-client source or Polymarket docs rather than assuming
