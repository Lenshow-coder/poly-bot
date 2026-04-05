# Poly-Bot

Market-taking bot for Polymarket. Compares prediction market prices against sportsbook-derived fair values; places directional FOK/FAK bets when edge exceeds threshold. Plugin-based: each market is a YAML config, no Python changes needed. Phases 1–2 done (dry-run pipeline), Phase 3 next (live execution). See `notes/plan.md` for full design doc.

## Commands

- Run: `.venv/Scripts/python.exe main.py --dry-run`
- Tests: `.venv/Scripts/python.exe -m pytest tests/`
- Integration check: `.venv/Scripts/python.exe test_connection.py`
- Package management: `uv` (not pip). Dependencies in `pyproject.toml`.

## Configuration Rule

IMPORTANT: All numeric thresholds, addresses, URLs, and tunable parameters must live in `config.yaml`, never hardcoded in Python source. Add to `config.yaml` first, then read from config in code. Dataclass defaults must match `config.yaml` and be loadable via `from_config()` classmethods.

Hardcoded protocol constants (not user-tunable) exist only in `core/polymarket_client.py`: USDC ABI, `1e6`, `2**256 - 1` max approval, gas limit `100000`, tx timeout `120s`.

## Key Conventions

- **Terminology**: "Event" = market grouping (e.g., Stanley Cup), "Outcome" = team/option with YES/NO tokens, "Token ID" = CLOB identifier. "Market" is used loosely for event.
- CSV outcome names must match Polymarket's `groupItemTitle` exactly for plugin name mapping
- All odds in decimal format (European). Scrapers convert from American/fractional.
- Order types: FOK (default) or FAK (thin liquidity). Never GTC for taking.
- `.env` has secrets only (`PK`, `BROWSER_ADDRESS`). Never commit.

## Polymarket API Gotchas

- **Signature type**: email/social = `1` (POLY_PROXY), MetaMask+Safe = `2` (POLY_GNOSIS_SAFE), EOA = `0`. Wrong type → `invalid signature` on orders but auth succeeds — misleading.
- **`balanceOf` returns $0 for active users**: Deposited funds live in exchange contracts, not wallet.
- **Order size precision**: Tick `0.01` → maker max 2 decimals, taker max 4. Min order $1.
- **WebSocket book snapshot is a JSON array**, not a dict. Check `isinstance(msg, list)`.
- **Free public Polygon RPCs are unreliable**: Use Alchemy.
- **Neg-risk markets**: CLOB raw prices may differ from Gamma effective prices. Use Gamma `bestAsk` to verify edge.

## Before Writing Code

- Run an assumptions audit after each implementation phase
- Verify blockchain addresses and API endpoints against current docs before using them
- When in doubt about Polymarket-specific values, check py-clob-client source or Polymarket docs
