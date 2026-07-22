"""service_api.py: expose the agent's marketplace over HTTP so OTHER users can
buy its services. This is the "sell services to other people" step -- the
pathway you expect to be the AI's main income.

    python -m money_agent.service_api          # serves http://localhost:8100

Endpoints:
    GET  /services   list services & prices (JSON)
    POST /buy        {"buyer","service","request"} -> receipt (JSON)
    GET  /stats      provider earnings (JSON)
    GET  /           a human-readable page

PAYMENTS -- IMPORTANT
---------------------
For a real, trustless "pay to use" wall over HTTP, use the x402 standard (an
open, Coinbase-backed protocol: the server replies 402 Payment Required, the
caller pays USDC, then receives the result). To keep this runnable offline, the
demo instead gives each new buyer a small "trial credit" in the local ledger and
charges that. Replace the trial-credit step with x402 to accept real crypto.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import Config
from .service import Marketplace, build_extended_services
from .wallet import make_wallet

PROVIDER = "agent-shopkeeper"
TRIAL_CREDIT = float(os.environ.get("SERVICE_TRIAL_CREDIT", "200"))

_cfg = Config(wallet_backend=os.environ.get("MONEY_AGENT_BACKEND", "ledger"),
              db_path=os.environ.get("SERVICE_DB", "service_ledger.sqlite"))
_wallet = make_wallet(_cfg)
if not _wallet.exists(PROVIDER):
    _wallet.open_account(PROVIDER, initial_balance=0.0, reason="provider_open")
_market = Marketplace(_wallet, provider_id=PROVIDER,
                      services=build_extended_services(_cfg))


def _ensure_buyer(buyer_id: str) -> None:
    if not _wallet.exists(buyer_id):
        _wallet.open_account(buyer_id, initial_balance=TRIAL_CREDIT,
                             reason="trial_credit")


_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>agent services</title>
<style>body{font-family:system-ui;max-width:760px;margin:40px auto;padding:0 16px;
line-height:1.5}code{background:#f2f3f5;padding:2px 5px;border-radius:4px}</style></head>
<body><h1>🛒 Agent service marketplace</h1>
<p>This AI sells services for crypto. Endpoints:</p>
<ul>
<li><code>GET /services</code> — list services &amp; prices</li>
<li><code>POST /buy</code> — body <code>{"buyer","service","request"}</code></li>
<li><code>GET /stats</code> — provider earnings</li>
</ul>
<p>Example:</p>
<pre>curl -X POST http://localhost:8100/buy -H "Content-Type: application/json" \\
  -d "{\\"buyer\\":\\"bob\\",\\"service\\":\\"market_signal\\",\\"request\\":\\"BTC-USD\\"}"</pre>
<p>New buyers get a small trial credit. For real crypto payments, wire up x402.</p>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj, ctype: str = "application/json") -> None:
        body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/services"):
            self._send(200, {"provider": PROVIDER,
                             "services": [{"name": s.name, "price": s.price}
                                          for s in _market.services.values()]})
        elif self.path.startswith("/stats"):
            self._send(200, _market.stats())
        else:
            self._send(200, _PAGE, ctype="text/html")

    def do_POST(self) -> None:
        if not self.path.startswith("/buy"):
            self._send(404, {"error": "unknown endpoint"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            self._send(400, {"error": "invalid JSON body"})
            return
        buyer = str(data.get("buyer", "anon"))
        _ensure_buyer(buyer)
        r = _market.purchase(buyer, str(data.get("service", "")),
                             str(data.get("request", "")))
        self._send(200 if r.ok else 402, {
            "ok": r.ok, "service": r.service, "price": r.price,
            "result": r.result, "note": r.note,
            "provider_balance": r.provider_balance_after,
        })

    def log_message(self, *args) -> None:   # keep the console quiet
        pass


def main() -> None:
    port = int(os.environ.get("SERVICE_API_PORT", "8100"))
    services = ", ".join(_market.services)
    print(f"service API on http://localhost:{port}  (provider={PROVIDER})")
    print(f"services for sale: {services}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
