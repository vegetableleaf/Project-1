"""service_x402.py: sell the agent's services for REAL crypto over HTTP via x402.

x402 is Coinbase's open "pay-per-request" standard. When a client calls a paid
route WITHOUT paying, the server replies HTTP 402 Payment Required with payment
instructions; the client pays USDC and retries; the server verifies the payment
through a "facilitator" and returns the result. This is how the AI accepts REAL
money for its services -- payment settles on-chain into your receiving wallet.

Install:
    pip install "x402[flask]"

Run on Base Sepolia TESTNET first (free test USDC, no signup):
    $env:PAY_TO = "0xYourReceivingAddress"     # your CDP wallet address
    python -m money_agent.service_x402         # http://localhost:8402

A buyer (human or AI agent) then pays test USDC per call; it lands in PAY_TO --
real (test) crypto flowing into the wallet. Get test funds from the CDP faucet.

Going to MAINNET (real money) -- only after testnet works:
    $env:X402_NETWORK     = "eip155:8453"                                  # Base mainnet
    $env:X402_FACILITATOR = "https://api.cdp.coinbase.com/platform/v2/x402"  # CDP facilitator
    $env:CDP_API_KEY_ID = "..."; $env:CDP_API_KEY_SECRET = "..."
Start with tiny prices. Mainnet payments are real and irreversible.
"""

from __future__ import annotations

import json
import os
import threading
import time
from urllib.parse import quote_plus

from .config import Config
from .notify import notify_sale, notify_startup

PAY_TO = os.environ.get("PAY_TO", "")
NETWORK = os.environ.get("X402_NETWORK", "eip155:84532")            # Base Sepolia
FACILITATOR = os.environ.get("X402_FACILITATOR", "https://x402.org/facilitator")
PORT = int(os.environ.get("SERVICE_X402_PORT", "8402"))

# A placeholder so the server can still start for a dry run if PAY_TO is unset.
_PAY_TO = PAY_TO or "0x000000000000000000000000000000000000dEaD"


def _usd(price_units: float) -> str:
    """Map an internal service price (sim units) to a small USDC dollar amount."""
    return f"${max(0.001, price_units / 1000.0):.3f}"


SALES_PATH = os.environ.get("X402_SALES_PATH", "x402_sales.json")
_SALES_LOCK = threading.Lock()


def _maybe_add_bazaar_metadata(routes) -> None:
    """When X402_BAZAAR=1, attach discovery metadata so the CDP x402 Bazaar can
    index these endpoints (requires the CDP facilitator + a public URL)."""
    if os.environ.get("X402_BAZAAR", "").strip().lower() not in ("1", "true", "yes", "on"):
        return
    try:
        from x402.extensions.bazaar import OutputConfig, declare_discovery_extension
    except ImportError:
        print('Bazaar metadata skipped -- run: pip install "x402[extensions]"')
        return
    for key, route in routes.items():
        name = key.rsplit("/", 1)[-1]
        ext = declare_discovery_extension(
            input={"input": "BTC-USD"},
            input_schema={"type": "object",
                          "properties": {"input": {"type": "string",
                                                   "description": "request payload / query"}},
                          "required": ["input"]},
            output=OutputConfig(example={"service": name, "result": "..."}),
        )
        try:
            route.extensions = ext
        except (AttributeError, ValueError, TypeError):
            pass


def _record_sale(service_name: str, price_units: float) -> tuple[int, float]:
    """Append a paid sale to x402_sales.json (the dashboard earnings panel reads it).

    Returns the running (count, usd_total) so callers can report it (e.g. Discord).
    """
    usd = max(0.001, price_units / 1000.0)
    with _SALES_LOCK:
        try:
            with open(SALES_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {"count": 0, "usd_total": 0.0, "recent": []}
        data["count"] = int(data.get("count", 0)) + 1
        data["usd_total"] = round(float(data.get("usd_total", 0.0)) + usd, 6)
        data.setdefault("recent", []).insert(
            0, {"service": service_name, "usd": round(usd, 6), "ts": time.time()})
        data["recent"] = data["recent"][:50]
        tmp = SALES_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, SALES_PATH)
        return int(data["count"]), float(data["usd_total"])


# Human-friendly landing-page copy: service name -> (short description, example input).
_SERVICE_META = {
    "summarize":     ("Condense long text down to its key sentences.", "Paste a long article or report and get a tight, readable summary of the main points."),
    "sentiment":     ("Read the mood of text with a labeled score.", "I love this \u2014 it's fast, reliable, and a joy to use!"),
    "keywords":      ("Surface the most important keywords in any text.", "reinforcement learning agents need reward signals and lots of clean data"),
    "readability":   ("Score how easy text is to read (Flesch + grade band).", "This is a fairly simple sentence that most people can read with ease."),
    "text_stats":    ("Word, character and sentence counts in a snap.", "The quick brown fox jumps over the lazy dog."),
    "extract":       ("Pull emails, URLs, phone numbers or numbers out of text.", "emails: reach me at ada@example.com or sales@acme.io"),
    "num_stats":     ("Instant descriptive stats \u2014 mean, median, stdev and more.", "3, 7, 2, 9, 4, 11, 6, 8"),
    "json_tools":    ("Validate, pretty-print or minify any JSON.", '{"b":1,"a":[3,2,1]}'),
    "csv_to_json":   ("Turn CSV (with a header row) into clean JSON records.", "name,role\nAda,engineer\nGrace,admiral"),
    "hash":          ("Hash text with SHA-256/512, SHA-1 or MD5.", "sha256: hash this exact string"),
    "uuid":          ("Generate up to 20 random UUIDv4 identifiers.", "5"),
    "token":         ("Mint a strong random API key / secret token.", "48"),
    "shout":         ("Turn text into loud, attention-grabbing capitals.", "big launch news today"),
    "sma_signal":    ("Moving-average crossover signal from a price series.", "100,101,99,102,105,108,107,110,112,115"),
    "market_signal": ("The trained model's BUY / HOLD / SELL read on a market.", "BTC-USD"),
}

_PAGE_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Services \u00b7 pay-per-call USDC APIs</title>
<style>
  :root{--bg:#0b0f1a;--card:#141b2d;--bd:#25314d;--fg:#e8eef7;--mut:#93a1bd;--acc:#4f8cff;--grn:#3fb950}
  *{box-sizing:border-box}
  body{margin:0;font-family:system-ui,"Segoe UI",Roboto,sans-serif;line-height:1.5;color:var(--fg);
       background:radial-gradient(1200px 600px at 50% -220px,#17223e,#0b0f1a)}
  .wrap{max-width:1000px;margin:0 auto;padding:44px 20px 64px}
  .hero{text-align:center;margin-bottom:22px}
  .hero h1{font-size:36px;margin:0 0 10px;background:linear-gradient(90deg,#7cb0ff,#b98cff);
           -webkit-background-clip:text;background-clip:text;color:transparent}
  .hero p{color:var(--mut);font-size:16px;max-width:640px;margin:0 auto}
  .badges{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin:18px 0 2px}
  .badge{background:var(--card);border:1px solid var(--bd);border-radius:999px;padding:6px 14px;font-size:13px;color:var(--mut)}
  .badge b{color:var(--fg)} .badge.g b{color:var(--grn)}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;margin:28px 0}
  .card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:16px;
        transition:transform .12s ease,border-color .12s ease}
  .card:hover{transform:translateY(-3px);border-color:var(--acc)}
  .card header{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}
  .svc{font-family:ui-monospace,SFMono-Regular,monospace;font-weight:700;font-size:15px;color:#cfe0ff}
  .price{background:rgba(63,185,80,.12);color:var(--grn);border:1px solid rgba(63,185,80,.35);
         border-radius:999px;padding:2px 10px;font-size:12px;font-weight:700;white-space:nowrap}
  .desc{color:var(--mut);font-size:14px;margin:0 0 12px}
  .try{display:inline-flex;align-items:center;gap:8px;font-family:ui-monospace,monospace;font-size:12.5px;
       color:#9db8ea;text-decoration:none;background:#0d1526;border:1px solid var(--bd);border-radius:8px;padding:7px 10px}
  .try span{background:var(--acc);color:#fff;border-radius:5px;padding:1px 7px;font-size:11px;font-weight:700}
  .try:hover{border-color:var(--acc);color:#cfe0ff}
  .how{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:20px 22px}
  .how h2{margin:0 0 16px;font-size:17px}
  .steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px}
  .step{display:flex;gap:12px;align-items:flex-start}
  .step .n{flex:none;width:26px;height:26px;border-radius:50%;background:var(--acc);color:#fff;
           font-weight:700;font-size:13px;display:grid;place-items:center}
  .step p{margin:0;font-size:13.5px;color:var(--mut)} .step b{color:var(--fg)}
  a{color:var(--acc)} code{font-family:ui-monospace,monospace}
  .foot{text-align:center;color:var(--mut);font-size:13px;margin-top:26px} .foot code{color:#cfe0ff}
</style></head><body>
<div class="wrap">
  <div class="hero">
    <h1>\U0001FA99 Agent Services</h1>
    <p><b>__COUNT__</b> instant micro-APIs you can call and pay for per request \u2014 settled in
    <b>USDC</b> over Coinbase's open <b>x402</b> standard. No signup, no API key, no invoice.
    Built for humans and autonomous AI agents alike.</p>
    <div class="badges">
      <span class="badge">network <b>__NET__</b></span>
      <span class="badge g">payment <b>gasless</b></span>
      <span class="badge"><b>__COUNT__</b> services</span>
      <span class="badge">pays to <b>__WALLET__</b></span>
    </div>
  </div>

  <div class="grid">__CARDS__</div>

  <div class="how">
    <h2>How to buy \u2014 one round-trip</h2>
    <div class="steps">
      <div class="step"><div class="n">1</div><p><b>Call</b> any service, e.g. <code>GET /service/summarize?input=\u2026</code></p></div>
      <div class="step"><div class="n">2</div><p><b>Get a 402</b> Payment Required with the exact USDC price and pay-to address.</p></div>
      <div class="step"><div class="n">3</div><p><b>Pay &amp; retry</b> with any x402 client \u2014 your agent signs a gasless USDC authorization.</p></div>
      <div class="step"><div class="n">4</div><p><b>Get JSON</b> back instantly. Prices auto-adjust to demand \u2014 see <a href="/pricing">/pricing</a>.</p></div>
    </div>
  </div>

  <p class="foot">Tip: click any service above to see its live <code>402</code> payment challenge \u00b7
  powered by <a href="https://x402.org" target="_blank" rel="noopener">x402</a> on <code>__NETWORK_ID__</code></p>
</div>
</body></html>"""


def build_app():
    # Trust the OS certificate store so the facilitator's HTTPS calls work behind
    # corporate TLS inspection (the x402 client uses httpx under the hood).
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass

    try:
        from flask import Flask, jsonify, request
        from x402.http import FacilitatorConfig, HTTPFacilitatorClientSync, PaymentOption
        from x402.http.middleware.flask import payment_middleware
        from x402.http.types import RouteConfig
        from x402.mechanisms.evm.exact import ExactEvmServerScheme
        from x402.server import x402ResourceServerSync
    except ImportError as exc:  # pragma: no cover - optional dep
        raise SystemExit('Install the x402 Flask package:  pip install "x402[flask]"') from exc

    from .pricing import DemandPricer
    from .service import build_extended_services

    services = build_extended_services(Config())
    # Demand-aware dynamic pricing: each service's price is resolved live, per
    # request, from how much it is actually being bought (see pricing.py and the
    # /pricing endpoint). Set X402_DYNAMIC_PRICING=0 to pin the base prices.
    pricer = DemandPricer({name: svc.price for name, svc in services.items()})

    app = Flask(__name__)
    # Choose the facilitator. The free testnet facilitator (x402.org) needs no
    # auth. The CDP facilitator (Base mainnet + the x402 Bazaar) requires CDP
    # API-key auth headers -- cdp.x402.create_facilitator_config wires those up.
    if "cdp.coinbase.com" in FACILITATOR:
        cdp_key_id = os.environ.get("CDP_API_KEY_ID", "").strip()
        cdp_key_secret = os.environ.get("CDP_API_KEY_SECRET", "").strip()
        if not (cdp_key_id and cdp_key_secret):
            raise SystemExit("The CDP facilitator needs CDP_API_KEY_ID and "
                             "CDP_API_KEY_SECRET. Set them, or use the free "
                             "testnet facilitator https://x402.org/facilitator.")
        try:
            from cdp.x402 import create_facilitator_config
        except ImportError as exc:  # pragma: no cover - optional dep
            raise SystemExit("The CDP facilitator needs the CDP SDK: "
                             "pip install cdp-sdk") from exc
        facilitator_config = create_facilitator_config(cdp_key_id, cdp_key_secret)
    else:
        facilitator_config = FacilitatorConfig(url=FACILITATOR)
    facilitator = HTTPFacilitatorClientSync(facilitator_config)
    server = x402ResourceServerSync(facilitator)
    server.register(NETWORK, ExactEvmServerScheme())

    routes: dict = {
        f"GET /service/{name}": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=_PAY_TO,
                                   price=pricer.price_hook(name), network=NETWORK)],
            mime_type="application/json",
            description=f"{svc.name} service (demand-priced USDC; live rates at /pricing)",
        )
        for name, svc in services.items()
    }
    _maybe_add_bazaar_metadata(routes)
    payment_middleware(app, routes=routes, server=server)

    def _make_handler(service):
        def handler():
            result = service.fulfill(request.args.get("input", ""))
            price_usd = pricer.usd(service.name)
            count, total = _record_sale(service.name, pricer.price_units(service.name))
            notify_sale(service.name, price_usd, network=NETWORK, pay_to=PAY_TO,
                        sales_count=count, sales_usd=total)
            return jsonify({"service": service.name,
                            "price_usd": price_usd,
                            "result": result})
        handler.__name__ = f"svc_{service.name}"
        return handler

    for name, svc in services.items():
        app.add_url_rule(f"/service/{name}", view_func=_make_handler(svc))

    @app.route("/")
    def index():
        cards = []
        for n in services:
            desc, example = _SERVICE_META.get(n, (f"The {n} service.", "your input here"))
            cards.append(
                '<article class="card">'
                f'<header><span class="svc">{n}</span>'
                f'<span class="price">{pricer.usd(n)} USDC</span></header>'
                f'<p class="desc">{desc}</p>'
                f'<a class="try" href="/service/{n}?input={quote_plus(example)}">'
                f'<span>GET</span> /service/{n}</a>'
                '</article>')
        wallet = (PAY_TO[:6] + "\u2026" + PAY_TO[-4:]) if PAY_TO else "(set PAY_TO)"
        net = ("Base mainnet" if NETWORK == "eip155:8453"
               else "Base Sepolia testnet" if NETWORK == "eip155:84532" else NETWORK)
        return (_PAGE_TEMPLATE
                .replace("__CARDS__", "\n".join(cards))
                .replace("__COUNT__", str(len(services)))
                .replace("__NET__", net)
                .replace("__NETWORK_ID__", NETWORK)
                .replace("__WALLET__", wallet))

    @app.route("/pricing")
    def pricing():
        return jsonify(pricer.analysis())

    # One-time "online" ping (per host) so you can confirm Discord alerts work and
    # see restarts. Set DISCORD_ALERT_ON_START=0 to suppress just this ping.
    notify_startup(network=NETWORK, pay_to=PAY_TO)

    return app


def main() -> None:
    if not PAY_TO:
        print("WARNING: PAY_TO is not set -- payments would have nowhere to go.")
        print('  Set it to your CDP wallet address:  $env:PAY_TO = "0xYourAddress"')
        print("Starting anyway for a dry run.\n")
    app = build_app()
    print(f"x402 service API on http://localhost:{PORT}")
    print(f"  network     = {NETWORK}")
    print(f"  facilitator = {FACILITATOR}")
    print(f"  receiving   = {PAY_TO or '(unset)'}")
    app.run(host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
