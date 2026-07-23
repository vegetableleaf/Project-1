"""dashboard/app.py: a tiny web dashboard for the money_agent colony.

It reads the trainer's OUTPUT files only -- no torch, no web3, no secrets:
  * money_ledger.sqlite  -> every wallet's balance + full transaction history
  * training.log         -> the latest generation number & alive count

It serves a small auto-refreshing web page plus a /api/stats JSON endpoint, and
runs in Docker so you just open http://localhost:8000 in a browser.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

from flask import Flask, Response, jsonify

LEDGER_DB = os.environ.get("LEDGER_DB", "/data/money_ledger.sqlite")
STATUS_JSON = os.environ.get("STATUS_JSON", "/data/status.json")
CHAIN_STATUS = os.environ.get("CHAIN_STATUS", "/data/chain_status.json")
EARNINGS_STATUS = os.environ.get("EARNINGS_STATUS", "/data/earnings_status.json")
VAULT_ACCOUNT = os.environ.get("VAULT_ACCOUNT", "__vault__")
PORT = int(os.environ.get("PORT", "8000"))
REFRESH_SECONDS = int(os.environ.get("REFRESH_SECONDS", "5"))

app = Flask(__name__)


# --------------------------------------------------------------- data access
def _connect() -> Optional[sqlite3.Connection]:
    """Open the ledger read-only. Returns None if it doesn't exist yet."""
    if not os.path.exists(LEDGER_DB):
        return None
    # Try a normal read-only open first (sees live data, incl. the WAL). If the
    # file is on a read-only mount where WAL shared-memory can't be created,
    # fall back to 'immutable' (reads the main file; may be slightly behind).
    for uri in (f"file:{LEDGER_DB}?mode=ro", f"file:{LEDGER_DB}?immutable=1"):
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=2.0)
            conn.row_factory = sqlite3.Row
            conn.execute("SELECT 1 FROM accounts LIMIT 1")
            return conn
        except sqlite3.Error:
            continue
    return None


def read_wallets() -> List[Dict[str, Any]]:
    conn = _connect()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT account_id, balance FROM accounts ORDER BY balance DESC"
        ).fetchall()
        return [{"account_id": r["account_id"], "balance": float(r["balance"])}
                for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def read_transactions(limit: int = 60) -> List[Dict[str, Any]]:
    conn = _connect()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT account_id, delta, balance, reason, ts "
            "FROM transactions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{
            "account_id": r["account_id"],
            "delta": float(r["delta"]),
            "balance": float(r["balance"]),
            "reason": r["reason"],
            "ts": float(r["ts"]),
        } for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def read_peak() -> float:
    """The highest balance any (non-vault) wallet ever reached."""
    conn = _connect()
    if conn is None:
        return 0.0
    try:
        row = conn.execute(
            "SELECT MAX(balance) FROM transactions WHERE account_id != ?",
            (VAULT_ACCOUNT,)).fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0
    except sqlite3.Error:
        return 0.0
    finally:
        conn.close()


def read_series(limit: int = 2000) -> Dict[str, List[Dict[str, float]]]:
    """Balance-over-time for each wallet (oldest -> newest), for the chart."""
    conn = _connect()
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            "SELECT account_id, balance, ts FROM transactions "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    series: Dict[str, List[Dict[str, float]]] = {}
    for r in reversed(rows):  # back to chronological order
        series.setdefault(r["account_id"], []).append(
            {"ts": float(r["ts"]), "balance": float(r["balance"])})
    return series


def read_status() -> Dict[str, Optional[Any]]:
    """Read the trainer's status.json (generation + alive count). Empty if absent."""
    try:
        with open(STATUS_JSON, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return {"generation": d.get("generation"), "alive": d.get("alive")}
    except (OSError, ValueError):
        return {"generation": None, "alive": None}


def read_chain() -> Optional[Dict[str, Any]]:
    """Read the on-chain snapshot (chain_status.json) if the host reporter wrote one."""
    try:
        with open(CHAIN_STATUS, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def read_earnings() -> Optional[Dict[str, Any]]:
    """Read the earnings snapshot (CDP wallet balance + x402 sales) if present."""
    try:
        with open(EARNINGS_STATUS, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _agent_index(account_id: str) -> int:
    tail = account_id.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else -1


def build_stats() -> Dict[str, Any]:
    wallets = read_wallets()
    agents = [w for w in wallets if w["account_id"] != VAULT_ACCOUNT]
    vault = next((w["balance"] for w in wallets
                  if w["account_id"] == VAULT_ACCOUNT), 0.0)
    balances = [w["balance"] for w in agents]
    gen = read_status()

    # Chart: keep only the 5 most-recent agents (highest index), excluding the
    # vault, so the graph stays readable as the colony grows.
    full_series = read_series()
    recent = sorted((a for a in full_series if a != VAULT_ACCOUNT),
                    key=_agent_index, reverse=True)[:5]
    series = {a: full_series[a] for a in recent}

    return {
        "generation": gen["generation"],
        "alive": gen["alive"],
        "best_balance": max(balances) if balances else 0.0,
        "peak_balance": read_peak(),
        "mean_balance": (sum(balances) / len(balances)) if balances else 0.0,
        "vault": vault,
        "total_holdings": sum(w["balance"] for w in wallets),
        "wallet_count": len(wallets),
        "wallets": wallets,
        "transactions": read_transactions(),
        "series": series,
        "chain": read_chain(),
        "earnings": read_earnings(),
        "has_data": bool(wallets),
        "refresh_seconds": REFRESH_SECONDS,
    }


@app.route("/api/stats")
def api_stats():
    return jsonify(build_stats())


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.route("/")
def index():
    return Response(PAGE.replace("__REFRESH__", str(REFRESH_SECONDS)),
                    mimetype="text/html")


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>money_agent dashboard</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, "Segoe UI", Roboto, sans-serif;
         background:#0f1420; color:#e6edf3; }
  header { padding:18px 24px; background:#161c2c; border-bottom:1px solid #263149;
           display:flex; align-items:center; justify-content:space-between; }
  header h1 { margin:0; font-size:18px; font-weight:600; }
  .muted { color:#8b98b0; font-size:13px; }
  main { padding:24px; max-width:1100px; margin:0 auto; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
           gap:14px; margin-bottom:8px; }
  .card { background:#161c2c; border:1px solid #263149; border-radius:12px; padding:16px; }
  .card .label { font-size:12px; color:#8b98b0; text-transform:uppercase; letter-spacing:.05em; }
  .card .value { font-size:24px; font-weight:700; margin-top:6px; }
  h2 { font-size:15px; color:#b9c4d8; margin:26px 0 10px; }
  table { width:100%; border-collapse:collapse; background:#131a28;
          border:1px solid #263149; border-radius:12px; overflow:hidden; }
  th, td { text-align:left; padding:10px 14px; font-size:13px; border-bottom:1px solid #1e2740; }
  th { background:#1a2233; color:#8b98b0; font-weight:600; }
  tr:last-child td { border-bottom:none; }
  /* Wallets list: show ~8 rows at once; scroll for the rest (exact height set in JS). */
  #wallets { max-height:360px; overflow-y:auto; border:1px solid #263149; border-radius:12px; }
  #wallets table { border:none; border-radius:0; }
  #wallets thead th { position:sticky; top:0; z-index:1; }
  #wallets::-webkit-scrollbar { width:10px; }
  #wallets::-webkit-scrollbar-thumb { background:#2b3856; border-radius:8px; }
  #wallets::-webkit-scrollbar-track { background:#0f1420; }
  .pos { color:#3fb950; } .neg { color:#f85149; }
  .pill { font-family:ui-monospace, monospace; font-size:12px; color:#c9d4e8; }
  .empty { padding:40px; text-align:center; color:#8b98b0;
           background:#131a28; border:1px solid #263149; border-radius:12px; }
  #chart { background:#131a28; border:1px solid #263149; border-radius:12px; padding:14px 12px 8px; }
  .legend { display:flex; flex-wrap:wrap; gap:14px; margin-top:8px; padding:0 4px; }
  .legend .lg { display:inline-flex; align-items:center; gap:6px; font-size:12px; color:#b9c4d8; }
  .legend .lg i { width:14px; height:3px; border-radius:2px; display:inline-block; }
  .toggle { display:flex; gap:6px; }
  .toggle button { background:#1a2233; color:#b9c4d8; border:1px solid #263149;
                   padding:6px 12px; border-radius:8px; font-size:13px; cursor:pointer; }
  .toggle button.active { background:#1f6feb; color:#fff; border-color:#1f6feb; }
  .addr { font-family:ui-monospace, monospace; font-size:12px; color:#8b98b0; }
  a { color:#58a6ff; text-decoration:none; }
  a:hover { text-decoration:underline; }
  .earnings { background:linear-gradient(90deg,#0d2a1a,#131a28); border:1px solid #1f6f46;
              border-radius:12px; padding:14px 16px; margin-bottom:20px; }
  .ecards { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; }
  .ecard { background:#0f1420; border:1px solid #23324a; border-radius:10px; padding:12px; }
  .ecard .label { font-size:11px; color:#8b98b0; text-transform:uppercase; letter-spacing:.05em; }
  .ecard .value { font-size:20px; font-weight:700; margin-top:4px; }
  .ecard.usdc .value { color:#3fb950; }
  .killswitch { margin-top:8px; background:#3d1418; border:1px solid #f85149; color:#ffb4ab;
                border-radius:8px; padding:8px 12px; font-weight:600; }
</style>
</head>
<body>
<header>
  <h1>&#129689; money_agent dashboard</h1>
  <div class="toggle">
    <button id="btnLedger" class="active" onclick="setView('ledger')">Offline ledger</button>
    <button id="btnChain" onclick="setView('chain')">On-chain (Base Sepolia)</button>
  </div>
  <span class="muted">every __REFRESH__s &middot; <span id="updated">connecting&hellip;</span></span>
</header>
<main>
  <div id="earnings" class="earnings"></div>
  <div id="ledgerView">
    <div class="cards" id="cards"></div>
    <h2>Balance over time <span class="muted">(5 most recent agents)</span></h2>
    <div id="chart"></div>
    <h2>Wallets &mdash; current balances</h2>
    <div id="wallets"></div>
    <h2>Transaction history</h2>
    <div id="transactions"></div>
  </div>
  <div id="chainView" style="display:none">
    <div id="chainStatus" class="muted" style="margin-bottom:12px"></div>
    <h2>On-chain wallets &mdash; Base Sepolia</h2>
    <div id="chainWallets"></div>
  </div>
</main>
<script>
const REFRESH = __REFRESH__ * 1000;
const money = v => '$' + Number(v).toLocaleString(undefined,
  {minimumFractionDigits:2, maximumFractionDigits:2});
const esc = s => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

let VIEW = 'ledger';
function setView(v) {
  VIEW = v;
  document.getElementById('ledgerView').style.display = v === 'ledger' ? '' : 'none';
  document.getElementById('chainView').style.display  = v === 'chain'  ? '' : 'none';
  document.getElementById('btnLedger').classList.toggle('active', v === 'ledger');
  document.getElementById('btnChain').classList.toggle('active', v === 'chain');
}
const card = (label, value) =>
  `<div class="card"><div class="label">${esc(label)}</div><div class="value">${value}</div></div>`;

// Constrain a scrollable table to show exactly `n` body rows (header + n rows);
// any extra rows stay reachable via the container's scrollbar.
function clampRows(id, n) {
  const box = document.getElementById(id);
  if (!box) return;
  const head = box.querySelector('thead tr');
  const rows = box.querySelectorAll('tbody tr');
  const hh = head ? head.getBoundingClientRect().height : 0;
  if (!head || hh === 0 || rows.length <= n) { box.style.maxHeight = ''; return; }
  let h = hh;
  for (let i = 0; i < n; i++) h += rows[i].getBoundingClientRect().height;
  box.style.maxHeight = (Math.ceil(h) + 1) + 'px';
}

function drawChart(series) {
  const box = document.getElementById('chart');
  const accts = Object.keys(series).filter(a => series[a].length);
  if (!accts.length) { box.innerHTML = '<div class="empty">No history yet.</div>'; return; }
  let T = [], B = [];
  for (const a of accts) for (const p of series[a]) { T.push(p.ts); B.push(p.balance); }
  const W = Math.max(320, box.clientWidth - 24), H = 240, pad = 44;
  const tMin = Math.min(...T), tMax = Math.max(...T);
  const bMin = Math.min(...B), bMax = Math.max(...B);
  const X = t => pad + (W - 2*pad) * (tMax === tMin ? 0.5 : (t - tMin)/(tMax - tMin));
  const Y = b => (H - pad) - (H - 2*pad) * (bMax === bMin ? 0.5 : (b - bMin)/(bMax - bMin));
  const palette = ['#58a6ff','#3fb950','#f778ba','#d29922','#a371f7','#f85149','#39c5cf'];
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">`;
  svg += `<line x1="${pad}" y1="${Y(bMax)}" x2="${W-pad}" y2="${Y(bMax)}" stroke="#263149"/>`;
  svg += `<line x1="${pad}" y1="${Y(bMin)}" x2="${W-pad}" y2="${Y(bMin)}" stroke="#263149"/>`;
  svg += `<text x="6" y="${Y(bMax)+4}" fill="#8b98b0" font-size="11">${money(bMax)}</text>`;
  svg += `<text x="6" y="${Y(bMin)+4}" fill="#8b98b0" font-size="11">${money(bMin)}</text>`;
  let legend = '';
  accts.forEach((a, i) => {
    const color = palette[i % palette.length];
    const pts = series[a].map(p => `${X(p.ts).toFixed(1)},${Y(p.balance).toFixed(1)}`).join(' ');
    svg += `<polyline fill="none" stroke="${color}" stroke-width="2" points="${pts}"/>`;
    legend += `<span class="lg"><i style="background:${color}"></i>${esc(a)}</span>`;
  });
  svg += '</svg>';
  box.innerHTML = svg + `<div class="legend">${legend}</div>`;
}

function renderChain(chain) {
  const status = document.getElementById('chainStatus');
  const box = document.getElementById('chainWallets');
  if (!chain) {
    status.innerHTML = 'No on-chain data yet. On the host, run: ' +
      '<span class="pill">python -m money_agent.chain_report --watch 20</span>';
    box.innerHTML = '';
    return;
  }
  const when = chain.updated ? new Date(chain.updated * 1000).toLocaleTimeString() : '';
  status.innerHTML = chain.connected
    ? `Connected to Base Sepolia (chain ${chain.chain_id}) &middot; updated ${when}`
    : `Not connected: ${esc(chain.error || 'unknown')} &middot; ${when}`;
  const rows = chain.wallets || [];
  if (!rows.length) { box.innerHTML = '<div class="empty">No wallets in the keystore yet.</div>'; return; }
  let t = '<table><thead><tr><th>Wallet</th><th>Address</th><th>Balance</th><th>ETH</th><th></th></tr></thead><tbody>';
  for (const w of rows) {
    t += `<tr><td class="pill">${esc(w.account_id)}</td>` +
         `<td class="addr">${esc(w.address)}</td>` +
         `<td>${money(w.balance)}</td>` +
         `<td>${Number(w.eth).toFixed(6)}</td>` +
         `<td><a href="${esc(w.explorer_url)}" target="_blank" rel="noopener">BaseScan &#8599;</a></td></tr>`;
  }
  t += '</tbody></table>';
  box.innerHTML = t + '<p class="muted" style="margin-top:10px">Full transaction history is on BaseScan &mdash; click any wallet.</p>';
}

function card2(label, value, cls) {
  return `<div class="ecard ${cls}"><div class="label">${esc(label)}</div><div class="value">${value}</div></div>`;
}

function renderEarnings(e) {
  const box = document.getElementById('earnings');
  if (!e || !e.address) {
    box.innerHTML = '<div class="muted">&#128176; Real earnings: set up your CDP wallet, then run '
      + '<span class="pill">python -m money_agent.earnings_report --watch 30</span> on the host.</div>';
    return;
  }
  const short = e.address.slice(0, 8) + '\u2026' + e.address.slice(-4);
  const link = e.explorer ? `<a href="${e.explorer}" target="_blank" rel="noopener">${esc(short)}</a>` : esc(short);
  const off = e.connected ? '' : ' &middot; <span class="neg">offline</span>';
  let safety = '';
  if (e.safety) {
    const s = e.safety;
    if (s.kill_switch) {
      safety = `<div class="killswitch">&#128721; KILL SWITCH TRIPPED &mdash; balance below $${Number(s.kill_switch_threshold).toFixed(2)}; all spending halted.</div>`;
    } else {
      safety = `<div class="muted" style="margin-top:6px">&#128737; daily spend cap $${Number(s.daily_cap).toFixed(2)} &middot; spent $${Number(s.spent_today).toFixed(2)} &middot; <b>$${Number(s.remaining_today).toFixed(2)} left today</b> &middot; kill switch OK</div>`;
    }
  }
  box.innerHTML =
    '<div class="ecards">'
    + card2('CDP wallet USDC', '$' + Number(e.usdc).toFixed(2), 'usdc')
    + card2('CDP wallet ETH', Number(e.eth).toFixed(6), '')
    + card2('x402 sales', e.sales_count ?? 0, '')
    + card2('x402 revenue', '$' + Number(e.sales_usd || 0).toFixed(3), '')
    + '</div>'
    + `<div class="muted" style="margin-top:8px">wallet ${link} on <code>${esc(e.network)}</code>${off}</div>`
    + safety;
}

async function refresh() {
  let s;
  try { s = await (await fetch('/api/stats')).json(); }
  catch (e) { document.getElementById('updated').textContent = 'trainer offline'; return; }

  renderChain(s.chain);
  renderEarnings(s.earnings);

  if (!s.has_data) {
    document.getElementById('cards').innerHTML =
      '<div class="empty">No data yet. Start the trainer and this fills in automatically.</div>';
    document.getElementById('wallets').innerHTML = '';
    document.getElementById('transactions').innerHTML = '';
    document.getElementById('chart').innerHTML = '';
    document.getElementById('updated').textContent = 'waiting for data';
    return;
  }

  document.getElementById('cards').innerHTML =
    card('Generation', s.generation ?? '&mdash;') +
    card('Alive agents', s.alive ?? '&mdash;') +
    card('Best wallet (now)', money(s.best_balance)) +
    card('Peak balance (ever)', money(s.peak_balance)) +
    card('Vault (banked)', money(s.vault)) +
    card('Total holdings', money(s.total_holdings));
  drawChart(s.series || {});

  let w = '<table><thead><tr><th>Wallet</th><th>Balance</th></tr></thead><tbody>';
  for (const row of s.wallets)
    w += `<tr><td class="pill">${esc(row.account_id)}</td><td>${money(row.balance)}</td></tr>`;
  w += '</tbody></table>';
  document.getElementById('wallets').innerHTML = w;
  clampRows('wallets', 8);   // show 8 agents at once; scroll to reach the others

  let t = '<table><thead><tr><th>Time</th><th>Wallet</th><th>Change</th>' +
          '<th>Balance after</th><th>Reason</th></tr></thead><tbody>';
  for (const tx of s.transactions) {
    const cls = tx.delta >= 0 ? 'pos' : 'neg';
    const sign = tx.delta >= 0 ? '+' : '';
    const when = new Date(tx.ts * 1000).toLocaleString();
    t += `<tr><td class="muted">${esc(when)}</td><td class="pill">${esc(tx.account_id)}</td>` +
         `<td class="${cls}">${sign}${money(tx.delta)}</td><td>${money(tx.balance)}</td>` +
         `<td class="pill">${esc(tx.reason)}</td></tr>`;
  }
  t += '</tbody></table>';
  if (!s.transactions.length) t = '<div class="empty">No transactions yet.</div>';
  document.getElementById('transactions').innerHTML = t;

  document.getElementById('updated').textContent = 'updated ' + new Date().toLocaleTimeString();
}
refresh();
setInterval(refresh, REFRESH);
</script>
</body>
</html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
