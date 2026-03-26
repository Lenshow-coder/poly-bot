# Poly-Maker Bot

A market making bot for Polymarket prediction markets. Automates liquidity provision by maintaining orders on both sides of the book.

## Project Structure

```
polymarket_bot/
└── poly-maker/           # Main project directory
    ├── main.py           # Entry point - async event loop, websocket connections
    ├── trading.py        # Trading logic
    ├── update_markets.py # Fetches/updates market data (run separately, different IP)
    ├── update_stats.py   # Updates account statistics
    ├── poly_data/        # Core module - market making logic, state, API client
    │   ├── polymarket_client.py  # Polymarket API client
    │   ├── websocket_handlers.py # Market and user websocket handlers
    │   ├── data_utils.py         # update_markets/positions/orders
    │   ├── data_processing.py    # Trade processing logic
    │   ├── global_state.py       # Shared state (client, df, orders, positions, etc.)
    │   ├── trading_utils.py      # Trading utilities
    │   ├── utils.py              # General utilities
    │   └── CONSTANTS.py          # Constants
    ├── poly_merger/      # Node.js utility for merging positions
    ├── poly_stats/       # Account statistics tracking
    ├── poly_utils/       # Shared utilities (google_utils)
    └── data_updater/     # Market data collection (separate repo included for convenience)
```

## Tech Stack

- **Language**: Python 3.9.10+, Node.js (for poly_merger)
- **Package manager**: `uv` (use `uv run python ...`, not plain `python`)
- **Key deps**: `py-clob-client`, `web3`, `websockets`, `pandas`, `gspread`, `eth-account`
- **Formatter**: `black` (line length 100, target py39)

## Commands

```bash
# Install dependencies
uv sync

# Run the market maker
uv run python main.py

# Update market data (run continuously on separate IP)
uv run python update_markets.py

# Update stats
uv run python update_stats.py
```

## Environment

Copy `.env.example` to `.env` and set:
- `PK` - Polymarket wallet private key
- `BROWSER_ADDRESS` - Wallet address
- `SPREADSHEET_URL` - Google Sheets URL for config/data

## Architecture Notes

- `global_state.py` holds shared mutable state (client, df, orders, positions, performing, all_tokens)
- `main.py` runs two concurrent websockets (`connect_market_websocket`, `connect_user_websocket`) via `asyncio.gather`
- A background thread (`update_periodically`) refreshes positions/orders every 5s, markets every 30s
- `performing` dict tracks in-flight trades; stale entries (>15s) are cleaned up automatically
- Google Sheets is the configuration source for selected markets and hyperparameters


## Important Rules
- Do NOT execute anything in this folder - I am just reviewing how this tool works but don't yet trust its security.
- Use Claude Opus 4.6 as the default model.