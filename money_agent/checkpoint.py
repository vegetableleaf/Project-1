"""checkpoint.py: save & restore training progress so you never lose it.

Two file kinds -- very different jobs:

  * .pth  -> the "save game". Full training state: every agent's brain AND its
             learning progress, plus the family tree. Load it to resume EXACTLY
             where you left off. This is what protects your progress.

  * .onnx -> a portable "snapshot" of one brain in a universal format, for
             running the model in other tools/languages. You CANNOT resume
             training from ONNX -- it's a flattened export, like a PDF of a
             document you can no longer easily edit.

Money is NOT stored here. The wallet backend already persists it (the SQLite
ledger file, or the blockchain itself). Checkpoints persist the neural networks.

SECURITY NOTE
-------------
Loading a .pth uses Python's pickle (weights_only=False) so it can restore the
optimizer state and family tree. Only ever load checkpoint files you created
yourself -- never one downloaded from an untrusted source.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import warnings
from dataclasses import asdict

import torch

from .agent import Agent
from .config import Config
from .population import LineageRecord, Population

_CKPT_VERSION = 1


# --------------------------------------------------------------------------- .pth
def save_checkpoint(population: Population, config: Config, path: str,
                    generations_done: int = 0) -> None:
    """Write the whole colony (all brains + lineage) to a .pth file."""
    state = {
        "version": _CKPT_VERSION,
        "obs_dim": population.obs_dim,
        "n_actions": population.n_actions,
        "counter": population._counter,
        "generations_done": int(generations_done),
        "lineage": [asdict(rec) for rec in population.lineage],
        "agents": [_agent_to_state(a) for a in population.agents.values()],
        "torch_rng": torch.get_rng_state(),
    }
    # write to a temp file first, then swap it in, so a crash mid-save can never
    # leave you with a half-written (corrupt) checkpoint.
    tmp = f"{path}.tmp"
    torch.save(state, tmp)
    os.replace(tmp, path)


def load_checkpoint(population: Population, config: Config, path: str) -> int:
    """Restore agents + lineage into `population`. Returns generations_done."""
    state = torch.load(path, map_location="cpu", weights_only=False)
    if state.get("version") != _CKPT_VERSION:
        print(f"  (warning: checkpoint version {state.get('version')} "
              f"!= expected {_CKPT_VERSION}; attempting to load anyway)")

    population._counter = int(state["counter"])
    population.lineage = [LineageRecord(**rec) for rec in state["lineage"]]
    population.agents = {}
    for a_state in state["agents"]:
        agent = _agent_from_state(a_state, config,
                                  population.obs_dim, population.n_actions)
        population.agents[agent.agent_id] = agent
        # ensure the wallet has an account for this agent (no-op if it exists)
        if not population.ledger.exists(agent.agent_id):
            population.ledger.open_account(agent.agent_id, config.starting_balance,
                                           reason="resume")
    with contextlib.suppress(Exception):
        torch.set_rng_state(state["torch_rng"])
    return int(state.get("generations_done", 0))


def _agent_to_state(agent: Agent) -> dict:
    return {
        "agent_id": agent.agent_id,
        "generation": agent.generation,
        "parent_id": agent.parent_id,
        "alive": agent.alive,
        "mercies_used": agent.mercies_used,
        "learning_rate": agent.learning_rate,
        "entropy_coef": agent.entropy_coef,
        "temperature": agent.temperature,
        "model": agent.model.state_dict(),
        "optimizer": agent.optimizer.state_dict(),
    }


def _agent_from_state(s: dict, config: Config, obs_dim: int, n_actions: int) -> Agent:
    agent = Agent(
        agent_id=s["agent_id"],
        config=config,
        obs_dim=obs_dim,
        n_actions=n_actions,
        generation=s["generation"],
        parent_id=s["parent_id"],
        learning_rate=s["learning_rate"],
        entropy_coef=s["entropy_coef"],
    )
    agent.alive = bool(s["alive"])
    agent.mercies_used = int(s["mercies_used"])
    agent.temperature = float(s["temperature"])
    agent.model.load_state_dict(s["model"])
    agent.optimizer.load_state_dict(s["optimizer"])
    return agent


# -------------------------------------------------------------------------- .onnx
def export_onnx(agent: Agent, path: str) -> None:
    """Export one agent's policy network to a portable .onnx file (quietly)."""
    model = agent.model
    was_training = model.training
    model.eval()
    dummy = torch.zeros(1, agent.obs_dim, dtype=torch.float32)
    try:
        # the exporter is chatty; silence its logs/warnings so training output
        # stays readable. Real failures still raise and are handled by callers.
        with warnings.catch_warnings(), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            warnings.simplefilter("ignore")
            logging.getLogger("torch.onnx").setLevel(logging.ERROR)
            torch.onnx.export(
                model, dummy, path,
                input_names=["observation"],
                output_names=["action_logits", "value"],
                opset_version=18,
                dynamo=True,
                verbose=False,
            )
        _inline_onnx_weights(path)  # keep it a single, portable file
    finally:
        if was_training:
            model.train()


def _inline_onnx_weights(path: str) -> None:
    """Fold any external-data sidecar (`<file>.onnx.data`) back into one file.

    The modern exporter can split the weights into a separate .data file; a
    single self-contained .onnx is easier to copy around, so we re-save inline.
    """
    try:
        import onnx
    except ImportError:
        return
    model = onnx.load(path)  # reads the external .data sidecar if present
    onnx.save_model(model, path, save_as_external_data=False)
    sidecar = f"{path}.data"
    if os.path.exists(sidecar):
        os.remove(sidecar)


def export_population_onnx(population: Population, onnx_dir: str) -> int:
    """Export every ALIVE agent's brain to onnx_dir/<agent_id>.onnx.

    Returns how many were exported. Never raises: an ONNX failure (e.g. the
    optional onnx package isn't installed) just prints a note and is skipped,
    so it can't interrupt training or the .pth save.
    """
    os.makedirs(onnx_dir, exist_ok=True)
    exported = 0
    for agent in population.agents.values():
        if not agent.alive:
            continue
        try:
            export_onnx(agent, os.path.join(onnx_dir, f"{agent.agent_id}.onnx"))
            exported += 1
        except Exception as exc:  # noqa: BLE001 - best effort, never fatal
            print(f"  (onnx export skipped for {agent.agent_id}: "
                  f"{type(exc).__name__}: {exc})")
    return exported
