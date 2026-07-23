# money_agent

An autonomous, self-improving AI "money colony." A population of
reinforcement-learning agents trains on **real crypto price history**, evolves
across generations (mercy → terminate → clone), and — separately — earns **real
USDC** by selling small services over the internet using Coinbase's **x402**
pay-per-request standard. Everything is watchable on a live dashboard, guarded by
a spend cap and kill switch, and deployable to a free cloud host.

> **This README is the project's handoff document.** If you open this folder on a
> new machine with VS Code + Copilot, read the "Handoff: current state" section
> first — it tells the AI exactly where the last session left off and what to do
> next.

> **📌 MAINTENANCE RULE (for the Copilot AI, on every device):** Whenever you
> change this project — add/rename/remove a file, change how something is run,
> change env vars, move testnet→mainnet, deploy, spend or move funds, or hit a new
> gotcha — **update this README in the same turn.** Keep "Handoff: current state",
> the module inventory, the env-var table, and "Continue from here" accurate so
> the next session can resume without guessing. Treat the README as the single
> source of truth for project state.

---

## Deploy status (LIVE on Base MAINNET — 2026-07-23)

The corporate-TLS blocker is gone on an unrestricted network, and the earner is
**deployed on all three hosts and accepting REAL USDC on Base mainnet**. Current
live state:

| Target | URL | Status | Network |
| --- | --- | --- | --- |
| **Fly.io** (always-on) | https://money-agent-x402.fly.dev/ | **LIVE** (200 / 402) | **mainnet `eip155:8453`** |
| **Railway** | https://money-agent-x402-production.up.railway.app/ | **LIVE** (200 / 402) | **mainnet `eip155:8453`** |
| **Render** (free; sleeps when idle) | https://money-agent-x402-1qbj.onrender.com/ | **LIVE** (200 / 402) | **mainnet `eip155:8453`** |
| **Dashboard** | http://localhost:8000/ | **LIVE** (Docker) | local |

All three verified returning a real mainnet 402: `network eip155:8453`, asset
`0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` (**USD Coin**), `payTo`
`0xeb4B12234218a7A56932a5395d730Ac1ae1C6096`, CDP facilitator, and
`X402_BAZAAR=1` (Bazaar discovery metadata attached). Receiving is gasless — the
0-ETH wallet accepts payments fine; ETH is only needed to *cash out*.

- Code is on GitHub: **https://github.com/vegetableleaf/Project-1** (branch `main`;
  the repo is public so Render can read it). `render.yaml` / `fly.toml` default to
  mainnet; Railway vars set server-side.
- Tooling installed on this device: `flyctl` (v0.4.71), `railway` (5.28.0),
  `node`/`npm`, `git`, Docker Desktop.
- Logins: Fly + Railway as `vegetable.leaf@gmail.com`; Render + GitHub as
  `vegetableleaf`.
- **CDP keys** (`CDP_API_KEY_ID` + `CDP_API_KEY_SECRET`) are set on all three hosts
  (Fly secrets, Railway variables, Render env). One CDP Secret API key serves both
  testnet and mainnet — see the credential table below. `CDP_WALLET_SECRET` is
  **not** on the servers (receiving never moves funds).

**Verify any deploy is live:**
```powershell
curl.exe -s "https://<your-url>/"                       # HTML menu of 15 services
curl.exe -s -o NUL -w "%{http_code}\n" "https://<your-url>/service/uuid?input=2"   # expect 402
```

**Go to real money (mainnet)** — see the "Where credentials are needed" table
below. Per host set `X402_NETWORK=eip155:8453`,
`X402_FACILITATOR=https://api.cdp.coinbase.com/platform/v2/x402`, `X402_BAZAAR=1`,
and add **`CDP_API_KEY_ID`/`CDP_API_KEY_SECRET`** (a dedicated CDP Secret API key).
⚠️ The server **crash-loops on mainnet without the CDP keys** (they're required at
boot when the facilitator is CDP), so set the keys in the same step you flip the
network.

### Where credentials are needed (test vs. real money)

| Secret / value | Needed on the deployed earner? | Testnet | Mainnet | Notes |
| --- | --- | --- | --- | --- |
| `PAY_TO` (wallet address `0xeb4B…6096`) | **Yes** (all hosts) | ✅ | ✅ | Public address, not a secret. Already set on Fly + Railway. |
| `CDP_API_KEY_ID` + `CDP_API_KEY_SECRET` | **Yes, for mainnet only** | ❌ not needed | ✅ **required** | One CDP **Secret API key** works for BOTH testnet and mainnet — the *network* is chosen by `X402_NETWORK`, not the key. There is **no separate "test account" key**. Best practice: create a **new, dedicated** key for the deployment (CDP Portal → API Keys → Secret API Keys). |
| `CDP_WALLET_SECRET` | **No** | ❌ | ❌ | Receiving x402 payments never moves your own funds, so the servers do **not** need the Wallet Secret. Only local fund-*moving* tools (`cdp_swap`, cash-out) use it. |
| `X402_PRIVATE_KEY` | No (local test buyer only) | local only | — | Only for `x402_buyer.py` to simulate a paying customer. |
| `BASE_SEPOLIA_PRIVATE_KEY` | No (local testnet wallet only) | local only | — | Only for the local Base Sepolia treasury wallet. |

Where to put the CDP keys per host (type them yourself — never paste secrets into
chat):
- **Fly:** `flyctl secrets set CDP_API_KEY_ID=... CDP_API_KEY_SECRET=...`
- **Railway:** `railway variables --set "CDP_API_KEY_ID=..." --set "CDP_API_KEY_SECRET=..."`
- **Render:** dashboard → the service → **Environment** → add both keys.

---

## Handoff: current state (read this first)

**What exists and works today:**

- **Training** runs continuously on **real Coinbase BTC-USD hourly candles** with
  an actor-critic policy upgraded to **GAE(λ) advantages + Adam + LR decay** and
  a **loss** that is logged every generation (so learning is measurable, not just
  wallet balance). See [`money_agent/agent.py`](money_agent/agent.py),
  [`money_agent/train.py`](money_agent/train.py).
- **Evolution**: mercy / terminate / clone lifecycle across generations
  ([`money_agent/lifecycle.py`](money_agent/lifecycle.py)).
- **Two money stores**: an offline SQLite **ledger** (for safe training) and a
  real **Base Sepolia testnet** wallet backend.
- **Real income path (this is the part that actually earns money):** an **x402**
  payment-gated HTTP server ([`money_agent/service_x402.py`](money_agent/service_x402.py))
  sells **15 small services** (summarize, sentiment, readability, JSON & CSV
  tools, hashing, UUID/token generation, regex extraction, number stats, keyword
  extraction, text stats, plus the trained model's
  market signal, etc.) for fractions of a cent in **USDC**. Buyers pay per
  request; the server verifies payment via an x402 facilitator and settles to a
  real wallet.
- **Real wallet funded:** a **Coinbase CDP** wallet holds **~5.14 USDC on Base
  mainnet** at address **`0xeb4B12234218a7A56932a5395d730Ac1ae1C6096`** (0 ETH —
  see caveat below). Managed by [`money_agent/cdp_wallet.py`](money_agent/cdp_wallet.py).
- **Safety:** [`money_agent/safety.py`](money_agent/safety.py) enforces a **daily
  spend cap of 30%** of (day-start balance + revenue) and a **kill switch** when
  the balance is essentially empty. Verified: on 5.14 USDC the cap computes to
  ~$1.54/day and correctly denies a $2.54 spend.
- **Dashboard** ([`dashboard/app.py`](dashboard/app.py), Docker, port 8000) shows
  generations, wallets, transactions, an earnings strip (CDP USDC/ETH, x402
  sales, safety cap + kill-switch banner), and a recent-agents chart.
- **Public exposure proven:** a Cloudflare tunnel (`cloudflared`) exposed the
  local x402 server to the internet and a request from outside returned **HTTP
  402 Payment Required** to the funded wallet — i.e. a real stranger could pay it.
- **Cloud deploy — LIVE on Base MAINNET (all 3 hosts), accepting real USDC:**
  the earner is deployed and returning **HTTP 402** to outside callers on three
  hosts:
  **https://money-agent-x402.fly.dev/** (Fly.io, always-on),
  **https://money-agent-x402-production.up.railway.app/** (Railway), and
  **https://money-agent-x402-1qbj.onrender.com/** (Render, free/sleeps-when-idle).
  All run mainnet (`X402_NETWORK=eip155:8453`, CDP facilitator, `X402_BAZAAR=1`,
  `PAY_TO` set) with `CDP_API_KEY_ID`/`CDP_API_KEY_SECRET` set per host. Verified
  402s carry the mainnet USDC asset and settle to the funded wallet. The earlier
  502 was purely the corporate TLS proxy corrupting the build upload — gone on
  this unrestricted network. The lean image boots, serves all 15 services, and
  honors `$PORT`. Configs for all three hosts are in the repo
  ([`fly.toml`](fly.toml), [`railway.json`](railway.json),
  [`render.yaml`](render.yaml)), all using [`deploy/Dockerfile`](deploy/Dockerfile),
  which starts via `python -c "... waitress.serve(build_app(), port=int(os.environ['PORT']))"`
  so `$PORT` is parsed robustly. **Note:** the CDP facilitator requires the CDP
  keys at boot — a mainnet deploy crash-loops (502) until `CDP_API_KEY_ID` and
  `CDP_API_KEY_SECRET` are present (this is what caused Railway's transient 502
  until the keys were added).

**Known caveats / honest truths:**

- **The trading model does NOT reliably beat buy-and-hold.** On held-out data it
  learned to mostly sit in cash. A small model beating a real market is close to
  impossible. The GAE upgrade makes *learning* sound and measurable, but **do not
  expect trading profit.** The **reliable real income is selling services via
  x402**, which is proven working.
- **Corporate TLS wall (IPG Photonics network):** the work network runs HTTPS
  inspection with a corporate root certificate. Python fixes this on Windows with
  `truststore.inject_into_ssl()` (uses the Windows cert store). **A Linux Docker
  container cannot use the Windows store**, so containers behind this network
  fail TLS to external hosts (that's the local deploy 500 we saw). On a normal
  cloud host there is no such proxy and it works. `curl.exe` needs
  `--ssl-no-revoke` on this network.
- **0 ETH for gas:** the CDP wallet holds USDC but no ETH. Receiving payments and
  reading balances needs no gas, but **sending funds out (cash-out) needs ~$1 of
  ETH on Base** for gas.
- Everything defaults to **testnet-first**. Mainnet only where explicitly set.

**What a future session should pick up next** — see "Continue from here" at the
bottom.

---

## Quick start (fastest path)

> **Full command cheat sheet:** every runnable command with its purpose and env
> vars is listed in [COMMANDS.txt](COMMANDS.txt) — keep it in sync with this file.

```powershell
# From the project root: C:\Users\bzhu\Documents\Project1
# (run everything as a module: python -m money_agent.<name>)

pip install -r requirements.txt          # core training deps (torch, numpy, ...)

# 1) Train on real data (offline ledger, safe, no network money):
python -m money_agent.train

# 2) See if the trained model actually generalises (out-of-sample):
python -m money_agent.evaluate

# 3) Watch it live (Docker dashboard on http://localhost:8000):
docker compose up -d --build
```

To earn real USDC, jump to **"Earning real money (x402)"** below.

---

## Architecture (the whole picture)

```
                        ┌─────────────────────────────────────────┐
   REAL PRICE DATA  ──▶ │  Training loop (train.py)                │
   (Coinbase candles)   │   Population ─▶ Agent (PolicyNetwork)    │
                        │        │            │                    │
                        │   Ledger        TradingEnv (real windows)│
                        │        │            │                    │
                        │   LifecycleManager: mercy/terminate/clone│
                        │   Learner: GAE(λ) + Adam + LR decay       │
                        └───────────────┬─────────────────────────┘
                                        │ writes status.json, ledger.sqlite,
                                        │ checkpoints (.pth / .onnx)
                                        ▼
   ┌──────────────────────┐     ┌───────────────────────────────┐
   │ Dashboard (Flask,    │◀────│ Host reporters (need real TLS):│
   │ Docker, :8000)       │     │  chain_report.py  (testnet)    │
   │ reads *.json+sqlite  │     │  earnings_report.py (CDP+x402) │
   └──────────────────────┘     └───────────────────────────────┘

   ── Separate, real-income track (this is what earns money) ──
   ┌───────────────────────────────────────────────────────────┐
   │ service.py: Marketplace of small sellable services         │
   │        │  (text_stats, shout, keywords, sma_signal,        │
   │        │   market_signal = the trained model as a product) │
   │        ▼                                                    │
   │ service_x402.py: Flask + x402 payment middleware (:8402)   │
   │   GET /service/<name> → 402 Payment Required → pay USDC →   │
   │   facilitator verifies → settle to CDP wallet → 200 result │
   │        ▲                          ▲                         │
   │ x402_buyer.py (test buyer)   cloudflared tunnel / cloud     │
   │        │                     deploy (Dockerfile, render)    │
   │ safety.py: 30% daily spend cap + kill switch                │
   │ cdp_wallet.py: real Coinbase CDP USDC wallet                │
   └───────────────────────────────────────────────────────────┘
```

Two independent tracks share one wallet philosophy:
1. **Training/evolution** — the AI learning experiment (offline or testnet).
2. **x402 service selling** — the real, reliable USDC income.

---

## Module inventory

Everything lives in the `money_agent/` package unless noted. Run modules with
`python -m money_agent.<name>` from the project root.

### Core training & evolution
| File | What it does |
| --- | --- |
| [`money_agent/config.py`](money_agent/config.py) | Single source of truth for all rules & hyperparameters (thresholds, learning rates, `gae_lambda`, `lr_decay`, `min_learning_rate`, data settings, wallet/chain settings, checkpoint/resume, `status_path`, explorer URL). |
| [`money_agent/model.py`](money_agent/model.py) | `PolicyNetwork` — actor-critic MLP (obs → 64 → 64 → actor head + critic head). |
| [`money_agent/agent.py`](money_agent/agent.py) | The RL `Agent` dataclass. `act()` samples actions; **`learn()` uses GAE(λ) advantages, normalizes them, trains actor+critic with Adam, decays LR per update, returns the loss.** Mutation/clone helpers. |
| [`money_agent/environment.py`](money_agent/environment.py) | `TradingEnv` — the market simulator. Samples **real price windows** from a pool (or GBM fallback). `step()` maps actions → PnL. |
| [`money_agent/population.py`](money_agent/population.py) | `Population` — holds living agents, `reseed()` on extinction, seeding, address book. |
| [`money_agent/lifecycle.py`](money_agent/lifecycle.py) | `LifecycleManager` — decides continue / mercy / terminate / clone each generation. |
| [`money_agent/train.py`](money_agent/train.py) | The main loop. Loads real data, runs episodes, evolves, logs per-gen line **incl. `loss=`**, writes `status.json`, checkpoints, supports `forever` mode, resume, and env overrides. |
| [`money_agent/evaluate.py`](money_agent/evaluate.py) | Out-of-sample evaluation on held-out test data. Reports mean return, win rate, % beating buy-and-hold, Sharpe, max drawdown. (Current verdict: does not beat buy-and-hold.) |
| [`money_agent/data.py`](money_agent/data.py) | Downloads Coinbase candles (truststore + urllib), caches to `prices_BTC-USD_3600.csv`. `load_prices()`, `fetch_candles()`, `train_test_split()`. |
| [`money_agent/checkpoint.py`](money_agent/checkpoint.py) | Save/load `.pth` weights and export single-file `.onnx`. |

### Money stores (wallets)
| File | What it does |
| --- | --- |
| [`money_agent/ledger.py`](money_agent/ledger.py) | Offline double-entry **SQLite ledger** (`open`/`balance`/`credit`/`debit`/`transfer`). Uses `journal_mode=TRUNCATE` so the Docker dashboard can read live data from a bind mount. |
| [`money_agent/wallet.py`](money_agent/wallet.py) | `WalletBackend` protocol — the pluggable money interface. |
| [`money_agent/chain.py`](money_agent/chain.py) | `BaseSepoliaWallet` — real **testnet** on-chain settlement (truststore-injected web3). Treasury bankrolls agents. |
| [`money_agent/cdp_wallet.py`](money_agent/cdp_wallet.py) | Creates/reads a real **Coinbase CDP** EVM account, writes `cdp_wallet.json`, `--fund` faucet. Graceful if keys missing. |
| [`money_agent/cdp_swap.py`](money_agent/cdp_swap.py) | Convert a little **USDC → native ETH** (gas) on Base via CDP. Dry-run quote by default; `--execute` to swap for real. Needs a little ETH already present to pay gas, so it's a top-up tool (see gas notes). |

### Real income (x402 service selling)
| File | What it does |
| --- | --- |
| [`money_agent/service.py`](money_agent/service.py) | `Marketplace` + `Service` dataclasses. **15 sellable services** (all pure-stdlib except `market_signal`): `text_stats`, `shout`, `keywords`, `sma_signal`, `summarize`, `sentiment`, `readability`, `json_tools`, `csv_to_json`, `hash`, `uuid`, `token`, `extract`, `num_stats`, and **`market_signal`** (sells the trained model's prediction). Earnings tracking. `build_extended_services(cfg)`. |
| [`money_agent/service_x402.py`](money_agent/service_x402.py) | Flask app with **x402 payment middleware** on `GET /service/<name>` (port 8402). Price = service price / 1000 USD. Records sales to `x402_sales.json`. `X402_BAZAAR=1` attaches discovery metadata. `build_app()` is the entry point for waitress/gunicorn. |
| [`money_agent/x402_buyer.py`](money_agent/x402_buyer.py) | A simulated paying customer (eth_account + x402 client). Signs and submits payment to test the full pay→verify→settle loop. |
| [`money_agent/safety.py`](money_agent/safety.py) | `SpendGuard` — **30% daily spend cap** + **kill switch**. State in `spend_state.json`, rolls per day. CLI: `python -m money_agent.safety`. |

### Host-side reporters (write JSON the Docker dashboard reads)
> These run on the **Windows host** because they need real TLS (truststore); the
> Linux container can't reach the corporate-proxied internet.

| File | What it does |
| --- | --- |
| [`money_agent/chain_report.py`](money_agent/chain_report.py) | Reads Base Sepolia keystore balances → `chain_status.json`. `--watch 30` to poll. |
| [`money_agent/earnings_report.py`](money_agent/earnings_report.py) | Reads CDP wallet ETH+USDC balance, merges x402 sales, computes SpendGuard status → `earnings_status.json`. `--watch 30`. Set `CDP_NETWORK=base` for mainnet. |

### Dashboard & deploy
| File | What it does |
| --- | --- |
| [`dashboard/app.py`](dashboard/app.py) | Flask dashboard (port 8000). Reads the ledger + all `*_status.json` files. Shows earnings strip, generation cards, wallets, transactions, recent-agents chart, ledger/on-chain toggle. |
| [`docker-compose.yml`](docker-compose.yml) | Runs the dashboard container with the project bind-mounted. |
| [`deploy/Dockerfile`](deploy/Dockerfile) | Lean image (python:3.12-slim, **no torch**) that serves `service_x402:build_app` via waitress. For cloud hosting the earner. |
| [`deploy/requirements.txt`](deploy/requirements.txt) | Cloud image deps: `x402[flask,evm]`, httpx, truststore, waitress. |
| [`render.yaml`](render.yaml) | Render.com Blueprint to deploy the x402 earner from `deploy/Dockerfile`. |
| [`run_training.ps1`](run_training.ps1) | Windows Scheduled-Task runner: starts trainer (forever, real data) + `chain_report --watch` + `earnings_report --watch`, with a kill-guard and clean UTF-8 logging. |

---

## Setup & dependencies

There are several requirements files, each for a different job:

| File | Install when you want to… |
| --- | --- |
| `requirements.txt` | Train / evaluate (torch, numpy, and friends). |
| `requirements-chain.txt` | Use the Base Sepolia testnet wallet (web3, eth-account, truststore). |
| `requirements-x402.txt` | Run the x402 earner + buyer locally (`x402[flask,evm]`, httpx, eth-account, waitress). |
| `deploy/requirements.txt` | Build the lean cloud image (no torch). |

```powershell
pip install -r requirements.txt
pip install -r requirements-chain.txt      # optional: testnet wallet
pip install -r requirements-x402.txt        # optional: real income server
```

Python: `C:\Users\bzhu\AppData\Local\Python\pythoncore-3.14-64\python.exe`.
Docker Desktop is required for the dashboard and cloud image.

### The corporate-TLS gotcha (important on the IPG network)

The work network inspects HTTPS with a corporate root CA. Every Python file that
makes an HTTPS call injects the Windows trust store first:

```python
import truststore
truststore.inject_into_ssl()   # must run BEFORE any HTTPS call
```

This is already done in the code. Consequences:
- **On the Windows host:** HTTPS works (web3 RPC, data download, x402 facilitator, CDP).
- **In a Linux container behind this network:** TLS to external hosts fails
  (`self-signed certificate in certificate chain`). That's why the reporters run
  on the host and write JSON for the container, and why the cloud deploy is meant
  to run on a real host (no corporate proxy) — where it works.
- Use `curl.exe --ssl-no-revoke` for manual tests on this network.

---

## Everything you can run

### 1) Train on real data (safe, offline)

```powershell
python -m money_agent.train
```

- Trains the population on real BTC-USD candles.
- Prints a per-generation line including `loss=` (the GAE actor-critic loss).
- Writes `status.json` (dashboard reads it), `money_ledger.sqlite`, and
  checkpoints (`.pth` and `.onnx`).

Useful env overrides:
```powershell
$env:MONEY_AGENT_FOREVER = "1"       # never stop (used by the scheduled task)
$env:MONEY_AGENT_GENERATIONS = "50"  # or a fixed count
$env:MONEY_AGENT_LOOP_DELAY = "2"    # seconds between generations
$env:MONEY_AGENT_BACKEND = "ledger"  # or "base_sepolia"
```

### 2) Evaluate (does it actually generalise?)

```powershell
python -m money_agent.evaluate
```
Runs the best checkpoint on held-out test data and reports mean return, win rate,
% of windows beating buy-and-hold, Sharpe, and max drawdown. **Reality check:** it
currently does not beat buy-and-hold.

### 3) Watch it live — dashboard

```powershell
docker compose up -d --build          # http://localhost:8000
```
The dashboard reads `money_ledger.sqlite`, `status.json`, `chain_status.json`,
and `earnings_status.json`. To populate the on-chain / earnings panels, run the
host reporters (below) alongside it.

### 4) Testnet wallet (real on-chain, no real value)

```powershell
$env:BASE_SEPOLIA_PRIVATE_KEY = "0xYOUR_TESTNET_PRIVATE_KEY"   # THROWAWAY key
python -m money_agent.chain               # prints treasury address to fund
# fund it from a Base Sepolia faucet, then:
$env:MONEY_AGENT_BACKEND = "base_sepolia"
$env:MONEY_AGENT_GENERATIONS = "5"        # keep small; each event is a real tx
python -m money_agent.train
python -m money_agent.chain_report --watch 30   # updates chain_status.json
```

### 5) Real wallet (Coinbase CDP)

```powershell
# From the CDP portal (https://portal.cdp.coinbase.com):
$env:CDP_API_KEY_ID     = "..."     # /api-keys/secret
$env:CDP_API_KEY_SECRET = "..."     # /api-keys/secret
$env:CDP_WALLET_SECRET  = "..."     # /wallets/non-custodial/security
python -m money_agent.cdp_wallet            # create/read account → cdp_wallet.json
python -m money_agent.cdp_wallet --fund     # testnet faucet (testnet only)

# Convert a little USDC into native ETH for gas (dry-run quote first):
python -m money_agent.cdp_swap              # quote $1 USDC → ETH, moves nothing
python -m money_agent.cdp_swap --usd 1 --execute   # actually swap (needs some ETH for gas)

# Report real balances + x402 sales for the dashboard:
$env:CDP_NETWORK = "base"                   # mainnet; omit/"" for base-sepolia
python -m money_agent.earnings_report --watch 30
```
Current real wallet: **`0xeb4B12234218a7A56932a5395d730Ac1ae1C6096`**, holding
~5.14 USDC on Base mainnet (0 ETH).

---

## Earning real money (x402)

This is the part that actually makes money. Buyers pay tiny amounts of **USDC**
per HTTP request; the server verifies payment through an x402 facilitator and
settles to your wallet.

### Run the earner locally

```powershell
pip install -r requirements-x402.txt
$env:PAY_TO = "0xeb4B12234218a7A56932a5395d730Ac1ae1C6096"   # your wallet
$env:X402_NETWORK = "base-sepolia"        # testnet first! ("base" = mainnet)
$env:X402_FACILITATOR = "https://x402.org/facilitator"        # free testnet facilitator
python -m money_agent.service_x402         # serves on http://localhost:8402
```

Test it (unfunded request returns 402):
```powershell
curl.exe --ssl-no-revoke "http://localhost:8402/service/shout?input=hello"
```

Simulate a paying customer end-to-end:
```powershell
$env:X402_PRIVATE_KEY = "0xTESTNET_KEY_WITH_TESTNET_USDC"
python -m money_agent.x402_buyer
```

### Expose it to the internet (proven working)

```powershell
# In a second terminal, tunnel the local server publicly:
cloudflared tunnel --url http://localhost:8402
# → prints a public https URL; real users can hit /service/<name> and pay.
```

### Go to mainnet + Coinbase (CDP) facilitator

Switch these to accept real USDC on Base mainnet:
```powershell
$env:X402_NETWORK = "eip155:8453"
$env:X402_FACILITATOR = "https://api.cdp.coinbase.com/platform/v2/x402"
$env:CDP_API_KEY_ID = "..."       # a DEDICATED CDP Secret API key (see note)
$env:CDP_API_KEY_SECRET = "..."
```

**Which CDP API key?** The CDP API keys are tied to your CDP **account/project**,
not to a network — the same key works for testnet and mainnet (the network is
chosen by `X402_NETWORK`, not the key). You do **not** need a separate "test"
account. Best practice: **create a NEW, dedicated Secret API key** just for the
deployed server (CDP Portal → API Keys → Secret API Keys → Create), so you can
rotate/revoke it independently of your local tooling. You do **not** need the
**Wallet Secret** (`CDP_WALLET_SECRET`) on the server — receiving x402 payments
never moves your own funds. `service_x402.py` auto-wires CDP auth via
`cdp.x402.create_facilitator_config` when `X402_FACILITATOR` points at
`api.cdp.coinbase.com`, and `cdp-sdk` is bundled in the deploy image, so this is
a pure config change — no rebuild.

### List it on the x402 Bazaar (discovery)

```powershell
$env:X402_BAZAAR = "1"      # attaches discovery metadata so buyers can find it
python -m money_agent.service_x402
```

### Safety: spend cap + kill switch (always on)

```powershell
python -m money_agent.safety          # prints the current cap and status
```
`SpendGuard` allows at most **30% of (day-start balance + revenue) per day** and
**halts all spending** if the balance is essentially empty. State is kept in
`spend_state.json` and resets daily. The dashboard shows the cap and a
kill-switch banner.

---

## Cloud deployment (host the earner permanently)

The earner is a plain Docker web service, so any container host works. Config
files for **all three** providers are in the repo, all pointing at the same
[`deploy/Dockerfile`](deploy/Dockerfile) — you can deploy to one or all of them
(each gets its own public URL but all pay into the same `PAY_TO` wallet):

| Provider | Config file | Needs GitHub? | Always-on? | Cost |
| --- | --- | --- | --- | --- |
| **Fly.io** | [`fly.toml`](fly.toml) | No (CLI uploads) | Yes (`min_machines_running=1`) | Card required, small |
| **Railway** | [`railway.json`](railway.json) | No (CLI uploads) | Yes | Small free credit, then paid |
| **Render** | [`render.yaml`](render.yaml) | Yes | Sleeps when idle, wakes on request | Free, no card |

The `flyctl` and `railway` CLIs are already installed on this machine. Deploys
should be run from a **non-corporate network** (the work TLS proxy can break CLI
auth/uploads).

```powershell
# Build & smoke-test locally (NOTE: on the corporate network the facilitator
# call fails TLS — that's expected here and does NOT happen on a real host):
docker build -f deploy/Dockerfile -t money-agent-x402 .
docker run -d --name magent-x402-test -p 8403:8402 `
  -e "PAY_TO=0xeb4B12234218a7A56932a5395d730Ac1ae1C6096" -e "PORT=8402" money-agent-x402
```

### Fly.io — always-on 24/7 (uses fly.toml)
```powershell
fly auth login                              # browser sign-in (needs a card)
fly launch --copy-config --no-deploy        # creates the app from fly.toml
fly secrets set PAY_TO=0xeb4B12234218a7A56932a5395d730Ac1ae1C6096
# (mainnet + CDP facilitator also: fly secrets set CDP_API_KEY_ID=... CDP_API_KEY_SECRET=...)
fly deploy                                  # → https://<app>.fly.dev
```

### Railway (uses railway.json)
```powershell
railway login                               # browser sign-in
railway init                                # create a project
railway up                                  # build & deploy from railway.json
railway variables --set PAY_TO=0xeb4B12234218a7A56932a5395d730Ac1ae1C6096
# set X402_NETWORK / X402_FACILITATOR / X402_BAZAAR / CDP keys the same way
```

### Render — free, no card (uses render.yaml)
1. Push this repo to GitHub.
2. In Render: **New → Blueprint**, point it at the repo (reads `render.yaml`).
3. Set env vars in the dashboard: `PAY_TO`, `X402_NETWORK`, `X402_FACILITATOR`,
   `X402_BAZAAR=1`, and CDP keys if using the CDP facilitator.
4. Render gives you a public HTTPS URL. Buyers hit `/service/<name>` and pay to
   `PAY_TO`. No corporate proxy → the facilitator call succeeds (402 → 200).

The container reads on-chain state from JSON files the host reporters produce; on
a cloud host the earner simply accepts payments — balance reporting for the
dashboard stays on your machine.

---

## Automation (Windows Scheduled Task)

[`run_training.ps1`](run_training.ps1) runs the whole learning stack unattended:
the forever-trainer plus both host reporters (`chain_report --watch 30`,
`earnings_report --watch 30` with `CDP_NETWORK=base`). It kill-guards previous
instances and writes a clean UTF-8 log. Point a Windows Scheduled Task at it to
keep the colony training and the dashboard fed.

---

## Environment variable reference

| Variable | Used by | Meaning |
| --- | --- | --- |
| `MONEY_AGENT_BACKEND` | train | `ledger` (default) or `base_sepolia`. |
| `MONEY_AGENT_GENERATIONS` | train | Fixed generation count. |
| `MONEY_AGENT_FOREVER` | train | `1` = run forever. |
| `MONEY_AGENT_LOOP_DELAY` | train | Seconds between generations. |
| `MONEY_AGENT_DATA` | train | Data source override. |
| `MONEY_AGENT_RPC_URL` / `RPC_URL` | chain | Base RPC endpoint. |
| `BASE_SEPOLIA_PRIVATE_KEY` | chain | **Throwaway** testnet treasury key. |
| `CDP_API_KEY_ID` | cdp_wallet, earnings, CDP facilitator | CDP API key id. |
| `CDP_API_KEY_SECRET` | cdp_wallet, earnings, CDP facilitator | CDP API key secret. |
| `CDP_WALLET_SECRET` | cdp_wallet | Authorizes moving funds (from CDP security page). |
| `CDP_WALLET_ADDRESS` | earnings_report | Override the reported address. |
| `CDP_NETWORK` | cdp_wallet, earnings | `base` (mainnet) or `base-sepolia`. |
| `PAY_TO` | service_x402 | Wallet that receives x402 payments. |
| `X402_NETWORK` | service_x402, buyer | `base-sepolia` (test) or `base` (real). |
| `X402_FACILITATOR` | service_x402 | Facilitator URL (x402.org test / CDP mainnet). |
| `X402_BAZAAR` | service_x402 | `1` = attach Bazaar discovery metadata. |
| `SERVICE_X402_PORT` / `PORT` | service_x402 | Server port (default 8402). |
| `X402_PRIVATE_KEY` | x402_buyer | Test buyer's key (needs test USDC). |

**Networks:** Base Sepolia = `eip155:84532`, Base mainnet = `eip155:8453`.
**USDC:** Sepolia `0x036CbD53842c5426634e7929541eC2318f3dCF7e`,
mainnet `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`.

Secrets and generated state (`*.pth`, `*.onnx`, `*.json`, logs, `prices_*.csv`,
`cdp_wallet.json`, `spend_state.json`, keystores) are git-ignored.

---

## Responsible use & honest expectations

- **The trading AI is a learning experiment, not a money printer.** It loses to
  buy-and-hold and learned to hold cash. Don't wire real trading capital to it.
- **The real, reliable income is selling services via x402** — proven working,
  with a real funded wallet and a public endpoint that returned 402 to strangers.
- **Testnet first, always.** Only move to mainnet deliberately, with the spend
  cap and kill switch on, and never risk money you can't lose.
- Autonomous financial software may be regulated where you live. Comply with the
  laws and platform terms that apply to you.

---

## Continue from here (for the next session)

Suggested next steps, roughly in priority order:

1. **Cloud deploy — DONE for Fly + Railway (testnet), Render pending one web-UI
   step.** Fly (https://money-agent-x402.fly.dev/) and Railway
   (https://money-agent-x402-production.up.railway.app/) are live and returning
   402. Code is on GitHub (`vegetableleaf/Project-1`); finish Render with
   **New → Blueprint** (reads [`render.yaml`](render.yaml)). **Next: flip to
   mainnet** — set `X402_NETWORK=eip155:8453`, the CDP facilitator, `X402_BAZAAR=1`,
   and the `CDP_API_KEY_ID`/`CDP_API_KEY_SECRET` secrets on each host (see "Where
   credentials are needed" near the top). The server needs those keys at boot on
   mainnet.
2. **Gas / ETH (only needed to move funds OUT — earning is gasless).** Receiving
   x402 payments needs 0 ETH. You only need a little ETH to send/cash-out USDC.
   Simplest: buy ~\$1–2 of ETH on Coinbase and send it to
   `0xeb4B12234218a7A56932a5395d730Ac1ae1C6096` on the **Base** network (keeps
   your USDC intact). After the wallet has a little ETH, top it up from earnings
   with `python -m money_agent.cdp_swap --usd 1 --execute`. (A truly from-zero
   gasless swap would need a CDP Smart Account + paymaster — more advanced.)
3. **Add more valuable services** to [`money_agent/service.py`](money_agent/service.py)
   — the more genuinely useful the endpoint, the more buyers pay. This is where
   real revenue grows, not in the trading model.
4. **Keep improving the learner** if desired (reward shaping, LSTM/Transformer
   body, entropy bonus tuning) — but treat trading profit as a research goal, not
   an income source. The `loss=` log and `evaluate.py` are your measuring sticks.
5. **Harden safety** before any mainnet spending: confirm `SpendGuard` limits,
   test the kill switch, and keep secrets out of git.

If you're a fresh Copilot session: read the "Handoff: current state" section at
the top, then use the module inventory to navigate. The uncompacted design
history for this project is large — prefer reading the code and this README over
guessing.
