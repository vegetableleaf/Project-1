"""earnings_report.py: snapshot the AI's REAL earnings for the dashboard.

Writes earnings_status.json with the CDP wallet's live ETH + USDC balance and a
summary of x402 sales, so the dashboard can show real income in one place.

Runs on the HOST -- the Docker dashboard can't reach the chain through corporate
TLS inspection. Balances are read from PUBLIC on-chain data, so no CDP keys are
needed just to look at the wallet.

    # point it at your wallet (or let it read cdp_wallet.json)
    $env:CDP_WALLET_ADDRESS = "0xYourCdpAddress"
    $env:CDP_NETWORK = "base-sepolia"     # or "base" for real mainnet money
    python -m money_agent.earnings_report --watch 30
"""

from __future__ import annotations

import json
import os
import sys
import time

# network -> (rpc url, USDC contract, explorer base)
_NETWORKS = {
    "base-sepolia": ("https://sepolia.base.org",
                     "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                     "https://sepolia.basescan.org"),
    "base": ("https://mainnet.base.org",
             "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
             "https://basescan.org"),
}

# minimal ERC-20 ABI -- just balanceOf
_ERC20_ABI = [{
    "constant": True,
    "inputs": [{"name": "_owner", "type": "address"}],
    "name": "balanceOf",
    "outputs": [{"name": "balance", "type": "uint256"}],
    "type": "function",
}]


def _cdp_address() -> str:
    addr = os.environ.get("CDP_WALLET_ADDRESS", "").strip()
    if addr:
        return addr
    if os.path.exists("cdp_wallet.json"):   # written by cdp_wallet.py
        try:
            with open("cdp_wallet.json", "r", encoding="utf-8") as fh:
                return json.load(fh).get("address", "")
        except (OSError, ValueError):
            pass
    return ""


def _read_sales(path: str = "x402_sales.json") -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"count": 0, "usd_total": 0.0, "recent": []}


def snapshot() -> dict:
    network = os.environ.get("CDP_NETWORK", "base-sepolia")
    rpc, usdc, explorer = _NETWORKS.get(network, _NETWORKS["base-sepolia"])
    address = _cdp_address()
    sales = _read_sales()
    result: dict = {
        "address": address,
        "network": network,
        "explorer": f"{explorer}/address/{address}" if address else "",
        "eth": 0.0,
        "usdc": 0.0,
        "sales_count": int(sales.get("count", 0)),
        "sales_usd": float(sales.get("usd_total", 0.0)),
        "recent_sales": sales.get("recent", [])[:10],
        "updated": time.time(),
        "connected": False,
        "error": None,
    }
    if not address:
        result["error"] = "no CDP wallet address (set CDP_WALLET_ADDRESS or run cdp_wallet)"
        return result

    try:
        from web3 import Web3
        try:
            import truststore
            truststore.inject_into_ssl()
        except ImportError:
            pass
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20}))
        if not w3.is_connected():
            result["error"] = f"could not connect to {rpc}"
            return result
        result["connected"] = True
        addr = Web3.to_checksum_address(address)
        result["eth"] = w3.eth.get_balance(addr) / 1e18
        token = w3.eth.contract(address=Web3.to_checksum_address(usdc), abi=_ERC20_ABI)
        result["usdc"] = token.functions.balanceOf(addr).call() / 1e6
    except Exception as exc:  # noqa: BLE001 - never crash the reporter
        result["error"] = f"{type(exc).__name__}: {exc}"

    # spending guardrails: 30% daily cap + kill switch (for the dashboard)
    try:
        from .safety import SpendGuard
        guard = SpendGuard(balance=result["usdc"], total_revenue=result["sales_usd"])
        result["safety"] = guard.status()
    except Exception:  # noqa: BLE001
        result["safety"] = None
    return result


def write_snapshot(path: str = "earnings_status.json") -> dict:
    snap = snapshot()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=2)
    os.replace(tmp, path)
    return snap


def main() -> None:
    path = os.environ.get("EARNINGS_STATUS_PATH", "earnings_status.json")
    watch = 0
    args = sys.argv[1:]
    if args and args[0] == "--watch":
        watch = int(args[1]) if len(args) > 1 else 30
    while True:
        snap = write_snapshot(path)
        state = "connected" if snap["connected"] else f"({snap['error']})"
        print(f"[earnings] {state}  eth={snap['eth']:.6f}  usdc={snap['usdc']:.2f}  "
              f"sales={snap['sales_count']} (${snap['sales_usd']:.3f})  -> {path}")
        if watch <= 0:
            break
        time.sleep(watch)


if __name__ == "__main__":
    main()
