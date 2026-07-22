"""LifecycleManager: the rules that decide who lives, gets a second chance,
dies, or reproduces.

This is the heart of your design. After each trading episode, every agent's
wallet is checked against three thresholds:

    balance <= bankruptcy_threshold
        -> first time ever:  MERCY   (restock the wallet, force a behaviour change)
        -> already mercied:  TERMINATE (final death)

    balance >= target_balance
        -> CLONE  (bank the profit, spawn mutated children)

    otherwise
        -> CONTINUE
"""

from __future__ import annotations

import enum

from .agent import Agent
from .config import Config
from .ledger import Ledger


class Event(enum.Enum):
    CONTINUE = "continue"
    MERCY = "mercy"
    TERMINATE = "terminate"
    CLONE = "clone"


class LifecycleManager:
    def __init__(self, config: Config, ledger: Ledger) -> None:
        self.cfg = config
        self.ledger = ledger

    def evaluate(self, agent: Agent) -> Event:
        balance = self.ledger.balance(agent.agent_id)

        # ---- out of money ------------------------------------------------
        if balance <= self.cfg.bankruptcy_threshold:
            if agent.mercies_used < self.cfg.max_mercies:
                # grant the one-time second chance
                self.ledger.credit(agent.agent_id, self.cfg.restock_amount,
                                    reason="mercy_restock")
                agent.on_mercy()
                return Event.MERCY
            # no mercy left -> terminate
            agent.alive = False
            return Event.TERMINATE

        # ---- hit the target ---------------------------------------------
        if balance >= self.cfg.target_balance:
            return Event.CLONE

        # ---- keep trading -----------------------------------------------
        return Event.CONTINUE
