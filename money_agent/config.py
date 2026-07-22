"""Central configuration for the whole framework.

Everything is a plain dataclass so you can tweak the "rules of the game"
(starting money, mercy restock, target, cloning, etc.) in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


@dataclass
class Config:
    # ---- Reproducibility -------------------------------------------------
    seed: int = 42

    # ---- Money / economy (all values are in "simulation dollars") --------
    starting_balance: float = 1_000.0     # what every new iteration is funded with
    restock_amount: float = 500.0         # the one-time "second chance" mercy top-up
    target_balance: float = 2_000.0       # reach this -> the agent clones itself
    bankruptcy_threshold: float = 1.0     # balance <= this -> the agent is "out of money"

    # ---- Mercy / termination rules --------------------------------------
    max_mercies: int = 1                  # how many restocks before termination is final

    # ---- Reproduction ----------------------------------------------------
    clone_count: int = 3                  # children spawned when the target is hit
    max_population: int = 16              # hard cap so the colony can't explode
    mutation_rate: float = 0.02           # gaussian noise added to a child's weights
    hyperparam_jitter: float = 0.15       # relative jitter applied to a child's hyperparams

    # ---- Market simulator ------------------------------------------------
    window: int = 12                      # number of past returns the agent observes
    horizon: int = 128                    # steps (candles) per trading episode
    fee_rate: float = 0.001               # transaction fee per unit notional traded
    drift: float = 0.0005                 # GBM drift per step
    volatility: float = 0.02              # GBM volatility per step
    action_fractions: Tuple[float, ...] = (0.0, 0.5, 1.0)  # target exposure per action

    # ---- Market data -----------------------------------------------------
    # "real"      -> download real crypto candles (money_agent/data.py), train on
    #                the earliest `data_train_frac`, and hold out the rest for
    #                honest out-of-sample evaluation (money_agent/evaluate.py).
    # "synthetic" -> the offline random-walk simulator (drift/volatility above).
    data_source: str = "real"
    data_product: str = "BTC-USD"         # which market to trade
    data_granularity: int = 3600          # seconds per candle (3600=1h, 86400=1d)
    data_candles: int = 2000              # how many candles to download & cache
    data_train_frac: float = 0.8          # earliest 80% trains; last 20% held out
    data_cache: str = ""                   # explicit CSV cache path ("" = auto-named)

    # ---- Learning --------------------------------------------------------
    hidden_size: int = 64
    learning_rate: float = 3e-4
    gamma: float = 0.99                   # reward discount
    gae_lambda: float = 0.95              # GAE smoothing for advantage estimation
    value_coef: float = 0.5               # critic loss weight
    entropy_coef: float = 0.01            # base exploration bonus
    lr_decay: float = 0.9995              # per-update learning-rate decay (gradient descent refinement)
    min_learning_rate: float = 1e-5       # floor so the learning rate never vanishes
    mercy_entropy_boost: float = 4.0      # exploration multiplier after a mercy restock
    mercy_temperature: float = 1.6        # softmax temperature after a mercy restock

    # ---- Run control -----------------------------------------------------
    max_generations: int = 200            # outer training loop length
    db_path: str = "money_ledger.sqlite"  # ledger file; ":memory:" for ephemeral runs
    vault_account: str = "__vault__"      # where realized profit is banked
    log_every: int = 1

    # ---- Continuous ("run forever") mode --------------------------------
    # With train(forever=True) or env var MONEY_AGENT_FOREVER=1, the loop never
    # stops on its own: if the whole colony dies out it starts a fresh agent and
    # keeps going, so you don't have to relaunch the trainer by hand.
    loop_delay: float = 0.0               # seconds to pause between generations
    reseed_on_extinction: bool = True     # auto-start a new agent if all die

    # ---- Checkpointing (save/resume so closing the device is safe) -------
    # .pth  = full "save game" (all brains + progress) -> resume exactly.
    # .onnx = portable per-agent snapshot for other tools (can't resume from it).
    checkpoint_path: str = "checkpoint.pth"   # where the resumable state is saved
    checkpoint_every: int = 10                # save every N generations (0 = only on exit)
    resume: bool = True                       # load checkpoint_path at startup if present
    export_onnx: bool = True                  # also export each live brain to ONNX
    onnx_dir: str = "onnx_models"             # folder for the .onnx snapshots
    status_path: str = "status.json"          # tiny live-stats file the dashboard reads

    # ---- Wallet backend --------------------------------------------------
    # "ledger"       -> offline SQLite accounting (default; fast, no network)
    # "base_sepolia" -> real on-chain settlement on the Base Sepolia TESTNET
    #                   (testnet ETH has no monetary value; safe to experiment)
    wallet_backend: str = "ledger"

    # --- Base Sepolia settings (only used when wallet_backend == "base_sepolia")
    rpc_url: str = "https://sepolia.base.org"   # public Base Sepolia RPC endpoint
    chain_id: int = 84532                        # Base Sepolia chain id
    # Name of the env var holding the TREASURY private key. NEVER hardcode a key.
    # Use a throwaway testnet key funded from a Base Sepolia faucet.
    private_key_env: str = "BASE_SEPOLIA_PRIVATE_KEY"
    # Local file mapping agent ids -> generated testnet accounts (TESTNET ONLY).
    keystore_path: str = "chain_accounts.json"
    # Conversion between "simulation dollars" and wei so faucet ETH lasts.
    # 1e12 wei = 1e-6 ETH per simulation dollar (1000 sim$ = 0.001 ETH).
    wei_per_unit: int = 1_000_000_000_000
    # Gas kept aside per account (NOT counted as tracked balance) to pay fees.
    gas_reserve_wei: int = 100_000_000_000_000   # 1e14 wei = 0.0001 ETH
    tx_timeout: int = 120                         # seconds to wait for a receipt
    # Block explorer used to print clickable links to the treasury/agent wallets.
    explorer_url: str = "https://sepolia.basescan.org"

    def action_dim(self) -> int:
        return len(self.action_fractions)

    def obs_dim(self) -> int:
        # `window` log-returns + current position fraction + normalized portfolio value
        return self.window + 2
