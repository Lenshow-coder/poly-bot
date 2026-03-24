"""
Standalone verification script for Phase 1 connectivity.

Usage:
    python test_connection.py --slug <event-slug>
    python test_connection.py --slug <event-slug> --place-order
    python test_connection.py --slug <event-slug> --fill-order
"""

import argparse
import json
import sys
import tempfile

from core.utils import setup_logging, load_config, ensure_data_dir
from core.polymarket_client import PolymarketClient
from core.state import StateManager
from core.models import BankrollSnapshot, Position
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser(description="Test Polymarket connectivity")
    parser.add_argument("--slug", required=True, help="Event slug to fetch")
    parser.add_argument(
        "--place-order",
        action="store_true",
        help="Place a $1 FOK BUY well below best ask (should NOT fill)",
    )
    parser.add_argument(
        "--fill-order",
        action="store_true",
        help="Place a $1 FOK BUY at best ask (WILL fill — costs real money)",
    )
    args = parser.parse_args()

    config = load_config()
    logger = setup_logging(level="DEBUG", console=True)
    ensure_data_dir()

    # ── Step 1: Init client (verifies auth) ──────────────────────────
    print("\n=== Step 1: Initialize PolymarketClient ===")
    try:
        client = PolymarketClient(config)
        print("  [OK] Client initialized, API credentials derived")
    except Exception as e:
        print(f"  [FAIL] {e}")
        sys.exit(1)

    # ── Step 2: Fetch event via Gamma API ────────────────────────────
    print(f"\n=== Step 2: Fetch event '{args.slug}' ===")
    try:
        event = client.get_event(args.slug)
        print(f"  Title: {event.title}")
        print(f"  Neg-risk: {event.neg_risk}")
        print(f"  Markets: {len(event.markets)}")
        for m in event.markets[:5]:
            bid_str = f"{m.best_bid:.3f}" if m.best_bid else "N/A"
            ask_str = f"{m.best_ask:.3f}" if m.best_ask else "N/A"
            print(f"    {m.outcome_name}: bid={bid_str} ask={ask_str} yes_token={m.yes_token_id[:12]}...")
        if len(event.markets) > 5:
            print(f"    ... and {len(event.markets) - 5} more")
    except Exception as e:
        print(f"  [FAIL] {e}")
        sys.exit(1)

    # ── Step 3: Fetch CLOB order book ────────────────────────────────
    first_market = event.markets[0] if event.markets else None
    prices = None
    if first_market:
        print(f"\n=== Step 3: CLOB order book for '{first_market.outcome_name}' ===")
        try:
            book = client.get_order_book(first_market.yes_token_id)

            # Handle both object and dict formats
            bids = getattr(book, "bids", None) or (book.get("bids") if isinstance(book, dict) else []) or []
            asks = getattr(book, "asks", None) or (book.get("asks") if isinstance(book, dict) else []) or []

            print(f"  Bids: {len(bids)} levels")
            for b in bids[:3]:
                p = b.price if hasattr(b, "price") else b["price"]
                s = b.size if hasattr(b, "size") else b["size"]
                print(f"    {p} x {s}")

            print(f"  Asks: {len(asks)} levels (sorted ascending)")
            for a in asks[:3]:
                p = a.price if hasattr(a, "price") else a["price"]
                s = a.size if hasattr(a, "size") else a["size"]
                print(f"    {p} x {s}")

            prices = client.get_prices(first_market.yes_token_id)
            print(f"  Best bid: {prices.best_bid}, Best ask: {prices.best_ask}, Mid: {prices.midpoint}")
        except Exception as e:
            print(f"  [FAIL] {e}")

    # ── Step 4: USDC balance ─────────────────────────────────────────
    print("\n=== Step 4: USDC Balance ===")
    try:
        balance = client.get_usdc_balance()
        print(f"  Balance: ${balance:.2f}")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ── Step 5: State round-trip test ────────────────────────────────
    print("\n=== Step 5: State Manager round-trip ===")
    try:
        tmp_dir = tempfile.mkdtemp(prefix="polybot_test_")
        sm = StateManager(state_dir=tmp_dir)

        test_bankroll = BankrollSnapshot(
            usdc_balance=100.0,
            positions_value=50.0,
            total_bankroll=150.0,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        test_positions = [
            Position(
                token_id="test_token",
                outcome_name="Test Team",
                market_name="Test Market",
                side="BUY",
                size=10.0,
                avg_cost=0.45,
                current_price=0.50,
            )
        ]

        sm.save(bankroll=test_bankroll, positions=test_positions, cooldowns={"test": "2024-01-01T00:00:00"})
        loaded = sm.load()

        assert loaded["bankroll"].usdc_balance == 100.0
        assert len(loaded["positions"]) == 1
        assert loaded["positions"][0].token_id == "test_token"
        print("  [OK] Save and load round-trip successful")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ── Step 6: Place order below market (should NOT fill) ───────────
    if args.place_order and first_market:
        print("\n=== Step 6: Place $1 FOK BUY below market ===")
        try:
            # Use Gamma best_ask or CLOB price, place well below
            clob_ask = prices.best_ask if prices else None
            ref_price = first_market.best_ask or clob_ask or 0.50
            test_price = round(max(0.01, ref_price - 0.20), 2)
            shares = round(1.0 / test_price, 2)

            print(f"  Placing: BUY {shares} shares @ ${test_price} (market ask: ${ref_price})")
            result = client.place_order(
                token_id=first_market.yes_token_id,
                side="BUY",
                size=shares,
                price=test_price,
                order_type="FOK",
            )
            print(f"  Order ID: {result.order_id}")
            print(f"  Status: {result.status}")
            print(f"  Filled: {result.filled_size} @ {result.filled_price}")
            if result.filled_size == 0:
                print("  [OK] Order not filled as expected (below market)")
            else:
                print("  [WARN] Order filled unexpectedly!")
        except Exception as e:
            print(f"  Result: {e}")

    # ── Step 7: Place order at market (WILL fill) ────────────────────
    if args.fill_order and first_market:
        print("\n=== Step 7: Place $1 FOK BUY at best ask (WILL FILL) ===")
        try:
            clob_ask = prices.best_ask if prices else None
            fill_price = first_market.best_ask or clob_ask
            if not fill_price:
                print("  [SKIP] No ask price available")
            else:
                shares = round(1.0 / fill_price, 2)
                print(f"  Placing: BUY {shares} shares @ ${fill_price}")
                result = client.place_order(
                    token_id=first_market.yes_token_id,
                    side="BUY",
                    size=shares,
                    price=fill_price,
                    order_type="FOK",
                )
                print(f"  Order ID: {result.order_id}")
                print(f"  Status: {result.status}")
                print(f"  Filled: {result.filled_size} @ {result.filled_price}")
        except Exception as e:
            print(f"  [FAIL] {e}")

    print("\n=== All tests complete ===")


if __name__ == "__main__":
    main()
