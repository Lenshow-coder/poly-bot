# Polymarket API Learnings

## API Structure

Three REST APIs (no GraphQL):

| API        | Base URL                              | Purpose                                      |
|------------|---------------------------------------|----------------------------------------------|
| Gamma API  | https://gamma-api.polymarket.com      | Market/event discovery, search, embedded prices |
| CLOB API   | https://clob.polymarket.com           | Orderbook data, real-time pricing, spreads    |
| Data API   | https://data-api.polymarket.com       | User positions, trades, leaderboards          |

For scraping odds, only the Gamma API is needed. It returns prices embedded in market objects.

## Authentication

No authentication needed for reading market data. Auth (Polygon wallet + HMAC) is only required for trading.

## Data Model

- Event: Top-level grouping (e.g. "NHL: Atlantic Division Winner"). Contains one or more markets.
  - Key fields: id, slug, title, active, closed, negRisk, markets[]

- Market: A single binary Yes/No question (e.g. "Will the Buffalo Sabres win the NHL Atlantic Division?").
  Each multi-outcome event has one market per team.
  - Key fields: id, slug, question, conditionId, outcomes, outcomePrices, clobTokenIds,
    groupItemTitle (team name), lastTradePrice, bestBid, bestAsk, active, closed

- Token: Each market has 2 ERC1155 tokens (Yes and No). clobTokenIds = [yes_token_id, no_token_id].

- Neg Risk: Multi-outcome events (like division winners) use negRisk: true.

## Finding Markets

Three approaches:

1. **Search endpoint:**
   `GET https://gamma-api.polymarket.com/public-search?q=NHL+Atlantic+Division+Winner&limit_per_type=10`

2. **Events endpoint with filters:**
   `GET https://gamma-api.polymarket.com/events?active=true&closed=false&tag_id=<nhl_tag_id>&limit=100`

3. **By slug (best for known markets):**
   `GET https://gamma-api.polymarket.com/events/slug/nhl-atlantic-division-winner-747`

## NHL Markets on Polymarket

- NHL: Atlantic Division Winner (slug: nhl-atlantic-division-winner-747)
- NHL: Pacific Division Winner
- NHL: Eastern Conference Champion
- NHL: Western Conference Champion
- NHL: Central Division Winner
- NHL: Metropolitan Division Winner
- 2026 NHL Stanley Cup Champion

## Prices / Odds

Prices are probabilities between 0.00 and 1.00 (price of a share that pays $1.00 if correct).

Key price fields per market object:
- outcomePrices: JSON string array ["yes_price", "no_price"], e.g. ["0.485", "0.515"]
- lastTradePrice: Most recent trade price
- bestBid: Highest bid price
- bestAsk: Lowest ask price
- spread: Bid-ask spread

The displayed price on polymarket.com is the midpoint of the bid-ask spread.
When spread exceeds $0.10, last trade price is shown instead.

Conversions:
- Price IS the implied probability (0.485 = 48.5% chance)
- Decimal odds = 1 / price (0.485 → ~2.06 decimal odds)

## Rate Limits

Sliding 10-second windows, enforced via Cloudflare throttling (delayed, not rejected):

| Endpoint              | Limit (per 10s) |
|-----------------------|------------------|
| Gamma /markets        | 300              |
| Gamma /events         | 500              |
| Gamma /public-search  | 350              |
| Gamma general         | 4,000            |
| CLOB general          | 9,000            |
| CLOB market data      | 500-1,500        |

Very generous for a scraper running every 10 minutes.

## Neg-Risk Markets — Price Gotcha

Division/conference winner markets use negRisk: true. This has a major implication:

- The CLOB `/book` endpoint returns RAW binary orderbooks per team. For neg-risk markets,
  bids show ~0.001 and asks show ~0.999 for most teams. These are NOT the effective prices.
- The Gamma API `bestBid`/`bestAsk` fields ARE correct — they compute effective prices by
  aggregating across all outcomes in the neg-risk group.
- However, the CLOB ask prices DO match Gamma's effective prices at the LOW end of the ask
  book. So you can walk CLOB asks (reversed) to get sizes at meaningful price levels.
- No best ask = 1 - yes best bid (derived from Gamma bestBid).

## CLOB Ask Sort Order

Despite docs saying "ascending", asks are actually sorted DESCENDING by price in the response.
To find the best (lowest) ask, iterate in reverse: `reversed(book["asks"])`.

## Getting Best Ask with Volume (Cumulative Threshold)

Many best asks have very thin liquidity (<10 shares). To get meaningful prices, use a
cumulative volume threshold — walk asks from lowest price up, accumulating size, and return
the price level where cumulative size >= threshold.

```python
MIN_SIZE = 100

def get_best_ask(book, min_size=MIN_SIZE):
    cumulative = 0.0
    for order in reversed(book.get("asks", [])):
        cumulative += float(order["size"])
        if cumulative >= min_size:
            return float(order["price"]), round(cumulative, 2)
    return None, None
```

Example: if asks are 0.50 (size 70), 0.51 (size 70), 0.52 (size 200), with MIN_SIZE=100
the function returns (0.51, 140.0) — not 0.52, because 70+70=140 already meets the threshold.

## Two Output Formats

- test_polymarket.py: Full output with yes_best_ask, no_best_ask, sizes, midpoints, spread,
  volume, liquidity per team. Uses CLOB orderbook for sizes.
- test_polymarket_simple.py: Sportsbook-compatible CSV (timestamp, sport, sportsbook, market,
  team, odds). Only yes_best_ask converted to decimal odds (1/price, rounded to 2 decimals).
  Same cumulative volume threshold logic underneath.

## Volume vs Liquidity

Docs don't formally define these. In practice:
- Volume (volumeNum): cumulative USD traded over market lifetime. Also volume24hr for 24h.
- Liquidity (liquidityNum): total USD of resting orders on the book right now.
- Some markets have None for volumeNum but valid liquidityNum.

## Recommended Approach

1. GET /events/slug/{slug} for each event → returns full event with all markets and prices
2. For prices: use Gamma API bestBid/bestAsk (correct for neg-risk markets)
3. For sizes at price levels: fetch CLOB /book per token, walk asks reversed with cumulative threshold
4. Convert to decimal odds: 1 / price
5. Use curl_cffi — no auth, no proxy, no browser automation needed

Gamma call gets prices in one request per event. CLOB calls needed only for orderbook depth/sizes.

## Geographic Note

US is blocked from trading on Polymarket, but read-only API endpoints appear publicly
accessible regardless of location.

## Python SDK

Package: py-clob-client (pip install py-clob-client)
Not needed — plain curl_cffi requests to Gamma API are simpler and sufficient.
