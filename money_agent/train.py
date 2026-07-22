"""train.py: wires everything together and runs the colony.

Run it with:

    python -m money_agent.train

The loop, each generation:
  1. every living agent trades one episode (its wallet is its capital),
  2. the wallet is marked-to-market in the ledger,
  3. the agent learns from the episode,
  4. the LifecycleManager decides: continue / mercy / terminate / clone,
  5. the Population applies that decision (spawning children on success).

Stops when the colony dies out or `max_generations` is reached.
"""

from __future__ import annotations

import json
import os
import time

import numpy as np
import torch

from .agent import Agent
from .checkpoint import export_population_onnx, load_checkpoint, save_checkpoint
from .config import Config
from .environment import TradingEnv
from .lifecycle import Event, LifecycleManager
from .population import Population
from .wallet import WalletBackend, make_wallet


def run_episode(agent: Agent, env: TradingEnv, ledger: WalletBackend, cfg: Config) -> tuple[float, float | None]:
    """Trade one episode using the agent's current wallet as starting capital."""
    start_cash = ledger.balance(agent.agent_id)
    obs = env.reset(start_cash)
    agent.reset_episode_memory()

    final_value = start_cash
    done = False
    while not done:
        action = agent.act(obs)
        obs, reward, done, info = env.step(action)
        agent.record_reward(reward)
        final_value = info["portfolio_value"]

    # persist the result to the ledger (money carries across episodes)
    ledger.set_balance(agent.agent_id, max(0.0, final_value), reason="episode_pnl")
    loss = agent.learn()
    agent.anneal_exploration()
    return final_value, loss


def _write_status(cfg: Config, generation: int, summary: dict, avg_loss: float = 0.0) -> None:
    """Write a tiny status.json the dashboard reads (clean UTF-8, no log parsing)."""
    data = {
        "generation": generation,
        "alive": summary["alive"],
        "best_balance": summary["best_balance"],
        "mean_balance": summary["mean_balance"],
        "vault": summary["vault"],
        "avg_loss": avg_loss,
        "updated": time.time(),
    }
    tmp = cfg.status_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, cfg.status_path)
    except OSError:
        pass


def train(cfg: Config | None = None, *, forever: bool = False) -> Population:
    cfg = cfg or Config()

    torch.manual_seed(cfg.seed)
    rng = np.random.default_rng(cfg.seed)

    ledger = make_wallet(cfg)
    print(f"wallet backend: {cfg.wallet_backend}")
    if forever:
        print("continuous mode: training keeps running -- press Ctrl+C to stop.")

    # --- market data: real candles (default) or the synthetic simulator ---
    price_pool = None
    if cfg.data_source == "real":
        from .data import load_prices, train_test_split
        prices = load_prices(cfg.data_product, cfg.data_granularity,
                             cfg.data_candles, cache_path=cfg.data_cache or None)
        if len(prices) >= (cfg.window + cfg.horizon + 1):
            price_pool, _ = train_test_split(prices, cfg.data_train_frac)
            held = len(prices) - len(price_pool)
            print(f"real data: {len(prices)} {cfg.data_product} candles "
                  f"({len(price_pool)} train / {held} held out for evaluation)")
        else:
            print(f"real data unavailable ({len(prices)} candles) -- using synthetic.")
    env = TradingEnv(cfg, rng, price_pool=price_pool)

    # --- resume from a checkpoint if one exists --------------------------
    resuming = cfg.resume and os.path.exists(cfg.checkpoint_path)
    population = Population(cfg, ledger, rng, cfg.obs_dim(), cfg.action_dim(),
                           seed=not resuming)
    total_gen = 0
    if resuming:
        total_gen = load_checkpoint(population, cfg, cfg.checkpoint_path)
        s = population.summary()
        print(f"resumed from {cfg.checkpoint_path}: {s['total_ever']} agents "
              f"({s['alive']} alive), {total_gen} generations trained so far.")

    lifecycle = LifecycleManager(cfg, ledger)

    def _save() -> None:
        save_checkpoint(population, cfg, cfg.checkpoint_path, total_gen)
        if cfg.export_onnx:
            export_population_onnx(population, cfg.onnx_dir)

    session_gen = 0
    try:
        while forever or session_gen < cfg.max_generations:
            if not population.has_alive_agents():
                if forever and cfg.reseed_on_extinction:
                    print(f"[gen {total_gen:05d}] colony extinct -- reseeding a fresh agent.")
                    population.reseed()
                else:
                    print(f"[gen {total_gen:05d}] colony extinct -- no agents left alive.")
                    break

            events = {e: 0 for e in Event}
            losses: list[float] = []
            # iterate over a snapshot: children born this gen start next gen
            for agent in list(population.alive_agents()):
                _, loss = run_episode(agent, env, ledger, cfg)
                if loss is not None:
                    losses.append(loss)
                event = lifecycle.evaluate(agent)
                events[event] += 1

                if event is Event.CLONE:
                    population.reproduce(agent)
                elif event is Event.TERMINATE:
                    population.terminate(agent)
                # MERCY / CONTINUE need no population-level change

            avg_loss = sum(losses) / len(losses) if losses else 0.0
            if total_gen % cfg.log_every == 0:
                s = population.summary()
                print(
                    f"[gen {total_gen:05d}] alive={s['alive']:>2} "
                    f"gen_max={s['max_generation']:>2} "
                    f"best=${s['best_balance']:>9.2f} "
                    f"mean=${s['mean_balance']:>9.2f} "
                    f"vault=${s['vault']:>10.2f} loss={avg_loss:+.4f} | "
                    f"clone={events[Event.CLONE]} mercy={events[Event.MERCY]} "
                    f"term={events[Event.TERMINATE]}"
                )
                _write_status(cfg, total_gen, s, avg_loss)

            session_gen += 1
            total_gen += 1
            if cfg.checkpoint_every and (total_gen % cfg.checkpoint_every == 0):
                _save()

            if cfg.loop_delay > 0:
                time.sleep(cfg.loop_delay)
    except KeyboardInterrupt:
        print("\nstopped by user (Ctrl+C).")
    finally:
        # always save on the way out (normal end, Ctrl+C, or crash) so closing
        # the device never loses progress.
        _save()
        extra = f"  (+ ONNX in {cfg.onnx_dir}/)" if cfg.export_onnx else ""
        print(f"checkpoint saved -> {cfg.checkpoint_path}{extra}")

    print("\n=== final summary ===")
    for k, v in population.summary().items():
        print(f"  {k}: {v}")
    print("\nlineage (id <- parent, gen, born):")
    for rec in population.lineage:
        print(f"  {rec.agent_id} <- {rec.parent_id} "
              f"(gen {rec.generation}, {rec.born_event})")

    ledger.close()
    return population


def train_forever(cfg: Config | None = None) -> Population:
    """Run training continuously until you stop it with Ctrl+C.

    Same as train(), except it never ends on its own: if the whole colony dies
    out it starts a fresh agent and keeps going, so you don't have to relaunch
    the trainer by hand.
    """
    return train(cfg, forever=True)


if __name__ == "__main__":
    # Optional convenience overrides via environment variables, e.g.
    #   $env:MONEY_AGENT_BACKEND = "base_sepolia"   (PowerShell)
    #   $env:MONEY_AGENT_GENERATIONS = "5"
    #   $env:MONEY_AGENT_FOREVER = "1"              (never stop; Ctrl+C to quit)
    overrides = {}
    if os.environ.get("MONEY_AGENT_BACKEND"):
        overrides["wallet_backend"] = os.environ["MONEY_AGENT_BACKEND"]
    if os.environ.get("MONEY_AGENT_GENERATIONS"):
        overrides["max_generations"] = int(os.environ["MONEY_AGENT_GENERATIONS"])
    if os.environ.get("MONEY_AGENT_RPC_URL"):
        overrides["rpc_url"] = os.environ["MONEY_AGENT_RPC_URL"]
    if os.environ.get("MONEY_AGENT_LOOP_DELAY"):
        overrides["loop_delay"] = float(os.environ["MONEY_AGENT_LOOP_DELAY"])
    if os.environ.get("MONEY_AGENT_DATA"):
        overrides["data_source"] = os.environ["MONEY_AGENT_DATA"]

    forever = os.environ.get("MONEY_AGENT_FOREVER", "").strip().lower() in (
        "1", "true", "yes", "on")
    train(Config(**overrides), forever=forever)
