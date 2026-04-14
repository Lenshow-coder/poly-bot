"""Convert NO tokens into YES tokens for the complementary outcomes.

Supports converting one or multiple outcomes' NO tokens in a single call.
Converting multiple NO types returns USDC as well as YES tokens.

Sends transactions through the Polymarket Proxy Wallet Factory so that
the proxy address (BROWSER_ADDRESS) is the msg.sender — matching where
your tokens are held.

Usage:
    # Single NO type (get YES for all others, no USDC back):
    .venv/Scripts/python.exe neg-risk-adapter/convert_positions.py \
        --event-slug 2026-nhl-stanley-cup-champion \
        --outcomes "Carolina Hurricanes" \
        --amount 100 --dry-run

    # Multiple NO types (get YES for the rest + USDC back):
    .venv/Scripts/python.exe neg-risk-adapter/convert_positions.py \
        --event-slug 2026-nhl-stanley-cup-champion \
        --outcomes "Carolina Hurricanes" "Dallas Stars" "Florida Panthers" \
        --amount 100 --dry-run

    # Convert entire NO balance (omit --amount):
    .venv/Scripts/python.exe neg-risk-adapter/convert_positions.py \
        --event-slug 2026-nhl-stanley-cup-champion \
        --outcomes "Carolina Hurricanes"

    # List all available outcomes for an event:
    .venv/Scripts/python.exe neg-risk-adapter/convert_positions.py \
        --event-slug 2026-nhl-stanley-cup-champion --list
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from web3 import Web3

from core.utils import load_config, load_env_credentials

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("neg-risk-convert")

# ── ABIs ────────────────────────────────────────────────────────────────

CTF_ABI = json.loads("""[
    {"inputs":[{"name":"account","type":"address"},{"name":"id","type":"uint256"}],
     "name":"balanceOf","outputs":[{"name":"","type":"uint256"}],
     "stateMutability":"view","type":"function"},
    {"inputs":[{"name":"owner","type":"address"},{"name":"operator","type":"address"}],
     "name":"isApprovedForAll","outputs":[{"name":"","type":"bool"}],
     "stateMutability":"view","type":"function"},
    {"inputs":[{"name":"operator","type":"address"},{"name":"approved","type":"bool"}],
     "name":"setApprovalForAll","outputs":[],
     "stateMutability":"nonpayable","type":"function"}
]""")

NEG_RISK_ADAPTER_ABI = json.loads("""[
    {"inputs":[{"name":"_marketId","type":"bytes32"},
               {"name":"_indexSet","type":"uint256"},
               {"name":"_amount","type":"uint256"}],
     "name":"convertPositions","outputs":[],
     "stateMutability":"nonpayable","type":"function"}
]""")

# Proxy Factory ABI — proxy() forwards calls through the user's proxy wallet.
# Each call is a tuple: (typeCode, to, value, data) where typeCode=1 is CALL.
PROXY_FACTORY_ABI = json.loads("""[
    {"constant":false,
     "inputs":[{"components":[
         {"name":"typeCode","type":"uint8"},
         {"name":"to","type":"address"},
         {"name":"value","type":"uint256"},
         {"name":"data","type":"bytes"}
       ],"name":"calls","type":"tuple[]"}],
     "name":"proxy",
     "outputs":[{"name":"returnValues","type":"bytes[]"}],
     "payable":true,"stateMutability":"payable","type":"function"}
]""")


def get_question_index(question_id: str) -> int:
    """Extract the question index from the last byte of the questionID."""
    return int(question_id, 16) & 0xFF


def build_index_set(question_indices: list[int]) -> int:
    """Build a bitmask with bits set at each given question index."""
    index_set = 0
    for idx in question_indices:
        index_set |= (1 << idx)
    return index_set


def fetch_event(gamma_url: str, slug: str) -> dict:
    resp = requests.get(f"{gamma_url}/events/slug/{slug}")
    resp.raise_for_status()
    data = resp.json()
    if not data.get("negRisk"):
        raise ValueError(f"Event '{slug}' is not a neg-risk market")
    return data


def parse_markets(event: dict) -> list[dict]:
    """Parse event API response into a flat list of market entries."""
    markets = []
    for m in event["markets"]:
        token_ids = json.loads(m.get("clobTokenIds", "[]"))
        if len(token_ids) != 2:
            continue
        markets.append({
            "name": m.get("groupItemTitle", m.get("question", "")),
            "question_id": m.get("questionID", ""),
            "condition_id": m.get("conditionId", ""),
            "yes_token_id": token_ids[0],
            "no_token_id": token_ids[1],
            "best_ask": m.get("bestAsk"),
        })
    return markets


def connect_web3(config: dict) -> Web3:
    rpc_urls = config["polygon"].get("rpc_urls", [])
    for url in rpc_urls:
        try:
            w3 = Web3(Web3.HTTPProvider(url))
            if w3.is_connected():
                logger.info(f"Connected to RPC: {url}")
                return w3
        except Exception as e:
            logger.warning(f"RPC failed ({url}): {e}")
    raise ConnectionError("All RPC endpoints failed")


def main():
    parser = argparse.ArgumentParser(description="Convert NO tokens to YES for other outcomes")
    parser.add_argument("--event-slug", required=True, help="Polymarket event slug")
    parser.add_argument("--outcomes", nargs="+", default=[], help="Outcome name(s) whose NO tokens to convert")
    parser.add_argument("--amount", type=float, default=None, help="Amount to convert per outcome (omit for full balance)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without sending transaction")
    parser.add_argument("--list", action="store_true", help="List all outcomes and exit")
    args = parser.parse_args()

    config = load_config()
    pk, browser_address = load_env_credentials()

    # EOA = the key we sign with. Proxy = where tokens live (BROWSER_ADDRESS).
    eoa_address = Web3().eth.account.from_key(pk).address
    proxy_address = browser_address
    is_proxy = eoa_address.lower() != proxy_address.lower()

    if is_proxy:
        logger.info(f"Proxy wallet: {proxy_address}")
        logger.info(f"EOA (signer): {eoa_address}")
    else:
        logger.info(f"EOA (direct): {eoa_address}")

    # ── Fetch event data ────────────────────────────────────────────
    gamma_url = config["polymarket"]["gamma_url"]
    event = fetch_event(gamma_url, args.event_slug)
    neg_risk_market_id = event["negRiskMarketID"]
    all_markets = parse_markets(event)

    logger.info(f"Event: {event['title']}")
    logger.info(f"negRiskMarketID: {neg_risk_market_id}")
    logger.info(f"Total outcomes: {len(all_markets)}")

    # ── List mode ───────────────────────────────────────────────────
    if args.list:
        logger.info("Available outcomes:")
        for m in sorted(all_markets, key=lambda x: x["name"]):
            idx = get_question_index(m["question_id"])
            price = m.get("best_ask", "?")
            logger.info(f"  [{idx:>2}] {m['name']}  (YES ask: {price})")
        return

    if not args.outcomes:
        parser.error("--outcomes is required (or use --list to see available outcomes)")

    # ── Find target outcomes ────────────────────────────────────────
    market_by_name = {m["name"]: m for m in all_markets}
    targets = []
    for name in args.outcomes:
        if name not in market_by_name:
            logger.error(f"Outcome '{name}' not found. Use --list to see available outcomes.")
            sys.exit(1)
        targets.append(market_by_name[name])

    m = len(targets)
    question_indices = [get_question_index(t["question_id"]) for t in targets]
    index_set = build_index_set(question_indices)

    logger.info(f"Converting NO tokens for {m} outcome(s):")
    for t, idx in zip(targets, question_indices):
        logger.info(f"  - {t['name']} (question index {idx})")
    logger.info(f"indexSet: {index_set} (0b{bin(index_set)[2:]})")

    # ── Connect to chain ────────────────────────────────────────────
    w3 = connect_web3(config)

    # Token balances and approvals are checked on the proxy address
    # (where tokens live), but txs are signed by the EOA.
    token_holder = Web3.to_checksum_address(proxy_address)
    signer = Web3.to_checksum_address(eoa_address)

    ctf_address = Web3.to_checksum_address(config["contracts"]["ctf"])
    adapter_address = Web3.to_checksum_address(config["contracts"]["neg_risk_adapter"])

    ctf = w3.eth.contract(address=ctf_address, abi=CTF_ABI)
    adapter = w3.eth.contract(address=adapter_address, abi=NEG_RISK_ADAPTER_ABI)

    # ── Check NO token balances ─────────────────────────────────────
    balances_raw = []
    for t in targets:
        no_token_id = int(t["no_token_id"])
        bal = ctf.functions.balanceOf(token_holder, no_token_id).call()
        balances_raw.append(bal)
        logger.info(f"  NO-{t['name']} balance: {bal / 1e6:.6f} (raw: {bal})")

    min_balance = min(balances_raw)
    if min_balance == 0:
        zero_names = [t["name"] for t, b in zip(targets, balances_raw) if b == 0]
        logger.error(f"Zero NO balance for: {', '.join(zero_names)}")
        sys.exit(1)

    # Amount must be equal across all NO types (contract requires same _amount)
    if args.amount is not None:
        amount_raw = int(args.amount * 1e6)
        for t, bal in zip(targets, balances_raw):
            if amount_raw > bal:
                logger.error(
                    f"Requested {args.amount} but NO-{t['name']} "
                    f"only has {bal / 1e6:.6f}"
                )
                sys.exit(1)
        amount = args.amount
    else:
        amount_raw = min_balance
        amount = min_balance / 1e6
        if any(b != min_balance for b in balances_raw):
            logger.warning(
                f"Balances differ — converting {amount:.6f} "
                f"(smallest balance) from each"
            )

    # ── Show what you'll receive ────────────────────────────────────
    target_names = {t["name"] for t in targets}
    complement = [mk for mk in all_markets if mk["name"] not in target_names]
    usdc_back = amount * (m - 1)

    logger.info(f"")
    logger.info(f"=== Conversion Summary ===")
    logger.info(f"Burning:")
    for t in targets:
        logger.info(f"  - {amount} NO-{t['name']}")
    logger.info(f"Receiving:")
    logger.info(f"  - {usdc_back:.2f} USDC  (amount x (m-1) = {amount} x {m - 1})")
    logger.info(f"  - {amount} YES tokens for each of {len(complement)} outcomes:")
    for mk in sorted(complement, key=lambda x: x["name"]):
        logger.info(f"      + {amount} YES-{mk['name']}")
    logger.info(f"")

    if args.dry_run:
        logger.info("[DRY RUN] Would call convertPositions — stopping here")
        return

    # ── Build the convertPositions calldata ──────────────────────────
    market_id_bytes = bytes.fromhex(neg_risk_market_id[2:])
    convert_calldata = adapter.functions.convertPositions(
        market_id_bytes, index_set, amount_raw,
    ).build_transaction({"gas": 0, "gasPrice": 0})["data"]

    nonce = w3.eth.get_transaction_count(signer)

    if is_proxy:
        # ── Proxy path: send through Proxy Wallet Factory ────────────
        factory_address = Web3.to_checksum_address(config["contracts"]["proxy_wallet_factory"])
        factory = w3.eth.contract(address=factory_address, abi=PROXY_FACTORY_ABI)

        # Check if proxy has approved the adapter on the CTF contract
        approved = ctf.functions.isApprovedForAll(token_holder, adapter_address).call()

        proxy_calls = []

        if not approved:
            logger.info("Adding CTF approval for NegRiskAdapter to proxy batch...")
            approve_calldata = ctf.functions.setApprovalForAll(
                adapter_address, True,
            ).build_transaction({"gas": 0, "gasPrice": 0})["data"]
            proxy_calls.append((1, ctf_address, 0, approve_calldata))

        # typeCode=1 is CALL
        proxy_calls.append((1, adapter_address, 0, convert_calldata))

        # Estimate gas — conversions scale with outcome count
        gas_estimate = factory.functions.proxy(proxy_calls).estimate_gas(
            {"from": signer, "value": 0}
        )
        gas_limit = int(gas_estimate * 1.2)
        gas_cost_pol = gas_limit * w3.eth.gas_price / 1e18

        logger.info(
            f"Sending {len(proxy_calls)} call(s) through Proxy Factory "
            f"(convertPositions indexSet={index_set}, amount={amount_raw})..."
        )
        logger.info(f"Estimated gas: {gas_estimate}, limit: {gas_limit} (~{gas_cost_pol:.4f} POL)")

        tx = factory.functions.proxy(proxy_calls).build_transaction({
            "from": signer,
            "nonce": nonce,
            "gas": gas_limit,
            "gasPrice": w3.eth.gas_price,
            "value": 0,
        })
    else:
        # ── Direct path: EOA calls adapter directly ──────────────────
        approved = ctf.functions.isApprovedForAll(signer, adapter_address).call()
        if not approved:
            logger.info("Setting CTF approval for NegRiskAdapter...")
            approve_tx = ctf.functions.setApprovalForAll(
                adapter_address, True,
            ).build_transaction({
                "from": signer,
                "nonce": nonce,
                "gas": 100000,
                "gasPrice": w3.eth.gas_price,
            })
            signed = w3.eth.account.sign_transaction(approve_tx, pk)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt.status != 1:
                logger.error(f"Approval tx failed: {tx_hash.hex()}")
                sys.exit(1)
            logger.info(f"Approval confirmed: {tx_hash.hex()}")
            nonce += 1

        # Estimate gas — conversions scale with outcome count
        gas_estimate = adapter.functions.convertPositions(
            market_id_bytes, index_set, amount_raw,
        ).estimate_gas({"from": signer})
        gas_limit = int(gas_estimate * 1.2)
        gas_cost_pol = gas_limit * w3.eth.gas_price / 1e18

        logger.info(
            f"Sending convertPositions("
            f"indexSet={index_set}, amount={amount_raw})..."
        )
        logger.info(f"Estimated gas: {gas_estimate}, limit: {gas_limit} (~{gas_cost_pol:.4f} POL)")

        tx = adapter.functions.convertPositions(
            market_id_bytes, index_set, amount_raw,
        ).build_transaction({
            "from": signer,
            "nonce": nonce,
            "gas": gas_limit,
            "gasPrice": w3.eth.gas_price,
        })

    # ── Sign and send ───────────────────────────────────────────────
    signed = w3.eth.account.sign_transaction(tx, pk)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    logger.info(f"Tx sent: {tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.status == 1:
        logger.info(f"Conversion successful! Gas used: {receipt.gasUsed}")
        logger.info(f"Tx: https://polygonscan.com/tx/{tx_hash.hex()}")
    else:
        logger.error(
            f"Conversion FAILED. "
            f"Tx: https://polygonscan.com/tx/{tx_hash.hex()}"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
