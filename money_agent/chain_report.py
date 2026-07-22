"""chain_report.py: snapshot on-chain (Base Sepolia) balances for the dashboard.

WHY THIS RUNS ON THE HOST (not in the Docker container)
-------------------------------------------------------
The dashboard container is Linux and sits behind your company's TLS inspection,
whose certificate it does not trust -- so it can't reach the RPC. This script
runs on the host (where `truststore` trusts your company's certificate) and
writes a plain `chain_status.json` file that the container simply displays.

It reads only the public wallet ADDRESSES from the keystore (chain_accounts.json)
and asks the RPC for their balances -- no private key is required or used.

Run once:
    python -m money_agent.chain_report
Keep it updated live (every 20 seconds):
    python -m money_agent.chain_report --watch 20
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Optional

from .config import Config


def _connect(cfg: Config):
    try:
        from web3 import Web3
    except ImportError as exc:  # pragma: no cover - optional dep
        raise ImportError(
            "chain_report needs web3:  pip install -r requirements-chain.txt"
        ) from exc
    # Trust the OS certificate store so the RPC works behind corporate TLS.
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass
    return Web3(Web3.HTTPProvider(
        cfg.rpc_url, request_kwargs={"timeout": int(cfg.tx_timeout)}))


def snapshot(cfg: Optional[Config] = None) -> dict:
    """Return a dict of on-chain balances for every wallet in the keystore."""
    cfg = cfg or Config(wallet_backend="base_sepolia")
    explorer = cfg.explorer_url.rstrip("/")
    result: dict = {
        "connected": False,
        "chain_id": int(cfg.chain_id),
        "explorer": explorer,
        "rpc_url": cfg.rpc_url,
        "updated": time.time(),
        "wallets": [],
        "error": None,
    }

    accounts: dict = {}
    if os.path.exists(cfg.keystore_path):
        with open(cfg.keystore_path, "r", encoding="utf-8") as fh:
            accounts = json.load(fh)

    try:
        w3 = _connect(cfg)
        if not w3.is_connected():
            result["error"] = f"could not connect to {cfg.rpc_url}"
            return result
        result["connected"] = True
        scale = int(cfg.wei_per_unit)
        gas_reserve = int(cfg.gas_reserve_wei)
        for account_id, info in accounts.items():
            addr = info["address"]
            wei = int(w3.eth.get_balance(addr))
            tracked = max(0, wei - gas_reserve)
            result["wallets"].append({
                "account_id": account_id,
                "address": addr,
                "balance": tracked / scale,
                "eth": wei / 1e18,
                "explorer_url": f"{explorer}/address/{addr}",
            })
    except Exception as exc:  # noqa: BLE001 - report any failure to the dashboard
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def write_snapshot(path: str, cfg: Optional[Config] = None) -> dict:
    """Write a snapshot to `path` atomically and return it."""
    snap = snapshot(cfg)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=2)
    os.replace(tmp, path)
    return snap


def main() -> None:
    cfg = Config(wallet_backend="base_sepolia")
    path = os.environ.get("CHAIN_STATUS_PATH", "chain_status.json")

    watch = 0
    args = sys.argv[1:]
    if args and args[0] == "--watch":
        watch = int(args[1]) if len(args) > 1 else 20

    while True:
        snap = write_snapshot(path, cfg)
        state = "connected" if snap["connected"] else f"NOT connected ({snap['error']})"
        print(f"[chain_report] {state}; wrote {len(snap['wallets'])} wallets -> {path}")
        if watch <= 0:
            break
        time.sleep(watch)


if __name__ == "__main__":
    main()
