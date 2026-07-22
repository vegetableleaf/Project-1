"""service.py: let an agent EARN real (test) crypto by SELLING a service.

The honest big idea
-------------------
Trading a random-number "market" (see `environment.py`) does not create money --
it just shuffles your own funds around and slowly bleeds fees. The real,
legitimate way for the agent to grow its wallet (and therefore stay alive) is to
*sell something a customer actually wants* and get paid for it.

Analogy: instead of gambling, the agent runs a little vending machine. A
customer puts crypto in, the machine hands back a useful result, and the agent's
balance goes up. Crucially, that income comes from OUTSIDE (a paying customer) --
that is what makes it real, unlike the treasury paying itself.

Why this file barely mentions "blockchain"
------------------------------------------
Every payment goes through the same `WalletBackend` interface used everywhere
else in the project (`balance` / `transfer`). So this exact marketplace runs on
BOTH money backends with zero code changes:
  * "ledger"       -> instant, free, offline (great for practice), and
  * "base_sepolia" -> real on-chain test transactions.
You flip ONE config setting, not a single line below.

Upgrading to real "test USDC" later
-----------------------------------
Right now "money" is the wallet's native unit (test ETH on Base Sepolia). To pay
in a token like USDC instead, you would write a NEW WalletBackend whose
`transfer()` moves an ERC-20 token. Because the Marketplace only ever calls the
WalletBackend interface, none of the code below would change. That pluggability
is the whole point of `wallet.py`.

Run it
------
Offline (instant, free, safe -- start here):
    python -m money_agent.service

On-chain on Base Sepolia (real testnet transactions; do the wallet setup first
and set BASE_SEPOLIA_PRIVATE_KEY):
    $env:MONEY_AGENT_BACKEND = "base_sepolia"
    python -m money_agent.service
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from .config import Config
from .wallet import WalletBackend, make_wallet


# ---------------------------------------------------------------------------
# 1) A SERVICE = one thing the agent can do that a customer will pay for.
# ---------------------------------------------------------------------------
@dataclass
class Service:
    """One item on the menu.

    * name    : how a customer asks for it
    * price   : how much it costs (in wallet units)
    * fulfill : the actual work -- takes the customer's request, returns a result
    """
    name: str
    price: float
    fulfill: Callable[[str], str]


# A couple of tiny, dependency-free example services. They are real and
# deterministic so the demo is easy to follow. Swap these for anything valuable:
# summaries, generated code, images, cleaned data feeds -- or a prediction from
# your own PolicyNetwork (see the note in build_default_services()).
def _text_stats(request: str) -> str:
    words = request.split()
    n_words = len(words)
    n_chars = len(request)
    n_sentences = max(1, sum(request.count(c) for c in ".!?"))
    avg_len = (sum(len(w) for w in words) / n_words) if n_words else 0.0
    return (f"words={n_words} chars={n_chars} "
            f"sentences={n_sentences} avg_word_len={avg_len:.1f}")


def _shout(request: str) -> str:
    return request.upper() + "!!!"


def build_default_services() -> Dict[str, "Service"]:
    """The starter menu. Keep prices small so a faucet drip lasts a long time.

    To sell your deep-learning model's output instead, add a service whose
    `fulfill` feeds the request into an Agent and returns `agent.act(obs)` --
    e.g. a paid "market signal" endpoint. The marketplace code never changes.
    """
    return {
        "text_stats": Service("text_stats", price=25.0, fulfill=_text_stats),
        "shout":      Service("shout",      price=10.0, fulfill=_shout),
    }


# --- a few more real, dependency-free services -----------------------------
def _keywords(request: str) -> str:
    import re
    from collections import Counter
    words = re.findall(r"[a-zA-Z']+", request.lower())
    stop = {"the", "a", "an", "and", "or", "to", "of", "in", "is", "it",
            "this", "that", "for", "on", "with"}
    common = Counter(w for w in words if len(w) > 2 and w not in stop).most_common(5)
    return ", ".join(f"{w}({n})" for w, n in common) or "(no keywords found)"


def _sma_signal(request: str) -> str:
    """A simple moving-average crossover on a comma-separated price list."""
    try:
        prices = [float(x) for x in request.split(",") if x.strip()]
    except ValueError:
        return "send prices as a comma-separated list, e.g. 100,101,99,102,..."
    if len(prices) < 10:
        return "need at least 10 prices"
    short = sum(prices[-3:]) / 3
    long = sum(prices[-10:]) / 10
    trend = "BULLISH (short SMA > long SMA)" if short > long else "BEARISH (short SMA <= long SMA)"
    return f"{trend}  short={short:.2f} long={long:.2f}"


# ===========================================================================
# 10 practical, dependency-free services (useful to developers and AI agents).
# Every function is pure-stdlib so it runs in the lean cloud image (no numpy /
# torch needed) and returns a plain string. The request payload arrives as the
# `?input=...` query parameter of the x402 route.
# ===========================================================================
_STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "is", "it", "this", "that",
    "for", "on", "with", "as", "are", "was", "be", "by", "at", "from", "but",
    "not", "have", "has", "had", "you", "your", "we", "they", "he", "she", "i",
}


def _summarize(request: str) -> str:
    """Extractive summary: return the highest-information sentences of a text."""
    import re
    from collections import Counter
    text = (request or "").strip()
    if not text:
        return "send some text to summarize (as ?input=...)"
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if len(sentences) <= 3:
        return text
    words = [w for w in re.findall(r"[a-zA-Z']+", text.lower())
             if len(w) > 2 and w not in _STOPWORDS]
    freq = Counter(words)

    def score(sentence: str) -> float:
        ws = re.findall(r"[a-zA-Z']+", sentence.lower())
        return sum(freq.get(w, 0) for w in ws) / (len(ws) or 1)

    ranked = sorted(range(len(sentences)), key=lambda i: score(sentences[i]),
                    reverse=True)
    keep = sorted(ranked[:3])
    return " ".join(sentences[i] for i in keep)


_POSITIVE = {
    "good", "great", "excellent", "happy", "love", "like", "best", "amazing",
    "wonderful", "positive", "success", "successful", "win", "gain", "profit",
    "nice", "awesome", "fantastic", "recommend", "fast", "reliable", "helpful",
}
_NEGATIVE = {
    "bad", "terrible", "awful", "hate", "worst", "poor", "negative", "fail",
    "failed", "loss", "slow", "broken", "angry", "sad", "problem", "bug",
    "crash", "disappointed", "expensive", "scam", "useless", "buggy",
}


def _sentiment(request: str) -> str:
    """Lexicon sentiment: label + normalized score for a piece of text."""
    import re
    words = re.findall(r"[a-zA-Z']+", (request or "").lower())
    if not words:
        return "send some text to analyze (as ?input=...)"
    pos = sum(w in _POSITIVE for w in words)
    neg = sum(w in _NEGATIVE for w in words)
    score = (pos - neg) / len(words)
    label = "positive" if pos > neg else "negative" if neg > pos else "neutral"
    return f"{label}  score={score:+.3f}  (pos={pos} neg={neg} words={len(words)})"


def _count_syllables(word: str) -> int:
    word = word.lower()
    vowels = "aeiouy"
    count, prev_vowel = 0, False
    for ch in word:
        is_vowel = ch in vowels
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def _readability(request: str) -> str:
    """Flesch Reading Ease + a plain-English grade band for a text."""
    import re
    text = (request or "").strip()
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    words = re.findall(r"[a-zA-Z']+", text)
    if not words or not sentences:
        return "send at least one full sentence (as ?input=...)"
    syllables = sum(_count_syllables(w) for w in words)
    n_words, n_sent = len(words), len(sentences)
    flesch = 206.835 - 1.015 * (n_words / n_sent) - 84.6 * (syllables / n_words)
    if flesch >= 80:
        band = "very easy"
    elif flesch >= 60:
        band = "plain English"
    elif flesch >= 30:
        band = "fairly difficult"
    else:
        band = "very difficult"
    return (f"flesch={flesch:.1f} ({band})  words={n_words} sentences={n_sent} "
            f"syllables/word={syllables / n_words:.2f}")


def _json_tools(request: str) -> str:
    """Validate + pretty-print JSON. Prefix with 'min:' to minify instead."""
    import json
    req = (request or "").strip()
    if not req:
        return "send JSON text (as ?input=...); prefix with 'min:' to minify"
    minify = req.lower().startswith("min:")
    if minify:
        req = req[4:].strip()
    try:
        obj = json.loads(req)
    except (ValueError, TypeError) as exc:
        return f"invalid JSON: {exc}"
    if minify:
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    return "valid JSON:\n" + json.dumps(obj, indent=2, sort_keys=True,
                                        ensure_ascii=False)


def _csv_to_json(request: str) -> str:
    """Convert CSV text (first row = headers) into a JSON array of objects."""
    import csv
    import io
    import json
    req = (request or "").strip()
    if not req:
        return "send CSV text (first row = headers) as ?input=..."
    try:
        rows = list(csv.DictReader(io.StringIO(req)))
    except csv.Error as exc:
        return f"could not parse CSV: {exc}"
    if not rows:
        return "no data rows found (need a header row plus at least one row)"
    return json.dumps(rows[:100], indent=2, ensure_ascii=False)


def _hash(request: str) -> str:
    """Hash text. Prefix with 'md5:'/'sha1:'/'sha256:'/'sha512:' (default sha256)."""
    import hashlib
    req = request or ""
    algo = "sha256"
    if ":" in req:
        maybe, rest = req.split(":", 1)
        if maybe.strip().lower() in ("md5", "sha1", "sha256", "sha512"):
            algo, req = maybe.strip().lower(), rest
    digest = hashlib.new(algo, req.encode("utf-8")).hexdigest()
    return f"{algo}={digest}"


def _uuid(request: str) -> str:
    """Generate N random UUIDv4s (default 1, max 20). Send a number as input."""
    import uuid
    n = 1
    req = (request or "").strip()
    if req:
        try:
            n = max(1, min(20, int(req)))
        except ValueError:
            n = 1
    return "\n".join(str(uuid.uuid4()) for _ in range(n))


def _token(request: str) -> str:
    """Generate a cryptographically strong random token (default 32, 8-128 chars)."""
    import secrets
    import string
    length = 32
    req = (request or "").strip()
    if req:
        try:
            length = max(8, min(128, int(req)))
        except ValueError:
            length = 32
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _extract(request: str) -> str:
    """Extract emails / urls / phones / numbers. Prefix 'emails:'/'urls:'/... to filter."""
    import json
    import re
    req = request or ""
    kind = "all"
    if ":" in req:
        maybe, rest = req.split(":", 1)
        if maybe.strip().lower() in ("emails", "urls", "phones", "numbers", "all"):
            kind, req = maybe.strip().lower(), rest
    found: Dict[str, list] = {}
    if kind in ("emails", "all"):
        found["emails"] = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", req)
    if kind in ("urls", "all"):
        found["urls"] = re.findall(r"https?://[^\s]+", req)
    if kind in ("phones", "all"):
        found["phones"] = re.findall(r"\+?\d[\d\s().-]{7,}\d", req)
    if kind in ("numbers", "all"):
        found["numbers"] = re.findall(r"-?\d+(?:\.\d+)?", req)
    return json.dumps(found, ensure_ascii=False)


def _num_stats(request: str) -> str:
    """Descriptive statistics for a list of numbers (any separators)."""
    import re
    import statistics
    nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", request or "")]
    if not nums:
        return "send some numbers, e.g. ?input=3, 7, 2, 9"
    stdev = statistics.pstdev(nums) if len(nums) > 1 else 0.0
    return (f"count={len(nums)} sum={sum(nums):g} mean={statistics.fmean(nums):.4g} "
            f"median={statistics.median(nums):g} min={min(nums):g} "
            f"max={max(nums):g} stdev={stdev:.4g}")


def build_extended_services(cfg: "Config | None" = None) -> Dict[str, "Service"]:
    """The full catalog, including a service that sells the trained MODEL's signal."""
    services = build_default_services()
    services["keywords"] = Service("keywords", price=15.0, fulfill=_keywords)
    services["sma_signal"] = Service("sma_signal", price=20.0, fulfill=_sma_signal)
    # --- 10 practical, dependency-free services (devs + AI agents) ---
    services["summarize"] = Service("summarize", price=50.0, fulfill=_summarize)
    services["sentiment"] = Service("sentiment", price=30.0, fulfill=_sentiment)
    services["readability"] = Service("readability", price=25.0, fulfill=_readability)
    services["json_tools"] = Service("json_tools", price=20.0, fulfill=_json_tools)
    services["csv_to_json"] = Service("csv_to_json", price=30.0, fulfill=_csv_to_json)
    services["hash"] = Service("hash", price=10.0, fulfill=_hash)
    services["uuid"] = Service("uuid", price=10.0, fulfill=_uuid)
    services["token"] = Service("token", price=15.0, fulfill=_token)
    services["extract"] = Service("extract", price=20.0, fulfill=_extract)
    services["num_stats"] = Service("num_stats", price=20.0, fulfill=_num_stats)
    signal = make_signal_service(cfg)
    services[signal.name] = signal
    return services


# --- selling the deep-learning MODEL itself as a service -------------------
def make_signal_service(cfg: "Config | None" = None, price: float = 40.0) -> "Service":
    """A paid service that sells THIS project's trained model as a market signal.

    The customer sends a product symbol (e.g. "BTC-USD") or a comma-separated
    list of recent prices; the service runs the best trained agent and returns a
    BUY / HOLD / SELL recommendation. This turns your deep-learning model into
    the actual product customers pay for -- the two halves of the project meet.
    """
    agent = _load_best_agent(cfg or Config())

    def fulfill(request: str) -> str:
        conf = cfg or Config()
        if agent is None:
            return "signal unavailable -- train a model first (python -m money_agent.train)"
        prices = _recent_prices(request, conf)
        if not prices:
            return "could not get prices; send a symbol like BTC-USD or a price list"
        obs = _obs_from_prices(prices, conf)
        import torch
        with torch.no_grad():
            logits, _ = agent.model(torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0))
            action = int(torch.argmax(logits).item())
        frac = conf.action_fractions[action]
        label = ("SELL / stay in cash" if frac == 0.0
                 else "BUY / full exposure" if frac >= 1.0
                 else f"HOLD ~{frac:.0%} exposure")
        return f"{label}  (model target exposure {frac:.0%})"

    return Service("market_signal", price=price, fulfill=fulfill)


def _load_best_agent(cfg: "Config"):
    import os
    if not os.path.exists(cfg.checkpoint_path):
        return None
    try:
        import numpy as np
        from .checkpoint import load_checkpoint
        from .ledger import Ledger
        from .population import Population
        pop = Population(cfg, Ledger(":memory:"), np.random.default_rng(0),
                        cfg.obs_dim(), cfg.action_dim(), seed=False)
        load_checkpoint(pop, cfg, cfg.checkpoint_path)
        agents = [a for a in pop.agents.values() if a.alive] or list(pop.agents.values())
        return max(agents, key=lambda a: a.generation) if agents else None
    except Exception:
        return None


def _recent_prices(request: str, cfg: "Config"):
    req = (request or "").strip()
    if "," in req:                       # a raw price list
        try:
            return [float(x) for x in req.split(",") if x.strip()]
        except ValueError:
            return None
    product = req or cfg.data_product     # or a symbol -> cached/downloaded candles
    try:
        from .data import load_prices
        p = load_prices(product, cfg.data_granularity, cfg.data_candles,
                        cache_path=cfg.data_cache or None)
        return list(p) if len(p) else None
    except Exception:
        return None


def _obs_from_prices(prices, cfg: "Config"):
    import numpy as np
    k = cfg.window
    window = np.asarray(prices[-(k + 1):], dtype=np.float64)
    if len(window) < k + 1:
        window = np.concatenate([np.full(k + 1 - len(window), window[0]), window])
    log_returns = np.diff(np.log(window))
    return np.concatenate([log_returns, [0.0, 1.0]]).astype(np.float32)


# ---------------------------------------------------------------------------
# 2) A RECEIPT = a plain record of what happened in a sale.
# ---------------------------------------------------------------------------
@dataclass
class Receipt:
    ok: bool
    service: str
    price: float
    buyer: str
    provider: str
    result: Optional[str]
    note: str
    provider_balance_after: float


# ---------------------------------------------------------------------------
# 3) THE MARKETPLACE = the shopkeeper that enforces "pay, THEN deliver".
# ---------------------------------------------------------------------------
class Marketplace:
    """Sells the provider's services and collects payment into their wallet.

    Every purchase follows the pay-to-unlock rule:
        1. check the buyer can afford the price,
        2. move the payment from buyer -> provider (a real transfer),
        3. ONLY THEN do the work and hand back the result.

    In production, the "deliver only after payment" guarantee is enforced by a
    payment protocol such as x402. Here the Marketplace itself is the trusted
    middleman that performs both steps together.
    """

    def __init__(self, wallet: WalletBackend, provider_id: str,
                 services: Optional[Dict[str, Service]] = None) -> None:
        self.wallet = wallet
        self.provider_id = provider_id
        self.services = services or build_default_services()
        self.sales = 0
        self.earned = 0.0
        self.revenue_by_service: Dict[str, float] = {}

    def menu(self) -> str:
        return "\n".join(f"  - {s.name}: {s.price:g} per request"
                         for s in self.services.values())

    def purchase(self, buyer_id: str, service_name: str, request: str) -> Receipt:
        provider_balance = self.wallet.balance(self.provider_id)

        service = self.services.get(service_name)
        if service is None:
            return Receipt(False, service_name, 0.0, buyer_id, self.provider_id,
                           None, f"no such service {service_name!r}", provider_balance)

        # 1) can the buyer afford it?
        if self.wallet.balance(buyer_id) < service.price:
            return Receipt(False, service_name, service.price, buyer_id,
                           self.provider_id, None, "buyer cannot afford it",
                           provider_balance)

        # 2) take payment: buyer -> provider
        #    (a real on-chain transfer when the backend is base_sepolia)
        self.wallet.transfer(buyer_id, self.provider_id, service.price,
                             reason=f"buy:{service_name}")

        # 3) now -- and only now -- do the work and deliver
        result = service.fulfill(request)
        self.sales += 1
        self.earned += service.price
        self.revenue_by_service[service_name] = (
            self.revenue_by_service.get(service_name, 0.0) + service.price)

        return Receipt(True, service_name, service.price, buyer_id,
                       self.provider_id, result, "paid & delivered",
                       self.wallet.balance(self.provider_id))

    def stats(self) -> Dict[str, object]:
        """Earnings summary for the provider (handy for a dashboard or report)."""
        return {
            "provider": self.provider_id,
            "sales": self.sales,
            "earned": self.earned,
            "balance": self.wallet.balance(self.provider_id),
            "revenue_by_service": dict(self.revenue_by_service),
        }


# ---------------------------------------------------------------------------
# 4) DEMO: run the whole "agent earns crypto by selling a service" loop.
# ---------------------------------------------------------------------------
def demo() -> None:
    backend = os.environ.get("MONEY_AGENT_BACKEND", "ledger")
    # An ephemeral in-memory ledger so repeated offline runs start clean.
    # (The chain backend ignores db_path and uses chain_accounts.json instead.)
    cfg = Config(wallet_backend=backend, db_path=":memory:")
    wallet = make_wallet(cfg)
    print(f"wallet backend: {backend}\n")

    provider = "agent-shopkeeper"   # our AI, selling services
    customer = "customer-alice"     # someone who wants those services

    # Give each a starting wallet. On base_sepolia this funds real testnet accounts.
    wallet.open_account(provider, initial_balance=100.0, reason="agent_seed")
    wallet.open_account(customer, initial_balance=500.0, reason="customer_seed")

    market = Marketplace(wallet, provider_id=provider)
    print("services for sale:")
    print(market.menu(), "\n")

    start_provider = wallet.balance(provider)
    print(f"BEFORE:  agent=${wallet.balance(provider):.2f}   "
          f"customer=${wallet.balance(customer):.2f}\n")

    orders = [
        ("text_stats", "Hello there. This is a test sentence! Is it working?"),
        ("shout",      "buy low sell high"),
        ("text_stats", "One more paragraph to analyze, please."),
    ]
    for name, request in orders:
        r = market.purchase(customer, name, request)
        status = "OK" if r.ok else "XX"
        print(f"[{status}] {name:<11} -> {r.result!r}  ({r.note})")

    print(f"\nAFTER:   agent=${wallet.balance(provider):.2f}   "
          f"customer=${wallet.balance(customer):.2f}")
    earned = wallet.balance(provider) - start_provider
    print(f"\nThe agent EARNED ${earned:.2f} from a real customer -- income from "
          f"OUTSIDE, not from its own treasury.")
    print(f"That is what keeps it alive: balance ${wallet.balance(provider):.2f} "
          f"> bankruptcy line ${cfg.bankruptcy_threshold:.2f}  ->  survives.")

    wallet.close()


if __name__ == "__main__":
    demo()
