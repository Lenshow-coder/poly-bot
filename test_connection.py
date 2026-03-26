"""
Standalone verification script for Phase 1 connectivity.

Usage:
    python test_connection.py --slug <event-slug>
    python test_connection.py --slug <event-slug> --place-order
    python test_connection.py --slug <event-slug> --fill-order
    python test_connection.py --slug <event-slug> --ws-test
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

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


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
    parser.add_argument(
        "--ws-test",
        action="store_true",
        help="Connect to market WebSocket for 30s and print incoming messages",
    )
    args = parser.parse_args()

    config = load_config()
    logger = setup_logging(level="INFO", console=True)
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
            print(f"    {m.outcome_name}: yes_token={m.yes_token_id[:12]}...")
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

            bids = getattr(book, "bids", None) or []
            asks = getattr(book, "asks", None) or []

            print(f"  Bids: {len(bids)} levels")
            for b in bids[:3]:
                print(f"    {b.price} x {b.size}")

            print(f"  Asks: {len(asks)} levels (sorted ascending)")
            for a in asks[:3]:
                print(f"    {a.price} x {a.size}")

            prices = client.get_prices(first_market.yes_token_id)
            print(f"  Best bid: {prices.best_bid}, Best ask: {prices.best_ask}, Mid: {prices.midpoint}")
        except Exception as e:
            print(f"  [FAIL] {e}")

    # ── Step 4: USDC balance ─────────────────────────────────────────
    print("\n=== Step 4: USDC Balance ===")
    try:
        wallet_balance = client.get_usdc_balance()
        exchange_balance = client.get_exchange_balance()
        print(f"  Wallet (not deposited): ${wallet_balance:.2f}")
        print(f"  Exchange (available to trade): ${exchange_balance:.2f}")
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
            ref_price = prices.best_ask if prices else 0.50
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
            fill_price = prices.best_ask if prices else None
            if not fill_price:
                print("  [SKIP] No ask price available")
            else:
                import math
                # Ceil to whole shares to meet $1 minimum order size
                shares = float(math.ceil(1.0 / fill_price))
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

    # ── Step 8: WebSocket smoke test ──────────────────────────────
    if args.ws_test and event.markets:
        import asyncio
        import websockets as ws_lib

        print("\n=== Step 8: WebSocket smoke test (30s) ===")
        token_ids = [m.yes_token_id for m in event.markets[:3]]
        ws_duration = 30
        messages_received = []

        async def ws_test():
            try:
                async with ws_lib.connect(WS_URL, ping_interval=5) as ws:
                    subscribe_msg = json.dumps(
                        {
                            "type": "subscribe",
                            "channel": "market",
                            "assets_ids": token_ids,
                        }
                    )
                    await ws.send(subscribe_msg)
                    print(f"  Subscribed to {len(token_ids)} tokens, listening for {ws_duration}s...")

                    async def listen():
                        async for raw_msg in ws:
                            msg = json.loads(raw_msg)
                            # WS may return a list of book snapshots or a dict
                            if isinstance(msg, list):
                                event_type = "book_snapshot"
                                n_books = len(msg)
                            else:
                                event_type = msg.get("event_type", msg.get("type", "unknown"))
                                n_books = None
                            messages_received.append(event_type)
                            if len(messages_received) <= 5:
                                detail = f" ({n_books} books)" if n_books else ""
                                print(f"  [{len(messages_received)}] {event_type}{detail}")
                            elif len(messages_received) == 6:
                                print("  ... (suppressing further output, counting messages)")

                    await asyncio.wait_for(listen(), timeout=ws_duration)
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"  [FAIL] WebSocket error: {e}")
                return

            total = len(messages_received)
            if total > 0:
                print(f"  [OK] Received {total} messages in {ws_duration}s")
            else:
                print(f"  [WARN] No messages received in {ws_duration}s — market may be inactive")

        asyncio.run(ws_test())

    print("\n=== All tests complete ===")


if __name__ == "__main__":
    main()
