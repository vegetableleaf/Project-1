"""TradingEnv: the "world" the agent lives in.

This is a self-contained market simulator so the whole framework runs offline
with no exchange account and no real money. It generates a synthetic price path
(geometric Brownian motion) and lets the agent choose how much of its capital to
expose to the asset each step. Profit/loss is real mark-to-market accounting so
the agent genuinely has to trade well to grow its wallet.

To go from simulation -> paper trading -> live, replace `reset()` / `step()`
with calls to an exchange sandbox API. The observation/reward contract stays the
same, so nothing else in the framework needs to change.
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

import numpy as np

from .config import Config


class TradingEnv:
    def __init__(self, config: Config, rng: np.random.Generator,
                 price_pool: Optional[np.ndarray] = None) -> None:
        self.cfg = config
        self.rng = rng
        self.k = config.window
        self.horizon = config.horizon
        self.fee_rate = config.fee_rate
        self.fractions = config.action_fractions

        # a pool of REAL prices to sample episodes from (None -> synthetic GBM)
        self.price_pool = price_pool

        # runtime state (set in reset)
        self.prices: np.ndarray = np.array([])
        self.t = 0
        self.cash = 0.0
        self.units = 0.0
        self.start_value = 0.0

    # ------------------------------------------------------------------ price
    def _generate_prices(self) -> np.ndarray:
        n = self.k + self.horizon + 1
        shocks = self.rng.normal(
            loc=self.cfg.drift - 0.5 * self.cfg.volatility**2,
            scale=self.cfg.volatility,
            size=n - 1,
        )
        log_prices = np.concatenate([[0.0], np.cumsum(shocks)])
        return 100.0 * np.exp(log_prices)  # start every path at price 100

    def _make_prices(self) -> np.ndarray:
        """A real price window when we have a pool, else a synthetic GBM path."""
        need = self.k + self.horizon + 1
        pool = self.price_pool
        if pool is not None and len(pool) >= need:
            start = int(self.rng.integers(0, len(pool) - need + 1))
            return pool[start:start + need].astype(np.float64)
        return self._generate_prices()

    # ------------------------------------------------------------------ reset
    def reset(self, starting_cash: float) -> np.ndarray:
        self.prices = self._make_prices()
        self.t = self.k
        self.cash = float(starting_cash)
        self.units = 0.0
        self.start_value = float(starting_cash)
        return self._obs()

    # ------------------------------------------------------------------- step
    def step(self, action: int) -> Tuple[np.ndarray, float, bool, Dict]:
        price = self.prices[self.t]
        pv_before = self.cash + self.units * price

        # --- rebalance to the target exposure the action selected ---
        target_fraction = self.fractions[int(action)]
        target_asset_value = target_fraction * pv_before
        trade_notional = target_asset_value - self.units * price
        fee = abs(trade_notional) * self.fee_rate

        self.cash -= trade_notional      # buying (>0) spends cash
        self.cash -= fee                 # fees always cost money
        self.units = target_asset_value / price if price > 0 else 0.0

        # --- advance the market one step ---
        self.t += 1
        new_price = self.prices[self.t]
        pv_after = self.cash + self.units * new_price

        # log-return reward keeps the scale stable and compounds naturally
        reward = math.log(max(pv_after, 1e-9) / max(pv_before, 1e-9))

        bankrupt = pv_after <= self.cfg.bankruptcy_threshold
        done = bool(self.t >= self.k + self.horizon or bankrupt)

        info = {
            "portfolio_value": float(pv_after),
            "price": float(new_price),
            "bankrupt": bool(bankrupt),
        }
        return self._obs(), float(reward), done, info

    # ----------------------------------------------------------- observation
    def _obs(self) -> np.ndarray:
        window_prices = self.prices[self.t - self.k: self.t + 1]
        log_returns = np.diff(np.log(window_prices))  # length == k

        price = self.prices[self.t]
        pv = self.cash + self.units * price
        position_fraction = (self.units * price) / pv if pv > 0 else 0.0
        normalized_value = pv / self.start_value if self.start_value > 0 else 0.0

        obs = np.concatenate([
            log_returns,
            [position_fraction, normalized_value],
        ]).astype(np.float32)
        return obs
