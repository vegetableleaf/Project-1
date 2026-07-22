"""Population: manages every living iteration and its family tree.

Responsibilities:
  * seed the first generation,
  * open a ledger account for every new agent,
  * apply lifecycle events (terminate / clone),
  * bank realized profit into the shared vault when an agent reproduces,
  * enforce the population cap so the colony can't grow without bound,
  * keep a lineage log you can inspect after a run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np

from .agent import Agent
from .config import Config
from .ledger import Ledger


@dataclass
class LineageRecord:
    agent_id: str
    parent_id: str | None
    generation: int
    born_event: str


class Population:
    def __init__(self, config: Config, ledger: Ledger, rng: np.random.Generator,
                 obs_dim: int, n_actions: int, seed: bool = True) -> None:
        self.cfg = config
        self.ledger = ledger
        self.rng = rng
        self.obs_dim = obs_dim
        self.n_actions = n_actions

        self.agents: Dict[str, Agent] = {}
        self.lineage: List[LineageRecord] = []
        self._counter = 0

        # a shared "treasury" that accumulates profit banked by successful agents
        self.ledger.open_account(self.cfg.vault_account, 0.0, reason="vault_open")

        # `seed=False` is used when resuming from a checkpoint (agents are loaded
        # from disk instead of starting a brand-new genesis agent).
        if seed:
            self._seed()

    # --------------------------------------------------------------- creation
    def _next_id(self) -> str:
        self._counter += 1
        return f"agent-{self._counter:04d}"

    def _register(self, agent: Agent, born_event: str) -> None:
        self.agents[agent.agent_id] = agent
        self.ledger.open_account(agent.agent_id, self.cfg.starting_balance,
                                 reason=f"funded:{born_event}")
        self.lineage.append(LineageRecord(
            agent_id=agent.agent_id,
            parent_id=agent.parent_id,
            generation=agent.generation,
            born_event=born_event,
        ))

    def _seed(self) -> None:
        agent = Agent(
            agent_id=self._next_id(),
            config=self.cfg,
            obs_dim=self.obs_dim,
            n_actions=self.n_actions,
            generation=0,
            parent_id=None,
        )
        self._register(agent, born_event="genesis")

    def reseed(self) -> None:
        """Start a brand-new genesis agent after the colony has died out.

        Used by continuous ("run forever") training so a total extinction just
        begins a fresh lineage instead of ending the run.
        """
        self._seed()

    # ------------------------------------------------------------------ queries
    def alive_agents(self) -> List[Agent]:
        return [a for a in self.agents.values() if a.alive]

    def has_alive_agents(self) -> bool:
        return any(a.alive for a in self.agents.values())

    def population_size(self) -> int:
        return len(self.alive_agents())

    # -------------------------------------------------------------- lifecycle ops
    def terminate(self, agent: Agent) -> None:
        agent.alive = False

    def reproduce(self, parent: Agent) -> List[Agent]:
        """Bank the parent's profit and spawn mutated children.

        The parent keeps living but is reset to its starting stake (it has to
        keep earning); everything it made above `starting_balance` is moved to
        the shared vault as realized profit.
        """
        balance = self.ledger.balance(parent.agent_id)
        profit = max(0.0, balance - self.cfg.starting_balance)
        if profit > 0:
            self.ledger.transfer(parent.agent_id, self.cfg.vault_account,
                                 profit, reason="bank_profit")
        # reset parent stake so it re-earns from a clean slate
        self.ledger.set_balance(parent.agent_id, self.cfg.starting_balance,
                                reason="reinvest_reset")

        children: List[Agent] = []
        room = self.cfg.max_population - self.population_size()
        n_children = max(0, min(self.cfg.clone_count, room))
        for _ in range(n_children):
            child = parent.clone(self._next_id(), self.rng)
            self._register(child, born_event="clone")
            children.append(child)
        return children

    # ------------------------------------------------------------------ summary
    def summary(self) -> Dict[str, float]:
        alive = self.alive_agents()
        balances = [self.ledger.balance(a.agent_id) for a in alive]
        return {
            "alive": len(alive),
            "total_ever": len(self.agents),
            "max_generation": max((a.generation for a in self.agents.values()), default=0),
            "vault": self.ledger.balance(self.cfg.vault_account),
            "best_balance": max(balances) if balances else 0.0,
            "mean_balance": float(np.mean(balances)) if balances else 0.0,
        }
