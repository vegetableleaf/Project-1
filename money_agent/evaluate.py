"""evaluate.py: honest, OUT-OF-SAMPLE evaluation of the trained model.

This is the tool that tells you whether the model is actually any good -- judged
not on the data it trained on, but on REAL market data it has never seen (the
held-out test split). It reports risk-adjusted metrics and compares against
simply buying and holding, which is the bar any trading model must beat to be
worth running at all.

    python -m money_agent.evaluate

It loads every agent from checkpoint.pth, scores each on the unseen test data,
and reports the best one plus a plain-English verdict.
"""

from __future__ import annotations

import math
import os
from typing import List

import numpy as np
import torch

from .agent import Agent
from .config import Config
from .environment import TradingEnv


def _greedy_action(agent: Agent, obs) -> int:
    """Deterministic action (no exploration) -- how you'd actually deploy it."""
    with torch.no_grad():
        logits, _ = agent.model(torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0))
        return int(torch.argmax(logits, dim=-1).item())


def _max_drawdown(equity: List[float]) -> float:
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd


def _run_episode(agent: Agent, env: TradingEnv, start_cash: float):
    obs = env.reset(start_cash)
    prices = env.prices.copy()
    k, horizon = env.k, env.horizon
    step_returns: List[float] = []
    equity = [start_cash]
    done, final = False, start_cash
    while not done:
        obs, reward, done, info = env.step(_greedy_action(agent, obs))
        step_returns.append(reward)
        equity.append(info["portfolio_value"])
        final = info["portfolio_value"]
    agent_return = final / start_cash - 1.0
    buy_hold_return = prices[k + horizon] / prices[k] - 1.0   # just hold the asset
    return agent_return, buy_hold_return, step_returns, equity


def evaluate_agent(agent: Agent, test_pool: np.ndarray, cfg: Config,
                   episodes: int = 100, seed: int = 123) -> dict:
    env = TradingEnv(cfg, np.random.default_rng(seed), price_pool=test_pool)
    a_rets, bh_rets, all_steps = [], [], []
    wins = beats = 0
    worst_dd = 0.0
    for _ in range(episodes):
        a, bh, steps, equity = _run_episode(agent, env, cfg.starting_balance)
        a_rets.append(a)
        bh_rets.append(bh)
        wins += int(a > 0)
        beats += int(a > bh)
        all_steps.extend(steps)
        worst_dd = max(worst_dd, _max_drawdown(equity))

    a_rets = np.array(a_rets)
    bh_rets = np.array(bh_rets)
    steps = np.array(all_steps)
    periods_per_year = (365 * 24 * 3600) / max(1, cfg.data_granularity)
    sharpe = 0.0
    if steps.size > 1 and steps.std() > 0:
        sharpe = float(steps.mean() / steps.std() * math.sqrt(periods_per_year))

    return {
        "mean_return": float(a_rets.mean()),
        "median_return": float(np.median(a_rets)),
        "win_rate": wins / episodes,
        "beat_bh_rate": beats / episodes,
        "bh_mean_return": float(bh_rets.mean()),
        "sharpe": sharpe,
        "worst_drawdown": worst_dd,
    }


def main() -> None:
    cfg = Config(data_source="real")
    from .data import load_prices, train_test_split

    prices = load_prices(cfg.data_product, cfg.data_granularity, cfg.data_candles,
                         cache_path=cfg.data_cache or None)
    need = cfg.window + cfg.horizon + 1
    if len(prices) < need * 2:
        print(f"Not enough real data ({len(prices)} candles). First run:\n"
              f"    python -m money_agent.data {cfg.data_product}")
        return
    _, test_pool = train_test_split(prices, cfg.data_train_frac)
    if len(test_pool) < need:
        print(f"Test split too short ({len(test_pool)} < {need}); "
              f"increase Config.data_candles.")
        return

    if not os.path.exists(cfg.checkpoint_path):
        print(f"No checkpoint at {cfg.checkpoint_path}. Train first with real data:\n"
              f"    $env:MONEY_AGENT_DATA = 'real'; python -m money_agent.train")
        return

    from .checkpoint import load_checkpoint
    from .ledger import Ledger
    from .population import Population

    ledger = Ledger(":memory:")
    pop = Population(cfg, ledger, np.random.default_rng(0),
                    cfg.obs_dim(), cfg.action_dim(), seed=False)
    load_checkpoint(pop, cfg, cfg.checkpoint_path)
    agents = [a for a in pop.agents.values() if a.alive] or list(pop.agents.values())
    if not agents:
        print("No agents found in the checkpoint.")
        return

    print(f"Evaluating {len(agents)} agent(s) on {len(test_pool)} UNSEEN candles "
          f"of {cfg.data_product} (granularity {cfg.data_granularity}s)...\n")
    scored = [(a, evaluate_agent(a, test_pool, cfg)) for a in agents]
    scored.sort(key=lambda r: r[1]["mean_return"], reverse=True)
    best_agent, m = scored[0]

    print(f"=== BEST MODEL: {best_agent.agent_id} (generation {best_agent.generation}) ===")
    print(f"  avg return / episode : {m['mean_return']*100:+.2f}%")
    print(f"  buy & hold baseline  : {m['bh_mean_return']*100:+.2f}%")
    print(f"  win rate             : {m['win_rate']*100:.0f}% of episodes profitable")
    print(f"  beats buy & hold     : {m['beat_bh_rate']*100:.0f}% of episodes")
    print(f"  annualized Sharpe    : {m['sharpe']:.2f}")
    print(f"  worst drawdown       : {m['worst_drawdown']*100:.1f}%")

    print("\n=== VERDICT ===")
    if m["mean_return"] <= m["bh_mean_return"]:
        print("  - Does NOT beat buy-and-hold -> NOT ready. Just holding is simpler and better.")
    else:
        print("  - Beats buy-and-hold on average (on this test window).")
    if m["sharpe"] < 1.0:
        print(f"  - Sharpe {m['sharpe']:.2f} < 1.0 -> weak risk-adjusted return.")
    else:
        print(f"  - Sharpe {m['sharpe']:.2f} >= 1.0 -> reasonable risk-adjusted return.")
    print("  - Even good out-of-sample numbers ignore real slippage, latency, fees on")
    print("    small balances, and regime change. Never risk money you can't lose.")


if __name__ == "__main__":
    main()
