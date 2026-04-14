## Sources

- https://docs.polymarket.com/advanced/neg-risk
- https://github.com/Polymarket/neg-risk-ctf-adapter

---

## Contract Addresses (Polygon mainnet, chain 137)

| Contract               | Address                                      |
|------------------------|----------------------------------------------|
| Neg Risk Adapter       | `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296` |
| Neg Risk CTF Exchange  | `0xC5d563A36AE78145C45a50134d48A1215220f80a` |
| CTF Exchange           | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` |
| Conditional Tokens     | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` |
| USDC.e                 | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` |

---

## How the Neg-Risk Adapter Works

### Problem it solves

In a multi-outcome event (e.g., "Who wins the Stanley Cup?" with 16 teams), each outcome is a separate binary market with YES/NO tokens. Without the adapter, buying NO on Team A has no relationship to the other outcomes. The adapter lets you **convert NO positions into YES positions for the complementary outcomes**, reflecting the economic reality that "NOT Team A" = "one of Team B, C, D, ..." wins.

### Core operations

**1. splitPosition** — Deposit USDC, receive 1 YES + 1 NO token for a given question.
- USDC is wrapped into WrappedCollateral (wcol) under the hood.
- Both YES and NO tokens are ERC-1155 on the Conditional Tokens contract.

**2. mergePositions** — Return 1 YES + 1 NO token, receive 1 USDC back.
- Reverse of split. Unwraps wcol back to USDC.

**3. convertPositions** — The key innovation. Convert NO tokens into YES tokens for the other outcomes + collateral.

**4. redeemPositions** — After resolution, redeem winning tokens for USDC.

---

## convertPositions — Deep Dive

### Signature

```solidity
function convertPositions(
    bytes32 _marketId,    // market identifier
    uint256 _indexSet,    // bitmask: which questions' NO tokens you're converting
    uint256 _amount       // how many of each NO token to convert
) external
```

### indexSet bitmask encoding

Each bit position represents a question index. Set bit = "I'm providing this question's NO token."

Example: 3-outcome market (questions 0, 1, 2). `_indexSet = 0b011 = 3` means "convert NO tokens for questions 0 and 1."

### What you get back

For a market with `n` questions, converting `_amount` of `m` NO tokens (m bits set in indexSet):
- **Collateral returned**: `_amount * (m - 1)` USDC (minus fees)
- **YES tokens received**: `_amount` of each complementary YES token (the `n - m` questions whose bits are NOT set)

### Worked example

3-outcome market (A, B, C). You hold 100 NO-A and 100 NO-B.

`convertPositions(marketId, 0b011, 100)` →
- Burns: 100 NO-A + 100 NO-B
- Returns: **100 USDC** (100 × (2-1)) + **100 YES-C**
- (minus any fee)

### Value equivalence

This works because in a mutually-exclusive market where exactly one outcome wins:
- Holding 1 NO-A AND 1 NO-B is worth exactly the same as 1 USDC + 1 YES-C
- If C wins → NO-A=1, NO-B=1, total=2. Converted: USDC=1, YES-C=1, total=2. ✓
- If A wins → NO-A=0, NO-B=1, total=1. Converted: USDC=1, YES-C=0, total=1. ✓
- If B wins → NO-A=1, NO-B=0, total=1. Converted: USDC=1, YES-C=0, total=1. ✓

**Caveat**: If ALL questions resolve false (e.g., augmented neg-risk "Other" wins), the converted position is worth LESS than the original.

### Mechanical steps inside the contract

1. Mint `(n-m) * _amount` of WrappedCollateral
2. For each question NOT in the indexSet: split wcol into YES+NO tokens
3. Burn all provided NO tokens (sent to unrecoverable burn address)
4. Burn the newly-created NO tokens from the splits
5. Deduct fee (feeBips / 10000) from output amounts
6. Send fees to vault
7. Send remaining YES tokens + USDC to caller

---

## Fee structure

- Each market has an optional `feeBips` parameter (basis points, denominator 10000).
- Fee is deducted from both the collateral payout and the YES token amounts.
- Fees go to the vault contract.

---

## How positions show in your account

- YES and NO tokens are ERC-1155 tokens on the Conditional Tokens contract (`0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`).
- After conversion, you hold standard YES tokens — they are **indistinguishable** from YES tokens bought directly on the CLOB.
- The Neg Risk CTF Exchange (`0xC5d563A36AE78145C45a50134d48A1215220f80a`) is used for trading these tokens — it's a fork of the standard CTF Exchange but wired to the NegRiskAdapter.
- Positions show up as YES token balances in your wallet / Polymarket UI the same way any other YES position would.
- The USDC returned from conversion goes back to your wallet (or exchange balance if trading through the exchange).

---

## Pricing relationship

- If YES-A costs $0.40 on the CLOB, then NO-A effectively costs $0.60.
- Converting that NO-A (with NO-B, NO-C, etc.) gives you YES tokens for the other outcomes.
- The arbitrage-enforced relationship: sum of all YES prices across outcomes ≈ $1.00 (minus vig/spread).
- So buying NO on one outcome at $0.60 and converting is economically equivalent to buying YES on all other outcomes, which should cost ~$0.40 total across them.

---

## CLOB integration (what we actually need)

For our bot, we do NOT call `convertPositions` on-chain ourselves. The Neg Risk CTF Exchange handles this transparently:

- When placing orders via the CLOB API, set `negRisk: true` in order options.
- The exchange uses the NegRiskAdapter under the hood.
- Buy YES → you get YES tokens directly.
- Buy NO → under the hood, the exchange may use the adapter for settlement.
- Our py-clob-client already supports this with the `negRisk` flag.

```python
# Example from py-clob-client
response = client.create_and_post_order(
    OrderArgs(
        token_id="TOKEN_ID",
        price=0.50,
        size=100,
        side=BUY,
    ),
    options=CreateOrderOptions(
        tick_size="0.01",
        neg_risk=True,  # Required for neg-risk markets
    ),
)
```

---

## Key gotchas for implementation

1. **Must pass `neg_risk=True`** when placing orders on neg-risk markets — wrong flag → order rejection or wrong settlement.
2. **Augmented neg-risk**: Only trade named outcomes. Ignore placeholder outcomes.
3. **"Other" outcome** in augmented markets changes definition as placeholders get named — avoid trading it.
4. **If all questions resolve false** (Other wins), converted positions lose value vs. original NO positions.
5. **Token IDs differ** between the standard CTF Exchange and the Neg Risk CTF Exchange — use the correct exchange address.
6. **Oracle integration**: Resolution goes through UmaCtfAdapter, deployed with its `ctf` set to the NegRiskAdapter address (not the raw CTF).

---

## Convert script usage

### List available outcomes

```bash
.venv/Scripts/python.exe neg-risk-adapter/convert_positions.py \
    --event-slug 2026-nhl-stanley-cup-champion --list
                 2026-nba-champion
```

Shows every outcome with its question index and current YES ask price.

### Convert one NO type (YES tokens only, no USDC back)

```bash
# Dry run first
.venv/Scripts/python.exe neg-risk-adapter/convert_positions.py \
    --event-slug 2026-nhl-stanley-cup-champion \
    --outcomes "Carolina Hurricanes" \
    --amount 32 --dry-run

# For real
.venv/Scripts/python.exe neg-risk-adapter/convert_positions.py \
    --event-slug 2026-nhl-stanley-cup-champion \
    --outcomes "Carolina Hurricanes" \
    --amount 32
```

Burns 32 NO-Carolina, gives you 32 YES for each of the other 31 teams. Zero USDC back.

### Convert multiple NO types (YES tokens + USDC back)

```bash
.venv/Scripts/python.exe neg-risk-adapter/convert_positions.py \
    --event-slug 2026-nhl-stanley-cup-champion \
    --outcomes "Carolina Hurricanes" "Dallas Stars" "Florida Panthers" \
    --amount 32 --dry-run
```

Burns 100 of each (NO-Carolina, NO-Dallas, NO-Florida). Gives you:
- **200 USDC** (100 × (3-1))
- **100 YES** for each of the other 29 teams

### Convert full balance (omit `--amount`)

```bash
.venv/Scripts/python.exe neg-risk-adapter/convert_positions.py \
    --event-slug 2026-nhl-stanley-cup-champion \
    --outcomes "Carolina Hurricanes"
```

Uses your entire NO balance. If converting multiple outcomes with different balances, it uses the smallest balance and warns you.

---

## Gotchas learned from testing

- **Gas is massive**: A 30-outcome conversion uses ~5M gas units (~$0.05 on Polygon). The script estimates gas dynamically — never hardcode a gas limit for this.
- **Proxy wallet**: POLY_PROXY users (signature_type=1) must route through the Proxy Wallet Factory (`0xaB45...`). The EOA signs the tx, the factory forwards through the proxy, so the proxy is `msg.sender` to the adapter. The script handles this automatically.
- **EOA needs POL**: The EOA (derived from PK, not BROWSER_ADDRESS) pays gas. Send POL to the EOA address, not the proxy.
- **Amount is in display units**: `--amount 32` means 32 tokens, not raw 6-decimal units. The script multiplies by 1e6 internally.
