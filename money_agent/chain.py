"""BaseSepoliaWallet: real on-chain money storage on the Base Sepolia TESTNET.

Base Sepolia is a public Ethereum L2 test network. Its ETH is handed out for
free by faucets and has **no monetary value**, so it is a safe place to let an
autonomous agent actually move funds while you develop.

Design
------
* A single **treasury** account (its private key comes from an environment
  variable, never the code) funds everything and acts as the settlement
  counterparty ("the house").
* Every agent / the vault gets its own freshly generated testnet account. The
  address+key map is stored in a local keystore JSON (testnet keys only).
* `balance()` reads the real on-chain ETH balance and converts wei ->
  "simulation dollars" using `wei_per_unit`.
* A fixed `gas_reserve_wei` is funded on top of each account and is *excluded*
  from the tracked balance, so accounts always have gas to pay their own fees
  (this sidesteps the "gas is paid in the same asset you're counting" problem).
* Simulated trading PnL is settled on-chain in `set_balance()`: the treasury
  tops the account up when it profits, the account pays the treasury when it
  loses.

SECURITY
--------
* Only ever put a **throwaway testnet** private key in `BASE_SEPOLIA_PRIVATE_KEY`.
  Never a mainnet key, never a key that controls real funds.
* The keystore file contains testnet private keys; keep it out of version
  control (see .gitignore).
"""

from __future__ import annotations

import json
import os
from typing import Dict, Optional

from .config import Config

_TREASURY_ID = "__treasury__"


class BaseSepoliaWallet:
    def __init__(self, config: Config) -> None:
        try:
            from web3 import Web3
            from eth_account import Account
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise ImportError(
                "The Base Sepolia backend needs web3. Install it with:\n"
                "    pip install -r requirements-chain.txt"
            ) from exc

        # Trust the operating system's certificate store (Windows/macOS/Linux).
        # By default Python only trusts its own bundled CA list, so the RPC's
        # HTTPS connection fails on networks that use corporate TLS inspection or
        # a custom/internal root CA. This makes Python behave like your browser.
        # Safe no-op if truststore isn't installed (e.g. on a plain home network).
        try:
            import truststore
            truststore.inject_into_ssl()
        except ImportError:
            pass

        self._Account = Account
        self.cfg = config
        self.w3 = Web3(Web3.HTTPProvider(
            config.rpc_url, request_kwargs={"timeout": int(config.tx_timeout)}))
        if not self.w3.is_connected():
            raise ConnectionError(
                f"Could not connect to RPC endpoint: {config.rpc_url}\n"
                "If this looks like an SSL/certificate error on a work network, "
                "install truststore (pip install truststore) so Python trusts your "
                "system's certificate store. You can also try a different rpc_url "
                "(e.g. https://base-sepolia-rpc.publicnode.com)."
            )

        private_key = os.environ.get(config.private_key_env)
        if not private_key:
            raise RuntimeError(
                f"Treasury key not found. Set env var {config.private_key_env} to a "
                "throwaway Base Sepolia TESTNET private key funded from a faucet. "
                "Never use a mainnet key."
            )
        self.treasury = Account.from_key(private_key)

        self.scale = int(config.wei_per_unit)
        self.gas_reserve = int(config.gas_reserve_wei)
        self.chain_id = int(config.chain_id)
        self.tx_timeout = int(config.tx_timeout)

        # generated accounts only (persisted). treasury key stays in memory.
        self._keystore_path = config.keystore_path
        self._accounts: Dict[str, dict] = {}
        self._load_keystore()

    # ------------------------------------------------------------- keystore
    def _load_keystore(self) -> None:
        if os.path.exists(self._keystore_path):
            with open(self._keystore_path, "r", encoding="utf-8") as fh:
                self._accounts = json.load(fh)

    def _save_keystore(self) -> None:
        with open(self._keystore_path, "w", encoding="utf-8") as fh:
            json.dump(self._accounts, fh, indent=2)

    # ------------------------------------------------------------- helpers
    def _address(self, account_id: str) -> str:
        if account_id in (_TREASURY_ID, "treasury"):
            return self.treasury.address
        return self._accounts[account_id]["address"]

    def _key(self, account_id: str):
        if account_id in (_TREASURY_ID, "treasury"):
            return self.treasury.key
        return self._accounts[account_id]["key"]

    def _to_wei(self, units: float) -> int:
        return int(round(max(0.0, float(units)) * self.scale))

    def _send(self, private_key, from_addr: str, to_addr: str,
              value_wei: int, reason: str = "") -> Optional[dict]:
        """Sign and broadcast a plain ETH transfer, then wait for the receipt."""
        value_wei = int(value_wei)
        if value_wei <= 0:
            return None

        w3 = self.w3
        nonce = w3.eth.get_transaction_count(from_addr, "pending")

        latest = w3.eth.get_block("latest")
        base_fee = int(latest.get("baseFeePerGas", w3.eth.gas_price))
        try:
            priority = int(w3.eth.max_priority_fee)
        except Exception:  # pragma: no cover - node dependent
            priority = w3.to_wei(1, "gwei")
        max_fee = base_fee * 2 + priority

        tx = {
            "chainId": self.chain_id,
            "nonce": nonce,
            "to": to_addr,
            "value": value_wei,
            "gas": 21_000,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority,
        }
        signed = w3.eth.account.sign_transaction(tx, private_key)
        raw = getattr(signed, "raw_transaction", None)
        if raw is None:  # older eth-account versions
            raw = signed.rawTransaction
        tx_hash = w3.eth.send_raw_transaction(raw)
        return w3.eth.wait_for_transaction_receipt(tx_hash, timeout=self.tx_timeout)

    # ------------------------------------------------------------- API
    def open_account(self, account_id: str, initial_balance: float = 0.0,
                     reason: str = "open") -> None:
        if account_id in self._accounts:
            return
        acct = self._Account.create()
        self._accounts[account_id] = {
            "address": acct.address,
            "key": acct.key.hex(),
        }
        self._save_keystore()

        # fund tracked balance + a gas reserve, all from the treasury
        target_wei = self._to_wei(initial_balance) + self.gas_reserve
        self._send(self.treasury.key, self.treasury.address, acct.address,
                   target_wei, reason=f"fund:{reason}")

    def exists(self, account_id: str) -> bool:
        return account_id in self._accounts or account_id in (_TREASURY_ID, "treasury")

    def balance(self, account_id: str) -> float:
        wei = self.w3.eth.get_balance(self._address(account_id))
        tracked = max(0, int(wei) - self.gas_reserve)
        return tracked / self.scale

    def credit(self, account_id: str, amount: float, reason: str) -> float:
        # move `amount` units from the treasury into the account (e.g. mercy restock)
        self._send(self.treasury.key, self.treasury.address,
                   self._address(account_id), self._to_wei(amount), reason=reason)
        return self.balance(account_id)

    def debit(self, account_id: str, amount: float, reason: str) -> float:
        self._send(self._key(account_id), self._address(account_id),
                   self.treasury.address, self._to_wei(amount), reason=reason)
        return self.balance(account_id)

    def set_balance(self, account_id: str, new_balance: float, reason: str) -> float:
        """Settle the account's on-chain balance to `new_balance` (mark-to-market)."""
        addr = self._address(account_id)
        current = int(self.w3.eth.get_balance(addr))
        target = self._to_wei(new_balance) + self.gas_reserve
        delta = target - current
        if delta > 0:
            # profited -> the house pays the account
            self._send(self.treasury.key, self.treasury.address, addr,
                       delta, reason=f"settle+:{reason}")
        elif delta < 0:
            # lost -> the account pays the house
            self._send(self._key(account_id), addr, self.treasury.address,
                       -delta, reason=f"settle-:{reason}")
        return self.balance(account_id)

    def transfer(self, src: str, dst: str, amount: float, reason: str) -> None:
        self._send(self._key(src), self._address(src), self._address(dst),
                   self._to_wei(amount), reason=reason)

    def treasury_address(self) -> str:
        return self.treasury.address

    def address_book(self) -> Dict[str, str]:
        """Map every known account id -> its on-chain address (treasury first)."""
        book: Dict[str, str] = {_TREASURY_ID: self.treasury.address}
        for acct_id, info in self._accounts.items():
            book[acct_id] = info["address"]
        return book

    def close(self) -> None:
        self._save_keystore()


def _print_treasury_address() -> None:
    """`python -m money_agent.chain` -> show the fund address + explorer links."""
    from .config import Config as _Config

    cfg = _Config(wallet_backend="base_sepolia")
    wallet = BaseSepoliaWallet(cfg)
    explorer = cfg.explorer_url.rstrip("/")
    treasury = wallet.treasury_address()

    print("Treasury (fund this address from a Base Sepolia faucet):")
    print(f"  address : {treasury}")
    print(f"  explorer: {explorer}/address/{treasury}")
    print(f"\nConnected RPC: {cfg.rpc_url}  chainId={cfg.chain_id}")
    bal = wallet.w3.eth.get_balance(treasury)
    print(f"Current treasury balance: {bal / 1e18:.6f} ETH")

    agents = {aid: addr for aid, addr in wallet.address_book().items()
              if aid != _TREASURY_ID}
    if agents:
        print(f"\nAgent wallets (from {cfg.keystore_path}) -- open any in a browser:")
        for aid, addr in agents.items():
            print(f"  {aid:<14} {explorer}/address/{addr}")
    else:
        print("\n(No agent wallets yet -- run the on-chain trainer to create some.)")


if __name__ == "__main__":
    _print_treasury_address()
