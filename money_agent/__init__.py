"""money_agent: a simulation-first framework for a self-replicating,
budget-constrained reinforcement-learning trading agent.

The package implements:
  * Ledger        -> money tracking (SQLite, a safer alternative to a live crypto wallet)
  * TradingEnv    -> a market simulator that turns actions into profit/loss
  * PolicyNetwork -> the deep-learning policy (PyTorch actor-critic)
  * Agent         -> an RL agent that can learn, mutate and clone itself
  * LifecycleManager -> the mercy / termination / reproduction rules
  * Population    -> manages every living iteration and its lineage
"""

from .config import Config

__all__ = ["Config"]
