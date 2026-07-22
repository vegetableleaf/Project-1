"""cdp_swap.py: convert a little USDC into native ETH (for gas) on Base mainnet.

WHY THIS EXISTS
---------------
On Base, on-chain *gas* is paid in native ETH, not USDC. Earning USDC via x402 is
gasless (the facilitator submits the transfer), so you can EARN with 0 ETH. But
the moment YOU want to move funds yourself -- e.g. send USDC to an exchange to
cash out -- your wallet must pay gas in ETH.

This tool swaps a small amount of your USDC into native ETH using Coinbase CDP's
swap (0x under the hood).

HONEST CAVEAT (read this)
-------------------------
This swap sends a real transaction from your regular CDP account, so the account
must ALREADY hold a tiny bit of native ETH to pay the gas for the swap + token
approval. In other words, this is a **top-up** tool, not a from-zero bootstrap.

So the first ETH still has to come from outside:
  * EASIEST: buy ~$1-2 of ETH on the Coinbase app/exchange and send it to your
    wallet address on the **Base** network (Coinbase pays the withdrawal fee, so
    your wallet needs no gas to receive). Keep your USDC intact.
Once the wallet has a little ETH, use THIS tool to top up ETH from your USDC
earnings without going back to Coinbase.

USAGE
-----
    python -m money_agent.cdp_swap                    # DRY RUN: quote $1 USDC -> ETH
    python -m money_agent.cdp_swap --usd 1.5          # quote a specific USD amount
    python -m money_agent.cdp_swap --usd 1.0 --execute  # actually perform the swap

By default this only prints a price quote and does nothing on-chain. You must add
--execute to move real funds. Real money -> testnet first if you can.

ENV
---
    CDP_API_KEY_ID / CDP_API_KEY_SECRET / CDP_WALLET_SECRET   (same as cdp_wallet)
    CDP_NETWORK      "base" (default here, mainnet) -- swaps need real liquidity
    CDP_ACCOUNT_NAME "money-agent" (default)
"""

from __future__ import annotations

import argparse
import asyncio
import os

# Token addresses on Base mainnet.
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
# 0x/aggregator sentinel for *native* ETH (what you need for gas).
NATIVE_ETH = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"

ACCOUNT_NAME = os.environ.get("CDP_ACCOUNT_NAME", "money-agent")
# Swaps need real liquidity, so default to mainnet here (unlike cdp_wallet).
NETWORK = os.environ.get("CDP_NETWORK", "base")

_REQUIRED = ("CDP_API_KEY_ID", "CDP_API_KEY_SECRET", "CDP_WALLET_SECRET")


def _check_env() -> bool:
    missing = [k for k in _REQUIRED if not os.environ.get(k)]
    if missing:
        print("Missing environment variable(s): " + ", ".join(missing))
        print("Set your CDP secrets (see cdp_wallet.py) and re-run.")
        return False
    return True


def _native_eth_balance(balances) -> float | None:
    """Best-effort read of the account's native ETH balance, in ETH."""
    for b in balances or []:
        token = getattr(b, "token", None)
        symbol = getattr(token, "symbol", None) or getattr(b, "symbol", None)
        if symbol and str(symbol).upper() in ("ETH", "WETH"):
            amount = getattr(b, "amount", None)
            raw = getattr(amount, "amount", None) if amount is not None else None
            decimals = getattr(amount, "decimals", 18) if amount is not None else 18
            if raw is not None:
                try:
                    return int(raw) / (10 ** int(decimals))
                except (ValueError, TypeError):
                    return None
    return None


async def _run(usd: float, execute: bool) -> None:
    # Trust the OS certificate store so HTTPS works behind corporate TLS.
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass

    try:
        from cdp import CdpClient
        from cdp.actions.evm.swap.types import AccountSwapOptions
    except ImportError:
        print("The CDP SDK isn't installed. Run:  pip install cdp-sdk")
        return

    if "sepolia" in NETWORK or "testnet" in NETWORK:
        print(f"note: swaps usually have no liquidity on '{NETWORK}'. "
              "Use CDP_NETWORK=base for a real quote.")

    from_amount = str(int(round(usd * 1_000_000)))  # USDC has 6 decimals

    async with CdpClient() as cdp:
        account = await cdp.evm.get_or_create_account(name=ACCOUNT_NAME)
        print(f"wallet '{ACCOUNT_NAME}': {account.address}  (network: {NETWORK})")
        print(f"quote: swap {usd:.6g} USDC  ->  native ETH")

        # 1) Always fetch a read-only price quote first.
        try:
            price = await cdp.evm.get_swap_price(
                from_token=USDC_BASE,
                to_token=NATIVE_ETH,
                from_amount=from_amount,
                network=NETWORK,
                taker=account.address,
            )
        except Exception as exc:  # noqa: BLE001 - show a friendly message
            print(f"could not get a swap price: {type(exc).__name__}: {exc}")
            print("If this is a TLS error you are behind a corporate proxy; try "
                  "from a normal network or a cloud host.")
            return

        to_amount = getattr(price, "to_amount", None)
        if to_amount is None:
            print("no liquidity / quote unavailable for that amount right now.")
            return
        eth_out = int(to_amount) / 1e18
        print(f"  estimated out : {eth_out:.8f} ETH")
        ratio = getattr(price, "price_ratio", None)
        if ratio is not None:
            print(f"  price ratio   : {ratio}")

        if not execute:
            print("\nDRY RUN -- nothing was moved. Re-run with --execute to swap "
                  "for real.")
            return

        # 2) Execute: warn if there is no ETH to pay gas with.
        try:
            result = await cdp.evm.list_token_balances(address=account.address,
                                                       network=NETWORK)
            eth_bal = _native_eth_balance(getattr(result, "balances", None))
            if eth_bal is not None and eth_bal <= 0:
                print("\nWARNING: this account has ~0 native ETH, so it cannot pay "
                      "gas for the swap. Send ~$1 of ETH to the address above on "
                      "the Base network first (see this file's header).")
                return
        except Exception:  # noqa: BLE001 - balance check is best-effort
            pass

        print("\nexecuting swap on mainnet (real funds)...")
        try:
            swap = await account.swap(AccountSwapOptions(
                network=NETWORK,
                from_token=USDC_BASE,
                to_token=NATIVE_ETH,
                from_amount=from_amount,
                slippage_bps=100,   # 1% max slippage
            ))
        except Exception as exc:  # noqa: BLE001 - surface the reason, don't crash
            print(f"swap failed: {type(exc).__name__}: {exc}")
            print("Common cause: not enough native ETH for gas. Top up a dollar "
                  "of ETH on Base and try again.")
            return

        tx = getattr(swap, "transaction_hash", None)
        print("swap submitted.")
        if tx:
            print(f"  tx      : {tx}")
            print(f"  explorer: https://basescan.org/tx/{tx}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Swap a little USDC into native ETH (gas) on Base via CDP.")
    parser.add_argument("--usd", type=float, default=1.0,
                        help="USD amount of USDC to swap (default 1.0)")
    parser.add_argument("--execute", action="store_true",
                        help="actually perform the swap (default is a dry-run quote)")
    args = parser.parse_args()

    if args.usd <= 0:
        print("--usd must be positive.")
        return
    if not _check_env():
        return
    asyncio.run(_run(usd=args.usd, execute=args.execute))


if __name__ == "__main__":
    main()
