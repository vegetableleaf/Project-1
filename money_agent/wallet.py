"""Wallet backends & selection.

The framework only ever talks to money through a small, wallet-shaped
interface (`WalletBackend`). That lets you swap the money store without
touching any agent, environment or lifecycle code:

  * "ledger"       -> Ledger        (offline SQLite; fast, no network, default)
  * "base_sepolia" -> BaseSepoliaWallet (real settlement on the Base Sepolia
                        TESTNET, whose ETH has no monetary value)

Add a real exchange/mainnet adapter later by implementing the same methods.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .config import Config


@runtime_checkable
class WalletBackend(Protocol):
    def open_account(self, account_id: str, initial_balance: float = 0.0,
                     reason: str = "open") -> None: ...
    def balance(self, account_id: str) -> float: ...
    def exists(self, account_id: str) -> bool: ...
    def credit(self, account_id: str, amount: float, reason: str) -> float: ...
    def debit(self, account_id: str, amount: float, reason: str) -> float: ...
    def set_balance(self, account_id: str, new_balance: float, reason: str) -> float: ...
    def transfer(self, src: str, dst: str, amount: float, reason: str) -> None: ...
    def close(self) -> None: ...


def make_wallet(config: Config) -> WalletBackend:
    """Build the money backend selected in the config."""
    backend = (config.wallet_backend or "ledger").strip().lower()

    if backend in ("ledger", "sqlite", "offline"):
        from .ledger import Ledger
        return Ledger(config.db_path)

    if backend in ("base_sepolia", "base-sepolia", "basesepolia", "chain"):
        from .chain import BaseSepoliaWallet
        return BaseSepoliaWallet(config)

    raise ValueError(
        f"Unknown wallet_backend {config.wallet_backend!r}. "
        "Use 'ledger' or 'base_sepolia'."
    )
