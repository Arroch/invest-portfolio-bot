"""Run the same logic as /portfolio and /rebalance, print results to stdout.

Useful for catching runtime errors in build_portfolio_state / compute_drift /
suggest_buys without going through Telegram.
"""

import asyncio
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402
from t_tech.invest import AsyncClient  # noqa: E402

from formatting import format_portfolio, format_rebalance  # noqa: E402
from portfolio import build_portfolio_state  # noqa: E402
from rebalance import compute_drift, suggest_buys  # noqa: E402
from target import load_target  # noqa: E402
from tinvest import resolve_instruments  # noqa: E402


async def main() -> None:
    load_dotenv()
    token = os.environ["TINVEST_TOKEN"]
    account_id = os.environ["TINVEST_ACCOUNT_ID"]
    target_file = os.environ.get("TARGET_FILE", "./target.yaml")

    target = load_target(target_file)
    print(f"Target: {len(target.categories)} categories, {len(target.buckets)} buckets\n")

    async with AsyncClient(token) as client:
        figi_map = await resolve_instruments(client, target.explicit_tickers)
        print(f"Resolved {len(figi_map)} tickers\n")

        state = await build_portfolio_state(client, account_id, target, figi_map)
        cats, _ = compute_drift(state, target)

        print("=" * 80)
        print("PORTFOLIO")
        print("=" * 80)
        print(_strip_html(format_portfolio(state, cats)))
        print()

        print("=" * 80)
        print("REBALANCE (no arg)")
        print("=" * 80)
        result = suggest_buys(state, target, Decimal(0))
        print(_strip_html(format_rebalance(result, target.base_currency)))
        print()

        print("=" * 80)
        print("REBALANCE 50 000")
        print("=" * 80)
        result = suggest_buys(state, target, Decimal(50_000))
        print(_strip_html(format_rebalance(result, target.base_currency)))


def _strip_html(s: str) -> str:
    for tag in ("<b>", "</b>", "<i>", "</i>", "<pre>", "</pre>"):
        s = s.replace(tag, "")
    return s


if __name__ == "__main__":
    asyncio.run(main())
