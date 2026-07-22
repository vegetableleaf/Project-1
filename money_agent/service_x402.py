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

from .config import Config

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


def _record_sale(service_name: str, price_units: float) -> None:
    """Append a paid sale to x402_sales.json (the dashboard earnings panel reads it)."""
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

    from .service import build_extended_services

    services = build_extended_services(Config())

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
                                   price=_usd(svc.price), network=NETWORK)],
            mime_type="application/json",
            description=f"{svc.name} service (pay {_usd(svc.price)} USDC per call)",
        )
        for name, svc in services.items()
    }
    _maybe_add_bazaar_metadata(routes)
    payment_middleware(app, routes=routes, server=server)

    def _make_handler(service):
        def handler():
            result = service.fulfill(request.args.get("input", ""))
            _record_sale(service.name, service.price)
            return jsonify({"service": service.name,
                            "price_usd": _usd(service.price),
                            "result": result})
        handler.__name__ = f"svc_{service.name}"
        return handler

    for name, svc in services.items():
        app.add_url_rule(f"/service/{name}", view_func=_make_handler(svc))

    @app.route("/")
    def index():
        items = "".join(
            f"<li><code>GET /service/{n}?input=...</code> &mdash; {_usd(s.price)} USDC</li>"
            for n, s in services.items())
        return (f"<h1>Agent x402 services</h1>"
                f"<p>Pay-per-call with USDC on <code>{NETWORK}</code>. "
                f"Receiving wallet: <code>{PAY_TO or '(set PAY_TO!)'}</code></p>"
                f"<ul>{items}</ul>"
                f"<p>Unpaid calls return HTTP 402 with payment instructions.</p>")

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
