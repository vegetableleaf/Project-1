"""notify.py: optional Discord alerts when an x402 service is sold.

Set the DISCORD_WEBHOOK_URL env var to a Discord **channel webhook** and the
x402 server will post a short message every time a paid request is fulfilled --
a live "cha-ching" feed of real sales. Everything here is fire-and-forget and
fully fail-safe: a Discord outage or a bad URL never blocks or breaks a sale.

Create the webhook in Discord: Server Settings -> Integrations -> Webhooks ->
New Webhook -> pick a channel -> Copy Webhook URL. Then set it on each host that
should alert (treat the URL like a secret -- anyone with it can post):

    Fly:     flyctl secrets set DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
    Railway: railway variables --set "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/..."
    Render:  dashboard -> Environment -> DISCORD_WEBHOOK_URL
    Local:   $env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."

Send a test message to confirm it works:
    python -m money_agent.notify --test

Pure-stdlib (urllib), so it runs in the lean cloud image with no extra deps.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.request

_WEBHOOK_ENV = "DISCORD_WEBHOOK_URL"


def _webhook_url() -> str:
    return os.environ.get(_WEBHOOK_ENV, "").strip()


def _post(url: str, content: str) -> None:
    """POST a plain message to a Discord webhook. Never raises."""
    try:
        data = json.dumps({"content": content[:1900]}).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json",
                     "User-Agent": "money-agent-x402/1.0 (+https://x402.org)"})
        urllib.request.urlopen(req, timeout=8).close()
    except Exception:
        # A notification must never affect the sale it is reporting on.
        pass


def notify_sale(service: str, price_usd: str, *, network: str = "",
                pay_to: str = "", sales_count: int | None = None,
                sales_usd: float | None = None) -> None:
    """Fire a Discord alert for a completed sale. No-op if the webhook is unset.

    Runs the HTTP POST on a daemon thread so the buyer's response is never
    delayed by Discord. Buyer input is intentionally NOT included (privacy).
    """
    url = _webhook_url()
    if not url:
        return
    header = f"\U0001F4B0 **x402 sale** \u2014 `{service}` for **{price_usd} USDC**"
    if sales_count is not None:
        total = f", ${sales_usd:.3f} total" if sales_usd is not None else ""
        header += f"  (sale #{sales_count}{total})"
    meta = []
    if network:
        meta.append(f"network `{network}`")
    if pay_to:
        meta.append(f"wallet `{pay_to[:6]}\u2026{pay_to[-4:]}`")
    content = header + ("\n" + " \u00b7 ".join(meta) if meta else "")
    threading.Thread(target=_post, args=(url, content), daemon=True).start()


def _detect_host() -> str:
    """Best-effort label of the cloud platform, from its injected env vars."""
    if os.environ.get("FLY_APP_NAME"):
        return f"Fly.io ({os.environ.get('FLY_REGION', '?')})"
    if os.environ.get("RAILWAY_PROJECT_NAME") or os.environ.get("RAILWAY_SERVICE_NAME"):
        return f"Railway ({os.environ.get('RAILWAY_ENVIRONMENT_NAME', 'production')})"
    if os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_NAME"):
        return f"Render ({os.environ.get('RENDER_SERVICE_NAME', 'service')})"
    return "local / unknown host"


def notify_startup(*, network: str = "", pay_to: str = "") -> None:
    """One-time "earner online" ping on boot, labeled with the host platform.

    No-op if the webhook is unset or DISCORD_ALERT_ON_START is falsy. Handy to
    confirm alerts work on each platform and to see restarts/redeploys.
    """
    if os.environ.get("DISCORD_ALERT_ON_START", "1").strip().lower() in (
            "0", "false", "no", "off"):
        return
    url = _webhook_url()
    if not url:
        return
    header = f"\U0001F7E2 **x402 earner online** \u2014 {_detect_host()}"
    meta = []
    if network:
        meta.append(f"network `{network}`")
    if pay_to:
        meta.append(f"wallet `{pay_to[:6]}\u2026{pay_to[-4:]}`")
    content = header + ("\n" + " \u00b7 ".join(meta) if meta else "")
    threading.Thread(target=_post, args=(url, content), daemon=True).start()


def main() -> None:
    """`python -m money_agent.notify --test` sends a sample alert (synchronous)."""
    url = _webhook_url()
    if not url:
        print(f"Set {_WEBHOOK_ENV} first (a Discord channel webhook URL).")
        return
    try:  # trust the OS cert store so it works behind TLS-inspecting networks
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass
    _post(url, "\U0001F9EA money_agent test alert \u2014 your x402 sale "
               "notifications are working.")
    print("Sent a test alert to Discord (check your channel).")


if __name__ == "__main__":
    main()
