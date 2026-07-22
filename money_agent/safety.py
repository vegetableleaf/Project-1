"""safety.py: spending guardrails for the AI's real money.

Two protections, both required before letting an agent touch real funds:

  * DAILY SPEND CAP -- the agent may spend at most 30% of
        (the day's STARTING balance  +  all revenue earned so far)
    in a single day. The cap resets each calendar day.

  * KILL SWITCH -- if the balance falls below $0.01, ALL spending is blocked.

State (today's starting balance and how much was spent today) lives in
spend_state.json so it survives restarts and rolls over automatically each day.

Enforce it by wrapping any real spend, e.g.:

    guard = SpendGuard(balance=usdc_balance, total_revenue=x402_revenue)
    ok, reason = guard.authorize(amount)
    if not ok:
        raise RuntimeError(f"blocked by safety guard: {reason}")
    ...  # do the actual on-chain send here
    guard.record(amount)

Inspect the current limits:
    python -m money_agent.safety
"""

from __future__ import annotations

import json
import os
from datetime import date

DAILY_CAP_FRACTION = 0.30       # 30% of (day-start balance + total revenue)
KILL_SWITCH_BALANCE = 0.01      # halt all spending below $0.01
STATE_PATH = os.environ.get("SPEND_STATE_PATH", "spend_state.json")


class SpendGuard:
    def __init__(self, balance: float, total_revenue: float,
                 state_path: str = STATE_PATH) -> None:
        self.balance = float(balance)
        self.total_revenue = float(total_revenue)
        self.state_path = state_path
        self._state = self._load()
        self._roll_day()

    # ------------------------------------------------------------- state
    def _load(self) -> dict:
        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._state, fh)
        os.replace(tmp, self.state_path)

    def _roll_day(self) -> None:
        """Start a fresh daily budget when the calendar day changes."""
        today = date.today().isoformat()
        if self._state.get("day") != today:
            self._state = {
                "day": today,
                "day_start_balance": self.balance,
                "spent_today": 0.0,
            }
            self._save()

    # ------------------------------------------------------------- limits
    def daily_cap(self) -> float:
        base = float(self._state.get("day_start_balance", self.balance))
        return DAILY_CAP_FRACTION * (base + self.total_revenue)

    def spent_today(self) -> float:
        return float(self._state.get("spent_today", 0.0))

    def remaining(self) -> float:
        return max(0.0, self.daily_cap() - self.spent_today())

    def kill_switch_tripped(self) -> bool:
        return self.balance < KILL_SWITCH_BALANCE

    # ------------------------------------------------------------- checks
    def authorize(self, amount: float) -> tuple[bool, str]:
        """Return (allowed, reason) for a proposed spend of `amount`."""
        amount = float(amount)
        if self.kill_switch_tripped():
            return False, (f"KILL SWITCH: balance ${self.balance:.4f} is below "
                           f"${KILL_SWITCH_BALANCE:.2f} -- all spending halted")
        if amount <= 0:
            return False, "amount must be positive"
        if amount > self.remaining():
            return False, (f"daily cap reached: ${amount:.4f} exceeds the "
                           f"${self.remaining():.4f} left of today's "
                           f"${self.daily_cap():.4f} cap")
        return True, "ok"

    def record(self, amount: float) -> None:
        """Record an actual spend against today's budget."""
        self._state["spent_today"] = self.spent_today() + float(amount)
        self._save()

    def status(self) -> dict:
        return {
            "balance": round(self.balance, 6),
            "kill_switch": self.kill_switch_tripped(),
            "kill_switch_threshold": KILL_SWITCH_BALANCE,
            "daily_cap": round(self.daily_cap(), 6),
            "spent_today": round(self.spent_today(), 6),
            "remaining_today": round(self.remaining(), 6),
            "day": self._state.get("day"),
        }


def guard_from_earnings(path: str = "earnings_status.json") -> SpendGuard:
    """Build a guard from the earnings snapshot (USDC balance + x402 revenue)."""
    balance = revenue = 0.0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        balance = float(data.get("usdc", 0.0))
        revenue = float(data.get("sales_usd", 0.0))
    except (OSError, ValueError):
        pass
    return SpendGuard(balance=balance, total_revenue=revenue)


def main() -> None:
    guard = guard_from_earnings()
    s = guard.status()
    print("=== spending safety ===")
    print(f"  balance          : ${s['balance']:.4f}")
    print(f"  kill switch      : {'TRIPPED (spending blocked)' if s['kill_switch'] else 'OK'}"
          f"  (trips below ${s['kill_switch_threshold']:.2f})")
    print(f"  daily cap (30%)  : ${s['daily_cap']:.4f}")
    print(f"  spent today      : ${s['spent_today']:.4f}")
    print(f"  remaining today  : ${s['remaining_today']:.4f}")
    # show a couple of example authorizations
    for amt in (0.01, s["remaining_today"] + 1.0):
        ok, reason = guard.authorize(amt)
        print(f"  authorize ${amt:.4f} -> {'ALLOW' if ok else 'DENY'}: {reason}")


if __name__ == "__main__":
    main()
