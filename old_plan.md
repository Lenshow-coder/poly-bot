# Prediction Market Betting Bot — Plan

Goal: Use sportsbook odds as a pricing signal to profit on prediction markets
(Polymarket, Kalshi, etc.), starting with futures/outrights (division winners,
conference winners).

## Phase 1: Pricing Engine

Build the core model that turns raw sportsbook odds into a fair probability.

- Aggregate odds from all 6 sportsbooks (Betano, Bet365, BetMGM, Caesars,
  FanDuel, DraftKings)
- Strip the vig from each book's odds to get implied probabilities
- Derive a fair value estimate (consider sharp-book weighting — Bet365/Pinnacle
  tend to move first and be more accurate than soft books)
- Output: a fair probability for each outcome in each market, updated every
  scrape cycle

## Phase 2: Automated Market Taking

The simpler strategy. Validate the pricing model with real money at low risk.

- Poll sportsbooks every 30-60 seconds (current architecture, parallelized)
- Compare fair value to prediction market prices via their API
- When prediction market price diverges from fair value by more than
  fees + safety buffer, place a bet on the mispriced side
- Run for several weeks to validate:
  - Is the pricing model accurate?
  - How often do mispricings appear?
  - What's the P&L after fees?

Latency requirement: Low. Mispricings on futures markets persist for minutes
to hours. 30-60 second polling is more than sufficient.

## Phase 3: Market Making - Do NOT Implement Yet (much later down the line)

Post offers on both sides of prediction markets, earning the spread.

- Price buy/sell offers based on sportsbook-derived fair value
- Start with wide spreads and small position sizes
- Tighten polling to every 10-15 seconds for faster move detection
- Implement order management:
  - Cancel all open orders the moment ANY sportsbook moves
  - Re-price and re-post after the move settles
  - Use move detection logic: if 1 book moves, be cautious; if 3+ move in
    the same direction, pull offers immediately

Key risk: Adverse selection (getting picked off on stale odds when sportsbooks
move between scrape cycles). Mitigations:
  - Wide spreads absorb small moves you miss
  - Small positions cap downside from getting picked off
  - Multi-book move detection catches changes within 10-15 seconds
  - For futures markets on prediction markets with thin books, this window
    is likely sufficient — this isn't high-frequency trading

## What Matters More Than Scraping Speed

1. Pricing model quality — how you aggregate, remove vig, and weight books
   is where the real edge lives
2. Prediction market API latency — how fast you can cancel/update orders is
   your actual bottleneck, not sportsbook scraping
3. Move detection logic — detecting that a move happened across books matters
   more than shaving milliseconds off individual scrapes
4. Spread and position sizing — the math of how much you earn from spreads
   vs. how much adverse selection costs you

## Why Not WebSockets?

- Futures odds change a few times per day, not sub-second
- Reverse-engineering 6 proprietary WebSocket protocols is weeks of work each
  plus ongoing maintenance
- 10-15 second polling across 6 books gives fast enough move detection
- If adverse selection becomes a measured problem in Phase 3, optimize then
  — don't pre-optimize
- If sub-second live in-play data is ever needed, pay for a third-party odds
  API (The Odds API, OddsJam, etc.) rather than building it

## Suggested Order

1. Finish upgrades.txt items (parallelize, kill nodriver, config-driven markets)
2. Build pricing engine (Phase 1)
3. Integrate prediction market API (read prices, place bets, cancel orders)
4. Run automated market taking (Phase 2) for weeks to validate
5. Graduate to market making (Phase 3) once pricing model is proven
