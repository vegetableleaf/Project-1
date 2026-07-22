"""Ledger: money storage & tracking.

WHY NOT A CRYPTO WALLET?
------------------------
For building, training and stress-testing an autonomous money-making agent,
a real on-chain crypto wallet is the *hardest* possible way to keep score:
  * every balance read/write costs gas and network latency,
  * mistakes are irreversible and can lose real funds,
  * an agent that can move real crypto autonomously is a serious security and
    legal liability while it is still learning (and it *will* make bad trades).

A local double-entry ledger (this module, backed by SQLite) is far simpler and
safer: instant reads/writes, full transaction history, trivially resettable,
and no real money at risk. Keep money virtual until the policy is proven, then
swap this class for an exchange *paper-trading* account, and only much later a
real wallet -- behind strict, human-approved risk limits.

The public API deliberately looks like a wallet (open/balance/credit/debit/
transfer) so a real wallet adapter can be dropped in later without touching the
rest of the framework.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Optional


class Ledger:
    def __init__(self, db_path: str = ":memory:") -> None:
        # check_same_thread=False keeps things simple if you later parallelize.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        # TRUNCATE journal (not WAL): every commit lands directly in the main
        # .sqlite file, so external readers -- like the Docker dashboard reading
        # the file over a bind mount -- always see fully up-to-date, consistent
        # data. (WAL keeps recent writes in a side file that bind-mounted readers
        # can't reliably read.) synchronous=NORMAL keeps writes fast; a busy
        # timeout lets the dashboard and trainer share the file without errors.
        self._conn.execute("PRAGMA journal_mode=TRUNCATE;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._init_schema()

    # ------------------------------------------------------------------ schema
    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                account_id TEXT PRIMARY KEY,
                balance    REAL NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                delta      REAL NOT NULL,
                balance    REAL NOT NULL,
                reason     TEXT NOT NULL,
                ts         REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    # ---------------------------------------------------------------- accounts
    def open_account(self, account_id: str, initial_balance: float = 0.0,
                     reason: str = "open") -> None:
        """Create an account. No-op if it already exists."""
        cur = self._conn.cursor()
        row = cur.execute(
            "SELECT 1 FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        if row is not None:
            return
        now = time.time()
        cur.execute(
            "INSERT INTO accounts (account_id, balance, created_at) VALUES (?, ?, ?)",
            (account_id, float(initial_balance), now),
        )
        self._log(cur, account_id, float(initial_balance), float(initial_balance), reason)
        self._conn.commit()

    def balance(self, account_id: str) -> float:
        row = self._conn.execute(
            "SELECT balance FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown account: {account_id!r}")
        return float(row[0])

    def exists(self, account_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone() is not None

    # ------------------------------------------------------------- mutations
    def adjust(self, account_id: str, delta: float, reason: str) -> float:
        """Apply a signed change and return the new balance."""
        cur = self._conn.cursor()
        new_balance = self.balance(account_id) + float(delta)
        cur.execute(
            "UPDATE accounts SET balance = ? WHERE account_id = ?",
            (new_balance, account_id),
        )
        self._log(cur, account_id, float(delta), new_balance, reason)
        self._conn.commit()
        return new_balance

    def credit(self, account_id: str, amount: float, reason: str) -> float:
        return self.adjust(account_id, abs(float(amount)), reason)

    def debit(self, account_id: str, amount: float, reason: str) -> float:
        return self.adjust(account_id, -abs(float(amount)), reason)

    def set_balance(self, account_id: str, new_balance: float, reason: str) -> float:
        """Force the balance to an absolute value (used to mark-to-market)."""
        delta = float(new_balance) - self.balance(account_id)
        return self.adjust(account_id, delta, reason)

    def transfer(self, src: str, dst: str, amount: float, reason: str) -> None:
        amount = abs(float(amount))
        self.debit(src, amount, f"{reason}:out")
        self.credit(dst, amount, f"{reason}:in")

    # --------------------------------------------------------------- reporting
    def history(self, account_id: str, limit: Optional[int] = None):
        sql = ("SELECT delta, balance, reason, ts FROM transactions "
               "WHERE account_id = ? ORDER BY id")
        params: tuple = (account_id,)
        if limit is not None:
            sql += " DESC LIMIT ?"
            params = (account_id, int(limit))
        return self._conn.execute(sql, params).fetchall()

    def total_supply(self) -> float:
        row = self._conn.execute("SELECT COALESCE(SUM(balance), 0) FROM accounts").fetchone()
        return float(row[0])

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _log(cur, account_id: str, delta: float, balance: float, reason: str) -> None:
        cur.execute(
            "INSERT INTO transactions (account_id, delta, balance, reason, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (account_id, delta, balance, reason, time.time()),
        )

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()
