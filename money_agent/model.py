"""PolicyNetwork: the deep-learning "brain".

A small actor-critic MLP:
  * the actor head outputs logits over discrete trading actions,
  * the critic head estimates the value of a state (a baseline that makes
    training far more stable than plain REINFORCE).

Kept intentionally compact so a whole *population* of these can train on a CPU.
Swap the body for an LSTM/Transformer if you feed it longer market history.
"""

from __future__ import annotations

import torch
import torch.nn as nn


class PolicyNetwork(nn.Module):
    def __init__(self, obs_dim: int, n_actions: int, hidden: int = 64) -> None:
        super().__init__()
        self.body = nn.Sequential(
            nn.Linear(obs_dim, hidden),
            nn.Tanh(),
            nn.Linear(hidden, hidden),
            nn.Tanh(),
        )
        self.actor = nn.Linear(hidden, n_actions)
        self.critic = nn.Linear(hidden, 1)

    def forward(self, x: torch.Tensor):
        h = self.body(x)
        logits = self.actor(h)
        value = self.critic(h).squeeze(-1)
        return logits, value
