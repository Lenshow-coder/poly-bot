import json
import logging
from datetime import datetime, timezone

import requests
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from web3 import Web3

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

        # Web3 for on-chain queries — try each RPC until one connects
        rpc_urls = polygon_cfg.get("rpc_urls", [polygon_cfg.get("rpc_url", "")])
        self.w3 = None
        for rpc_url in rpc_urls:
            try:
                w3 = Web3(Web3.HTTPProvider(rpc_url))
                if w3.is_connected():
                    self.w3 = w3
                    logger.info(f"Connected to RPC: {rpc_url}")
                    break
                logger.warning(f"RPC not reachable: {rpc_url}")
            except Exception as e:
                logger.warning(f"RPC failed ({rpc_url}): {e}")
        if self.w3 is None:
            raise ConnectionError(f"All RPC endpoints failed: {rpc_urls}")

        self.usdc_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(polygon_cfg["usdc_address"]),
            abi=USDC_ABI,
        )

        # URLs
        self.gamma_url = poly_cfg["gamma_url"]
        self.data_url = poly_cfg["data_url"]

    # ── REST: Gamma API ──────────────────────────────────────────────

    def get_event(self, slug: str) -> EventInfo:
        resp = requests.get(f"{self.gamma_url}/events/slug/{slug}")
        resp.raise_for_status()
        data = resp.json()

        markets = []
        for m in data.get("markets", []):
            token_ids = json.loads(m.get("clobTokenIds", "[]"))
            if len(token_ids) != 2:
                logger.warning(
                    f"Skipping market {m.get('conditionId', '?')}: "
                    f"expected 2 tokens, got {len(token_ids)}"
                )
                continue

            markets.append(
                MarketInfo(
                    condition_id=m.get("conditionId", ""),
                    question=m.get("question", ""),
                    outcome_name=m.get("groupItemTitle", m.get("question", "")),
                    yes_token_id=token_ids[0],
                    no_token_id=token_ids[1],
                    active=m.get("active", False),
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
        return book

    def get_prices(self, token_id: str) -> PriceInfo:
        book = self.get_order_book(token_id)

        best_bid = None
        best_ask = None

        bids = getattr(book, "bids", None) or []
        asks = getattr(book, "asks", None) or []

        if bids:
            best_bid = float(bids[0].price)

        if asks:
            best_ask = float(asks[0].price)

        midpoint = None
        if best_bid is not None and best_ask is not None:
            midpoint = round((best_bid + best_ask) / 2, 4)

        return PriceInfo(best_bid=best_bid, best_ask=best_ask, midpoint=midpoint)

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

        # Parse response
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
        )

    # ── REST: Approvals ──────────────────────────────────────────────

    def approve_contracts(self) -> None:
        """Approve USDC spending and CTF operator for Polymarket exchange contracts."""
        contracts_cfg = self.config["contracts"]
        exchange = Web3.to_checksum_address(contracts_cfg["exchange"])
        neg_risk_exchange = Web3.to_checksum_address(contracts_cfg["neg_risk_exchange"])
        ctf = Web3.to_checksum_address(contracts_cfg["ctf"])

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
