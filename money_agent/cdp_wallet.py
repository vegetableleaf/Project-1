"""cdp_wallet.py: create & use a Coinbase CDP wallet for the AI agent.

A CDP "server wallet" is an EVM account whose private key is secured by
Coinbase's key infrastructure and controlled by YOUR API keys. That makes it a
good fit for an autonomous agent: the agent can receive, send, and pay, and you
can attach a *spend permission* (a hard cap) so it can never move more than you
allow -- exactly the safety rail this project keeps insisting on.

SETUP (do this once)
--------------------
1. Create a free CDP account at https://portal.cdp.coinbase.com
2. Create a **Secret API key** (Portal -> API Keys):  CDP_API_KEY_ID + CDP_API_KEY_SECRET
3. Create a **Wallet Secret** (Portal -> Server Wallets):  CDP_WALLET_SECRET
4. Install the SDK:   pip install cdp-sdk
5. Put the three secrets in environment variables (NEVER in code or git):

       $env:CDP_API_KEY_ID     = "..."
       $env:CDP_API_KEY_SECRET = "..."
       $env:CDP_WALLET_SECRET  = "..."

USAGE
-----
    python -m money_agent.cdp_wallet          # show the agent's wallet address & balances
    python -m money_agent.cdp_wallet --fund   # request FREE Base Sepolia testnet ETH

SECURITY
--------
* Anyone holding those three secrets controls the wallet. Guard them like the
  seed phrase warning from earlier -- env vars or a secrets manager only.
* Stay on Base Sepolia (testnet, free) until everything works. Move to Base
  mainnet only with a tiny balance you can fully afford to lose AND a spend cap.
"""

from __future__ import annotations

import asyncio
import json
import os

ACCOUNT_NAME = os.environ.get("CDP_ACCOUNT_NAME", "money-agent")
NETWORK = os.environ.get("CDP_NETWORK", "base-sepolia")   # testnet first!

_REQUIRED = ("CDP_API_KEY_ID", "CDP_API_KEY_SECRET", "CDP_WALLET_SECRET")


def _check_env() -> bool:
    missing = [k for k in _REQUIRED if not os.environ.get(k)]
    if missing:
        print("Missing environment variable(s): " + ", ".join(missing))
        print("Create a Secret API key + Wallet Secret at "
              "https://portal.cdp.coinbase.com and set them, then re-run.")
        return False
    return True


async def _run(fund: bool) -> None:
    # Trust the OS certificate store so HTTPS works behind corporate TLS.
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass

    try:
        from cdp import CdpClient
    except ImportError:
        print("The CDP SDK isn't installed. Run:  pip install cdp-sdk")
        return

    # CdpClient reads CDP_API_KEY_ID / CDP_API_KEY_SECRET / CDP_WALLET_SECRET from env.
    async with CdpClient() as cdp:
        account = await cdp.evm.get_or_create_account(name=ACCOUNT_NAME)
        print(f"agent wallet '{ACCOUNT_NAME}'")
        print(f"  address : {account.address}")
        print(f"  network : {NETWORK}")
        print(f"  explorer: https://sepolia.basescan.org/address/{account.address}")

        # save the address so the dashboard + earnings reporter can find it
        try:
            with open("cdp_wallet.json", "w", encoding="utf-8") as fh:
                json.dump({"address": account.address, "network": NETWORK}, fh)
        except OSError:
            pass

        if fund:
            if "sepolia" not in NETWORK:
                print("Refusing to use the faucet on a non-testnet network.")
            else:
                print("requesting free testnet ETH from the faucet...")
                await cdp.evm.request_faucet(address=account.address,
                                             network=NETWORK, token="eth")
                print("faucet requested -- balance updates in a few seconds.")

        try:
            result = await cdp.evm.list_token_balances(address=account.address,
                                                       network=NETWORK)
            balances = getattr(result, "balances", None) or []
            if balances:
                print("balances:")
                for b in balances:
                    print(f"  {b}")
            else:
                print("balances: none yet -- fund the address above to get started.")
        except Exception as exc:  # noqa: BLE001 - reading balances shouldn't crash
            print(f"could not read balances: {type(exc).__name__}: {exc}")


def main() -> None:
    import sys
    if not _check_env():
        return
    asyncio.run(_run(fund="--fund" in sys.argv[1:]))


if __name__ == "__main__":
    main()
