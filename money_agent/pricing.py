"""pricing.py: demand-aware dynamic pricing for the x402 services.

The earner watches how often each service is actually bought (from the sales log
the x402 server writes, ``x402_sales.json``) and nudges each service's price up
when demand is strong and down when demand is soft -- a simple, bounded
supply-and-demand loop. Prices are resolved **live, per request** through the
x402 price hook (the x402 server calls ``price(context)`` on every request), so
adjustments take effect immediately without a restart.

A snapshot of the current demand + prices is written to ``service_pricing.json``
and served at ``GET /pricing`` so the model / dashboard / a human can see what
the pricing loop is doing.

How the price moves
-------------------
* ``demand(service)``  = number of paid calls in the last ``PRICING_WINDOW_HOURS``.
* a service taking **more** than its fair share of traffic gets a **higher**
  multiplier (up to ``PRICING_MAX_MULT``); one taking **less** gets a lower one
  (down to ``PRICING_MIN_MULT``). No sales anywhere -> everyone stays at base.
* the multiplier is smoothed toward its target each recompute, so prices drift
  rather than jump.
* recompute is lazy with a short TTL (``PRICING_TTL_SECONDS``) so the per-request
  hook stays cheap.

Everything is pure-stdlib and fails safe: if the sales file is missing or
corrupt, prices simply stay at their base values.

Tuning knobs (all optional env vars):
    X402_DYNAMIC_PRICING   "1"/"0"  -- master switch (default on)
    PRICING_WINDOW_HOURS   float    -- demand look-back window (default 6)
    PRICING_MIN_MULT       float    -- floor multiplier (default 0.5)
    PRICING_MAX_MULT       float    -- ceiling multiplier (default 3.0)
    PRICING_SENSITIVITY    float    -- how hard demand moves price (default 1.0)
    PRICING_SMOOTH         float    -- 0..1 drift speed per recompute (default 0.3)
    PRICING_TTL_SECONDS    float    -- min seconds between recomputes (default 60)
    X402_PRICING_PATH      path     -- output snapshot (default service_pricing.json)
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Callable, Dict


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name, "").strip()
        return float(raw) if raw else default
    except (TypeError, ValueError):
        return default


def _usd(price_units: float) -> str:
    """Map an internal service price (sim units) to a small USDC dollar amount."""
    return f"${max(0.001, price_units / 1000.0):.3f}"


class DemandPricer:
    """Tracks per-service demand and resolves a demand-adjusted price on demand."""

    def __init__(self, base_prices: Dict[str, float], *,
                 sales_path: str = "x402_sales.json",
                 out_path: str = "service_pricing.json") -> None:
        self.base_prices: Dict[str, float] = {k: float(v) for k, v in base_prices.items()}
        self.sales_path = os.environ.get("X402_SALES_PATH", sales_path)
        self.out_path = os.environ.get("X402_PRICING_PATH", out_path)
        self.window_hours = _env_float("PRICING_WINDOW_HOURS", 6.0)
        self.min_mult = _env_float("PRICING_MIN_MULT", 0.5)
        self.max_mult = _env_float("PRICING_MAX_MULT", 3.0)
        self.sensitivity = _env_float("PRICING_SENSITIVITY", 1.0)
        self.smooth = min(1.0, max(0.0, _env_float("PRICING_SMOOTH", 0.3)))
        self.ttl = _env_float("PRICING_TTL_SECONDS", 60.0)
        self.enabled = os.environ.get("X402_DYNAMIC_PRICING", "1").strip().lower() \
            not in ("0", "false", "no", "off")
        self._lock = threading.Lock()
        self._mult: Dict[str, float] = {n: 1.0 for n in self.base_prices}
        self._last = 0.0
        self._analysis: Dict[str, object] = {}
        self._load_persisted()

    # -- price access -------------------------------------------------------
    def price_units(self, name: str) -> float:
        """Current demand-adjusted price for a service, in internal sim units."""
        if self.enabled:
            self._maybe_recompute()
        with self._lock:
            return self.base_prices.get(name, 0.0) * self._mult.get(name, 1.0)

    def usd(self, name: str) -> str:
        """Current price as a '$0.010'-style USDC string (what x402 expects)."""
        return _usd(self.price_units(name))

    def price_hook(self, name: str) -> Callable[[object], str]:
        """Return an x402 price callable resolved fresh on every request."""
        def hook(_ctx: object = None) -> str:
            return self.usd(name)
        return hook

    # -- demand analysis ----------------------------------------------------
    def _read_recent_sales(self) -> list:
        try:
            with open(self.sales_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return []
        return data.get("recent", []) if isinstance(data, dict) else []

    def _maybe_recompute(self) -> None:
        now = time.time()
        if now - self._last < self.ttl:
            return
        with self._lock:
            if time.time() - self._last < self.ttl:
                return
            self._recompute_locked(now)
            self._last = time.time()

    def _recompute_locked(self, now: float) -> None:
        recent = self._read_recent_sales()
        horizon = now - self.window_hours * 3600.0
        counts: Dict[str, int] = {n: 0 for n in self.base_prices}
        for sale in recent:
            name = sale.get("service") if isinstance(sale, dict) else None
            ts = sale.get("ts", 0) if isinstance(sale, dict) else 0
            if name in counts and isinstance(ts, (int, float)) and ts >= horizon:
                counts[name] += 1
        total = sum(counts.values())
        fair = total / max(1, len(self.base_prices)) if total else 0.0

        services: Dict[str, object] = {}
        for name, base_units in self.base_prices.items():
            c = counts[name]
            if total > 0 and fair > 0:
                target = 1.0 + self.sensitivity * ((c - fair) / fair)
            else:
                target = 1.0
            target = max(self.min_mult, min(self.max_mult, target))
            cur = self._mult.get(name, 1.0)
            new = cur + self.smooth * (target - cur)
            new = max(self.min_mult, min(self.max_mult, new))
            self._mult[name] = new
            services[name] = {
                "demand_window": c,
                "share": round(c / total, 4) if total else 0.0,
                "multiplier": round(new, 3),
                "base_usd": round(max(0.001, base_units / 1000.0), 4),
                "current_usd": round(max(0.001, base_units * new / 1000.0), 4),
            }
        self._analysis = {
            "updated_at": now,
            "window_hours": self.window_hours,
            "total_sales_in_window": total,
            "dynamic_pricing": self.enabled,
            "services": services,
        }
        self._persist_locked()

    def analysis(self) -> Dict[str, object]:
        """Current demand + pricing snapshot (safe to serve as JSON)."""
        if self.enabled:
            self._maybe_recompute()
        with self._lock:
            return dict(self._analysis)

    # -- persistence --------------------------------------------------------
    def _persist_locked(self) -> None:
        try:
            tmp = self.out_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._analysis, fh)
            os.replace(tmp, self.out_path)
        except OSError:
            pass

    def _load_persisted(self) -> None:
        try:
            with open(self.out_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        services = data.get("services", {}) if isinstance(data, dict) else {}
        for name, info in services.items():
            if name in self._mult and isinstance(info, dict):
                try:
                    m = float(info.get("multiplier", 1.0))
                    self._mult[name] = max(self.min_mult, min(self.max_mult, m))
                except (TypeError, ValueError):
                    pass
