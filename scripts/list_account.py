"""Dump everything on the T-Invest account: instruments, cash, totals.

Run from the project root with the venv active and .env loaded:
    python scripts/list_account.py

Requires TINVEST_TOKEN and TINVEST_ACCOUNT_ID env vars.
"""

import asyncio
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402
from t_tech.invest import AsyncClient, InstrumentIdType  # noqa: E402
from t_tech.invest.utils import money_to_decimal, quotation_to_decimal  # noqa: E402


def fmt(value: Decimal, places: int = 2) -> str:
    quant = Decimal(10) ** -places
    s = f"{value.quantize(quant):,.{places}f}".replace(",", " ")
    return s


async def main() -> None:
    load_dotenv()
    token = os.environ["TINVEST_TOKEN"]
    account_id = os.environ["TINVEST_ACCOUNT_ID"]

    async with AsyncClient(token) as client:
        portfolio = await client.operations.get_portfolio(account_id=account_id)
        positions_response = await client.operations.get_positions(account_id=account_id)

        instrument_positions = [p for p in portfolio.positions if p.instrument_type != "currency"]
        currency_positions = [p for p in portfolio.positions if p.instrument_type == "currency"]

        print(f"Account: {account_id}")
        print(
            f"Portfolio totals (sum on T-Invest side, base currency):  "
            f"shares={money_to_decimal(portfolio.total_amount_shares)}  "
            f"bonds={money_to_decimal(portfolio.total_amount_bonds)}  "
            f"etf={money_to_decimal(portfolio.total_amount_etf)}  "
            f"futures={money_to_decimal(portfolio.total_amount_futures)}  "
            f"currencies={money_to_decimal(portfolio.total_amount_currencies)}"
        )
        print()

        print(f"INSTRUMENT POSITIONS ({len(instrument_positions)}):")
        print(f"  {'type':<10} {'ticker':<14} {'class':<6} {'qty':>10}  {'price':>14}  {'value':>16}  name")
        for p in sorted(instrument_positions, key=lambda x: x.instrument_type):
            info = await client.instruments.get_instrument_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=p.figi
            )
            inst = info.instrument
            qty = quotation_to_decimal(p.quantity)
            price = money_to_decimal(p.current_price)
            value = qty * price
            print(
                f"  {p.instrument_type:<10} {inst.ticker:<14} {inst.class_code:<6} "
                f"{fmt(qty, 4):>10}  {fmt(price):>10} {inst.currency.upper():<3}  "
                f"{fmt(value):>12} {inst.currency.upper():<3}  {inst.name}"
            )
        print()

        print(f"CURRENCY POSITIONS in get_portfolio ({len(currency_positions)}):")
        for p in currency_positions:
            qty = quotation_to_decimal(p.quantity)
            price_cur = (p.current_price.currency or "").upper() if p.current_price else ""
            price_val = money_to_decimal(p.current_price) if p.current_price else Decimal(0)
            print(
                f"  figi={p.figi:<14}  qty={fmt(qty):>14}  "
                f"price={fmt(price_val):>10} {price_cur}"
            )
        print()

        print("CASH (from get_positions.money):")
        for mv in positions_response.money:
            print(f"  {(mv.currency or '').upper():<5} {fmt(money_to_decimal(mv)):>14}")
        if positions_response.blocked:
            print("BLOCKED CASH:")
            for mv in positions_response.blocked:
                print(f"  {(mv.currency or '').upper():<5} {fmt(money_to_decimal(mv)):>14}")


if __name__ == "__main__":
    asyncio.run(main())
