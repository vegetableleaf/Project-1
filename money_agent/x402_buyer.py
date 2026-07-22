"""x402_buyer.py: simulate a paying customer for the agent's x402 services.

Pairs with service_x402.py. Point it at a running x402 service and it will:
  1. request a paid endpoint,
  2. receive the 402 Payment Required,
  3. sign & send a USDC payment from a buyer wallet,
  4. print the delivered result.

Use a THROWAWAY buyer key funded with TEST USDC on Base Sepolia (from the CDP
faucet) -- this lets you watch test USDC actually move into the seller's wallet
before any real money is involved.

    # make a throwaway buyer key, fund it with test USDC, then:
    $env:BUYER_PRIVATE_KEY = "0x..."
    python -m money_agent.x402_buyer market_signal BTC-USD
"""

from __future__ import annotations

import os
import sys
from urllib.parse import quote


def main() -> None:
    # Trust the OS cert store so the payment (via httpx/requests) works behind
    # corporate TLS inspection.
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass

    key = os.environ.get("BUYER_PRIVATE_KEY")
    if not key:
        print("Set a throwaway TESTNET buyer key that holds test USDC:")
        print('  $env:BUYER_PRIVATE_KEY = "0x..."')
        print("Make one:  python -c \"from eth_account import Account; "
              "print(Account.create().key.hex())\"")
        print("Then fund that address with test USDC from the CDP faucet.")
        return

    base = os.environ.get("SERVICE_URL", "http://localhost:8402").rstrip("/")
    network = os.environ.get("X402_NETWORK", "eip155:84532")
    service = sys.argv[1] if len(sys.argv) > 1 else "shout"
    payload = sys.argv[2] if len(sys.argv) > 2 else "hello from a paying customer"

    from eth_account import Account
    from x402.client import x402ClientSync
    from x402.http.clients.requests import x402_requests
    from x402.mechanisms.evm.exact import register_exact_evm_client

    account = Account.from_key(key)
    print(f"buyer wallet: {account.address}")

    client = x402ClientSync()
    register_exact_evm_client(client, account, networks=[network])
    session = x402_requests(client)

    url = f"{base}/service/{service}?input={quote(payload)}"
    print(f"calling (and paying for): {url}")
    resp = session.get(url)
    print(f"HTTP {resp.status_code}")
    try:
        print("result:", resp.json())
    except ValueError:
        print(resp.text[:500])


if __name__ == "__main__":
    main()
