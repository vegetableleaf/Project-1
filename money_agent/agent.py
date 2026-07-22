"""Agent: one living iteration of the model.

An Agent bundles:
  * identity & lineage (id, generation, parent),
  * survival state (alive, mercies_used),
  * the policy network + optimizer,
  * an on-policy actor-critic learner (REINFORCE + value baseline),
  * the two behaviours that make the "evolutionary" story work:
        - on_mercy(): shake up behaviour after a second chance,
        - clone():    spawn a mutated child when the target is hit.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import List, Optional

import torch
import torch.nn.functional as F
from torch.distributions import Categorical

from .config import Config
from .model import PolicyNetwork


@dataclass
class Agent:
    agent_id: str
    config: Config
    obs_dim: int
    n_actions: int
    generation: int = 0
    parent_id: Optional[str] = None

    # survival state
    alive: bool = True
    mercies_used: int = 0

    # learning knobs (mutable so mercy/mutation can change "methods")
    learning_rate: float = None            # type: ignore
    entropy_coef: float = None             # type: ignore
    temperature: float = 1.0

    # internals
    model: PolicyNetwork = field(default=None, repr=False)      # type: ignore
    optimizer: torch.optim.Optimizer = field(default=None, repr=False)  # type: ignore

    # per-episode rollout buffers
    _log_probs: List[torch.Tensor] = field(default_factory=list, repr=False)
    _values: List[torch.Tensor] = field(default_factory=list, repr=False)
    _entropies: List[torch.Tensor] = field(default_factory=list, repr=False)
    _rewards: List[float] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.learning_rate is None:
            self.learning_rate = self.config.learning_rate
        if self.entropy_coef is None:
            self.entropy_coef = self.config.entropy_coef
        if self.model is None:
            self.model = PolicyNetwork(self.obs_dim, self.n_actions, self.config.hidden_size)
        if self.optimizer is None:
            self.optimizer = torch.optim.Adam(self.model.parameters(), lr=self.learning_rate)

    # ------------------------------------------------------------------ acting
    def act(self, obs) -> int:
        obs_t = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0)
        logits, value = self.model(obs_t)
        dist = Categorical(logits=logits / max(self.temperature, 1e-3))
        action = dist.sample()

        self._log_probs.append(dist.log_prob(action).squeeze(0))
        self._entropies.append(dist.entropy().squeeze(0))
        self._values.append(value.squeeze(0))
        return int(action.item())

    def record_reward(self, reward: float) -> None:
        self._rewards.append(float(reward))

    def reset_episode_memory(self) -> None:
        self._log_probs.clear()
        self._values.clear()
        self._entropies.clear()
        self._rewards.clear()

    # ---------------------------------------------------------------- learning
    def learn(self) -> Optional[float]:
        """Actor-critic update on the last episode using GAE. Returns the loss.

        Upgrades over plain REINFORCE:
          * Generalized Advantage Estimation (GAE-lambda) gives a lower-variance,
            better-credited learning signal than raw discounted returns, so the
            policy actually improves instead of stalling.
          * The learning rate decays toward a floor each update, so gradient
            descent takes big steps early and fine steps later.
        """
        if not self._rewards:
            self.reset_episode_memory()
            return None

        log_probs = torch.stack(self._log_probs)
        values = torch.stack(self._values)             # V(s_t) for each step
        entropies = torch.stack(self._entropies)
        vals = [v.item() for v in self._values]

        # --- Generalized Advantage Estimation (GAE-lambda) ---
        gamma, lam = self.config.gamma, self.config.gae_lambda
        advantages = [0.0] * len(self._rewards)
        gae = 0.0
        for t in reversed(range(len(self._rewards))):
            next_v = vals[t + 1] if t + 1 < len(vals) else 0.0   # bootstrap 0 at terminal
            delta = self._rewards[t] + gamma * next_v - vals[t]
            gae = delta + gamma * lam * gae
            advantages[t] = gae
        advantages_t = torch.tensor(advantages, dtype=torch.float32)
        returns_t = advantages_t + values.detach()     # value-function targets

        # normalize advantages -> a stable, well-scaled policy-gradient signal
        if advantages_t.numel() > 1:
            advantages_t = (advantages_t - advantages_t.mean()) / (advantages_t.std() + 1e-8)

        policy_loss = -(log_probs * advantages_t).mean()
        value_loss = F.mse_loss(values, returns_t)
        entropy_bonus = entropies.mean()

        loss = (policy_loss
                + self.config.value_coef * value_loss
                - self.entropy_coef * entropy_bonus)

        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
        self.optimizer.step()

        # gradient-descent refinement: decay the learning rate toward a floor
        for group in self.optimizer.param_groups:
            group["lr"] = max(self.config.min_learning_rate,
                              group["lr"] * self.config.lr_decay)

        self.reset_episode_memory()
        return float(loss.item())

    # -------------------------------------------------------------- second chance
    def on_mercy(self) -> None:
        """Called when the agent is granted a restock.

        The whole point of the mercy system is that the agent should *change its
        methods*, so we deliberately push it to explore again rather than repeat
        the behaviour that just bankrupted it: crank up exploration temporarily
        and nudge the weights out of the bad basin.
        """
        self.mercies_used += 1
        self.entropy_coef = self.config.entropy_coef * self.config.mercy_entropy_boost
        self.temperature = self.config.mercy_temperature
        with torch.no_grad():
            for p in self.model.parameters():
                p.add_(torch.randn_like(p) * self.config.mutation_rate)

    def anneal_exploration(self) -> None:
        """Gradually relax the post-mercy exploration back toward normal."""
        self.temperature = max(1.0, self.temperature * 0.9)
        self.entropy_coef = max(self.config.entropy_coef, self.entropy_coef * 0.9)

    # ------------------------------------------------------------------ cloning
    def clone(self, child_id: str, rng) -> "Agent":
        """Create a mutated child that inherits this agent's learned weights."""
        child_model = PolicyNetwork(self.obs_dim, self.n_actions, self.config.hidden_size)
        child_model.load_state_dict(copy.deepcopy(self.model.state_dict()))

        # mutate weights
        with torch.no_grad():
            for p in child_model.parameters():
                p.add_(torch.randn_like(p) * self.config.mutation_rate)

        # mutate hyperparameters (bounded jitter)
        jitter = self.config.hyperparam_jitter
        lr = float(self.learning_rate * (1.0 + rng.uniform(-jitter, jitter)))
        ent = float(self.config.entropy_coef * (1.0 + rng.uniform(-jitter, jitter)))

        child = Agent(
            agent_id=child_id,
            config=self.config,
            obs_dim=self.obs_dim,
            n_actions=self.n_actions,
            generation=self.generation + 1,
            parent_id=self.agent_id,
            learning_rate=max(1e-5, lr),
            entropy_coef=max(1e-4, ent),
            model=child_model,
        )
        return child
