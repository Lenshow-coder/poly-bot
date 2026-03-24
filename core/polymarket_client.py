import asyncio
import json
import logging
from datetime import datetime, timezone

import requests
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from sortedcontainers import SortedDict
from web3 import Web3
import websockets

from core.models import EventInfo, MarketInfo, OrderResult, PriceInfo
from core.utils import load_env_credentials

logger = logging.getLogger("poly-bot.client")

# Minimal USDC ABI — just balanceOf
USDC_ABI = json.loads(
    '[{"constant":true,"inputs":[{"name":"account","type":"address"}],'
    '"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],'
    '"stateMutability":"view","type":"function"}]'
)


class PolymarketClient:
    def __init__(self, config: dict):
        self.config = config
        poly_cfg = config["polymarket"]
        polygon_cfg = config["polygon"]

        # Load credentials
        self.pk, self.browser_address = load_env_credentials()

        # Init CLOB client
        self.clob = ClobClient(
            host=poly_cfg["clob_url"],
            key=self.pk,
            chain_id=poly_cfg["chain_id"],
            signature_type=poly_cfg["signature_type"],
            funder=self.browser_address,
        )

        # Derive and set API credentials
        creds = self.clob.create_or_derive_api_creds()
        self.clob.set_api_creds(creds)
        logger.info("CLOB client initialized and API credentials set")

        # Web3 for on-chain queries
        self.w3 = Web3(Web3.HTTPProvider(polygon_cfg["rpc_url"]))
        self.usdc_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(polygon_cfg["usdc_address"]),
            abi=USDC_ABI,
        )

        # URLs
        self.gamma_url = poly_cfg["gamma_url"]
        self.data_url = poly_cfg["data_url"]
        self.ws_url = poly_cfg["ws_url"]

        # Order book cache: token_id -> {"bids": SortedDict, "asks": SortedDict}
        self.books: dict[str, dict[str, SortedDict]] = {}

    # ── REST: Gamma API ──────────────────────────────────────────────

    def get_event(self, slug: str) -> EventInfo:
        resp = requests.get(f"{self.gamma_url}/events/slug/{slug}")
        resp.raise_for_status()
        data = resp.json()

        markets = []
        for m in data.get("markets", []):
            token_ids = json.loads(m.get("clobTokenIds", "[]"))
            yes_token = token_ids[0] if len(token_ids) > 0 else ""
            no_token = token_ids[1] if len(token_ids) > 1 else ""

            best_bid = None
            best_ask = None
            if m.get("bestBid"):
                best_bid = float(m["bestBid"])
            if m.get("bestAsk"):
                best_ask = float(m["bestAsk"])

            markets.append(
                MarketInfo(
                    condition_id=m.get("conditionId", ""),
                    question=m.get("question", ""),
                    outcome_name=m.get("groupItemTitle", m.get("question", "")),
                    yes_token_id=yes_token,
                    no_token_id=no_token,
                    active=m.get("active", False),
                    best_bid=best_bid,
                    best_ask=best_ask,
                )
            )

        return EventInfo(
            event_id=str(data.get("id", "")),
            event_slug=slug,
            title=data.get("title", ""),
            neg_risk=data.get("negRisk", False),
            markets=markets,
        )

    # ── REST: CLOB API ───────────────────────────────────────────────

    def get_order_book(self, token_id: str) -> dict:
        book = self.clob.get_order_book(token_id)
        # Asks come DESCENDING from the API — reverse them to ascending
        if hasattr(book, "asks") and book.asks:
            book.asks = list(reversed(book.asks))
        elif isinstance(book, dict) and book.get("asks"):
            book["asks"] = list(reversed(book["asks"]))
        return book

    def get_prices(self, token_id: str) -> PriceInfo:
        book = self.get_order_book(token_id)

        best_bid = None
        best_ask = None
        bid_liquidity = 0.0
        ask_liquidity = 0.0

        # Extract bids and asks — handle both object and dict formats
        bids = getattr(book, "bids", None) or (
            book.get("bids") if isinstance(book, dict) else []
        ) or []
        asks = getattr(book, "asks", None) or (
            book.get("asks") if isinstance(book, dict) else []
        ) or []

        if bids:
            first_bid = bids[0]
            price = float(first_bid.price if hasattr(first_bid, "price") else first_bid["price"])
            best_bid = price
            for b in bids:
                bid_liquidity += float(b.size if hasattr(b, "size") else b["size"])

        if asks:
            first_ask = asks[0]
            price = float(first_ask.price if hasattr(first_ask, "price") else first_ask["price"])
            best_ask = price
            for a in asks:
                ask_liquidity += float(a.size if hasattr(a, "size") else a["size"])

        midpoint = None
        if best_bid is not None and best_ask is not None:
            midpoint = round((best_bid + best_ask) / 2, 4)

        return PriceInfo(
            best_bid=best_bid,
            best_ask=best_ask,
            midpoint=midpoint,
            bid_liquidity=round(bid_liquidity, 2),
            ask_liquidity=round(ask_liquidity, 2),
        )

    # ── REST: Balance ────────────────────────────────────────────────

    def get_usdc_balance(self) -> float:
        raw = self.usdc_contract.functions.balanceOf(
            Web3.to_checksum_address(self.browser_address)
        ).call()
        return raw / 1e6

    # ── REST: Order Placement ────────────────────────────────────────

    def place_order(
        self,
        token_id: str,
        side: str,
        size: float,
        price: float,
        order_type: str = "FOK",
    ) -> OrderResult:
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side.upper(),
        )
        signed_order = self.clob.create_order(order_args)

        ot = getattr(OrderType, order_type, OrderType.FOK)
        resp = self.clob.post_order(signed_order, ot)

        # Parse response — may be dict or have attributes
        if isinstance(resp, dict):
            raw = resp
        else:
            raw = resp.__dict__ if hasattr(resp, "__dict__") else {"raw": str(resp)}

        return OrderResult(
            order_id=raw.get("orderID", raw.get("id", "")),
            status=raw.get("status", "UNKNOWN"),
            filled_size=float(raw.get("filledSize", 0)),
            filled_price=float(raw.get("filledPrice", price)),
            timestamp=datetime.now(timezone.utc).isoformat(),
            raw_response=raw,
        )

    # ── REST: Approvals ──────────────────────────────────────────────

    def approve_contracts(self) -> None:
        """Approve USDC spending and CTF operator for Polymarket exchange contracts."""
        # Exchange contract addresses
        exchange = Web3.to_checksum_address(
            "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
        )
        neg_risk_exchange = Web3.to_checksum_address(
            "0xC5d563A36AE78145C45a50134d48A1215220f80a"
        )
        ctf = Web3.to_checksum_address(
            "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
        )

        max_uint = 2**256 - 1
        account = Web3.to_checksum_address(self.browser_address)

        # Build minimal approve ABI
        approve_abi = json.loads(
            '[{"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],'
            '"name":"approve","outputs":[{"name":"","type":"bool"}],'
            '"stateMutability":"nonpayable","type":"function"}]'
        )
        usdc = self.w3.eth.contract(
            address=Web3.to_checksum_address(
                self.config["polygon"]["usdc_address"]
            ),
            abi=approve_abi,
        )

        # CTF setApprovalForAll ABI
        set_approval_abi = json.loads(
            '[{"inputs":[{"name":"operator","type":"address"},{"name":"approved","type":"bool"}],'
            '"name":"setApprovalForAll","outputs":[],'
            '"stateMutability":"nonpayable","type":"function"}]'
        )
        ctf_contract = self.w3.eth.contract(address=ctf, abi=set_approval_abi)

        nonce = self.w3.eth.get_transaction_count(account)

        for spender in [exchange, neg_risk_exchange]:
            # USDC approval
            tx = usdc.functions.approve(spender, max_uint).build_transaction(
                {
                    "from": account,
                    "nonce": nonce,
                    "gas": 100000,
                    "gasPrice": self.w3.eth.gas_price,
                }
            )
            signed = self.w3.eth.account.sign_transaction(tx, self.pk)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            nonce += 1
            logger.info(f"USDC approved for {spender}")

            # CTF operator approval
            tx = ctf_contract.functions.setApprovalForAll(
                spender, True
            ).build_transaction(
                {
                    "from": account,
                    "nonce": nonce,
                    "gas": 100000,
                    "gasPrice": self.w3.eth.gas_price,
                }
            )
            signed = self.w3.eth.account.sign_transaction(tx, self.pk)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            nonce += 1
            logger.info(f"CTF operator approved for {spender}")

        logger.info("All contract approvals completed")

    # ── REST: Positions ──────────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        resp = requests.get(
            f"{self.data_url}/positions",
            params={"user": self.browser_address},
        )
        resp.raise_for_status()
        return resp.json()

    # ── WebSocket ────────────────────────────────────────────────────

    async def connect_market_ws(self, token_ids: list[str]) -> None:
        engine_cfg = self.config.get("engine", {})
        ping_interval = engine_cfg.get("ws_ping_interval", 5)
        reconnect_delay = engine_cfg.get("ws_reconnect_delay", 5)

        while True:
            try:
                async with websockets.connect(
                    self.ws_url, ping_interval=ping_interval
                ) as ws:
                    # Subscribe
                    subscribe_msg = json.dumps(
                        {
                            "type": "subscribe",
                            "channel": "market",
                            "assets_ids": token_ids,
                        }
                    )
                    await ws.send(subscribe_msg)
                    logger.info(
                        f"WebSocket connected, subscribed to {len(token_ids)} tokens"
                    )

                    async for raw_msg in ws:
                        msg = json.loads(raw_msg)
                        event_type = msg.get("event_type", msg.get("type", ""))

                        if event_type == "book":
                            self._handle_book_snapshot(msg)
                        elif event_type == "price_change":
                            self._handle_price_change(msg)

            except (
                websockets.ConnectionClosed,
                websockets.InvalidURI,
                ConnectionError,
                OSError,
            ) as e:
                logger.warning(
                    f"WebSocket disconnected: {e}. Reconnecting in {reconnect_delay}s..."
                )
                await asyncio.sleep(reconnect_delay)

    def _handle_book_snapshot(self, msg: dict) -> None:
        asset_id = msg.get("asset_id", "")
        if not asset_id:
            return

        bids = SortedDict()
        asks = SortedDict()

        for bid in msg.get("bids", []):
            price = float(bid["price"])
            bids[-price] = float(bid["size"])  # Negative key for descending sort

        for ask in msg.get("asks", []):
            price = float(ask["price"])
            asks[price] = float(ask["size"])

        self.books[asset_id] = {"bids": bids, "asks": asks}
        logger.debug(f"Book snapshot for {asset_id[:8]}...")

    def _handle_price_change(self, msg: dict) -> None:
        changes = msg.get("changes", [])
        for change in changes:
            asset_id = change.get("asset_id", "")
            if not asset_id or asset_id not in self.books:
                continue

            book = self.books[asset_id]
            side = change.get("side", "").lower()
            price = float(change.get("price", 0))
            size = float(change.get("size", 0))

            if side == "bid":
                if size == 0:
                    book["bids"].pop(-price, None)
                else:
                    book["bids"][-price] = size
            elif side == "ask":
                if size == 0:
                    book["asks"].pop(price, None)
                else:
                    book["asks"][price] = size
