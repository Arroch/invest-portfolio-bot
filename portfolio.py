from decimal import Decimal

from models import Holding, PortfolioState
from target import Target
from tinvest import (
    Instrument,
    PortfolioSnapshot,
    fetch_fx_rates,
    fetch_last_prices,
    fetch_portfolio,
)


async def build_portfolio_state(
    client, account_id: str, target: Target, instruments: dict[str, Instrument]
) -> PortfolioState:
    snapshot: PortfolioSnapshot = await fetch_portfolio(client, account_id)

    held_figis = {p.figi for p in snapshot.positions}
    target_figis_by_ticker = {t: instruments[t].figi for t in target.tickers}
    missing_target_figis = [
        figi for figi in target_figis_by_ticker.values() if figi not in held_figis
    ]
    missing_prices = await fetch_last_prices(client, missing_target_figis)

    used_currencies = {p.currency for p in snapshot.positions if p.currency}
    used_currencies |= {instruments[t].currency for t in target.tickers}
    used_currencies |= {c.currency for c in snapshot.cash if c.currency}
    fx_rates = await fetch_fx_rates(
        client, sorted(used_currencies), target.base_currency
    )

    instruments_by_figi: dict[str, Instrument] = {inst.figi: inst for inst in instruments.values()}
    target_tickers_by_figi = {figi: t for t, figi in target_figis_by_ticker.items()}

    target_holdings: list[Holding] = []
    untracked_holdings: list[Holding] = []

    for pos in snapshot.positions:
        currency = pos.currency or "RUB"
        fx = fx_rates.get(currency)
        if fx is None:
            raise RuntimeError(f"Missing FX rate for currency {currency}")
        inst = instruments_by_figi.get(pos.figi)
        if inst is not None:
            ticker = target_tickers_by_figi.get(pos.figi, inst.ticker)
            target_holdings.append(
                Holding(
                    figi=pos.figi,
                    ticker=ticker,
                    name=inst.name,
                    quantity=pos.quantity,
                    last_price=pos.current_price,
                    currency=currency,
                    lot=inst.lot,
                    fx_to_base=fx,
                )
            )
        else:
            untracked_holdings.append(
                Holding(
                    figi=pos.figi,
                    ticker=pos.figi,
                    name="(untracked)",
                    quantity=pos.quantity,
                    last_price=pos.current_price,
                    currency=currency,
                    lot=1,
                    fx_to_base=fx,
                )
            )

    for ticker, figi in target_figis_by_ticker.items():
        if figi in held_figis:
            continue
        inst = instruments[ticker]
        price = missing_prices.get(figi)
        if price is None:
            raise RuntimeError(f"No last price for {ticker} ({figi})")
        fx = fx_rates.get(inst.currency)
        if fx is None:
            raise RuntimeError(f"Missing FX rate for {inst.currency}")
        target_holdings.append(
            Holding(
                figi=figi,
                ticker=ticker,
                name=inst.name,
                quantity=Decimal(0),
                last_price=price,
                currency=inst.currency,
                lot=inst.lot,
                fx_to_base=fx,
            )
        )

    free_cash_breakdown: dict[str, Decimal] = {}
    free_cash_base = Decimal(0)
    for cb in snapshot.cash:
        fx = fx_rates.get(cb.currency)
        if fx is None:
            raise RuntimeError(f"Missing FX rate for cash currency {cb.currency}")
        base = cb.amount * fx
        free_cash_breakdown[cb.currency] = (
            free_cash_breakdown.get(cb.currency, Decimal(0)) + cb.amount
        )
        free_cash_base += base

    target_order = {t: i for i, t in enumerate(target.tickers)}
    target_holdings.sort(key=lambda h: target_order.get(h.ticker, 1_000_000))

    return PortfolioState(
        target_holdings=target_holdings,
        untracked_holdings=untracked_holdings,
        free_cash_base=free_cash_base,
        free_cash_breakdown=free_cash_breakdown,
        base_currency=target.base_currency,
        fetched_at=snapshot.fetched_at,
    )
