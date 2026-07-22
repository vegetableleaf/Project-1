"""data.py: real historical market data for training & evaluation.

Replaces the synthetic random-walk prices with REAL crypto candles, so the model
learns on -- and is judged on -- actual market behaviour. Data is downloaded once
from Coinbase's public candles API and cached to a CSV; later runs read the cache.

Behind a corporate network we inject `truststore` so HTTPS works through TLS
inspection (same fix used for the RPC connection).

Download/refresh the cache manually:
    python -m money_agent.data BTC-USD
"""

from __future__ import annotations

import csv
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np

_API = "https://api.exchange.coinbase.com/products/{product}/candles"
_MAX_PER_REQUEST = 300  # Coinbase returns at most 300 candles per call


def _http_get_json(url: str, timeout: int = 30):
    # Trust the OS certificate store so the request works behind corporate TLS.
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass
    req = urllib.request.Request(url, headers={"User-Agent": "money_agent/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_candles(product: str = "BTC-USD", granularity: int = 3600,
                  n_candles: int = 2000) -> np.ndarray:
    """Download about `n_candles` recent CLOSE prices (oldest -> newest)."""
    closes: dict[int, float] = {}
    end = datetime.now(timezone.utc)
    remaining = n_candles
    while remaining > 0:
        count = min(_MAX_PER_REQUEST, remaining)
        start = end - timedelta(seconds=granularity * count)
        url = (f"{_API.format(product=product)}"
               f"?granularity={granularity}"
               f"&start={start.isoformat()}&end={end.isoformat()}")
        rows = _http_get_json(url)
        if not rows:
            break
        for r in rows:                      # [time, low, high, open, close, volume]
            closes[int(r[0])] = float(r[4])
        end = start
        remaining -= count
        time.sleep(0.34)                    # be polite to the public rate limit
    times = sorted(closes)
    return np.array([closes[t] for t in times], dtype=np.float64)


def _auto_cache(product: str, granularity: int) -> str:
    return f"prices_{product}_{granularity}.csv"


def load_prices(product: str = "BTC-USD", granularity: int = 3600,
                n_candles: int = 2000, cache_path: str | None = None,
                refresh: bool = False) -> np.ndarray:
    """Return real close prices, using a CSV cache when available.

    Never raises on a network failure -- returns an empty array so callers can
    fall back to synthetic data.
    """
    cache_path = cache_path or _auto_cache(product, granularity)
    if os.path.exists(cache_path) and not refresh:
        with open(cache_path, "r", encoding="utf-8", newline="") as fh:
            return np.array([float(row[0]) for row in csv.reader(fh) if row],
                            dtype=np.float64)
    try:
        prices = fetch_candles(product, granularity, n_candles)
    except Exception as exc:  # noqa: BLE001 - network/cert issues shouldn't crash training
        print(f"[data] could not download prices ({type(exc).__name__}: {exc})")
        return np.array([], dtype=np.float64)

    if prices.size:
        tmp = cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            for p in prices:
                writer.writerow([p])
        os.replace(tmp, cache_path)
    return prices


def train_test_split(prices: np.ndarray, train_frac: float = 0.8):
    """Chronological split: earliest `train_frac` trains, the rest is held out.

    Time order matters -- the test set must be the FUTURE relative to training,
    never shuffled, or the evaluation is cheating.
    """
    cut = int(len(prices) * train_frac)
    return prices[:cut], prices[cut:]


def _main() -> None:
    import sys
    product = sys.argv[1] if len(sys.argv) > 1 else "BTC-USD"
    prices = load_prices(product, refresh=True)
    print(f"downloaded {len(prices)} candles for {product}")
    if len(prices):
        print(f"  range: {prices.min():.2f} .. {prices.max():.2f}  "
              f"last: {prices[-1]:.2f}")


if __name__ == "__main__":
    _main()
