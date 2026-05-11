from decimal import Decimal

from classify import find_bucket
from models import BucketState, Holding, PortfolioState
from target import Target, TargetBucket
from tinvest import (
    Instrument,
    fetch_fx_rates,
    fetch_instruments_by_figi,
    fetch_last_prices,
    fetch_portfolio,
)


async def build_portfolio_state(
    client,
    account_id: str,
    target: Target,
    explicit_instruments: dict[str, Instrument],
) -> PortfolioState:
    snapshot = await fetch_portfolio(client, account_id)
    as_of = snapshot.fetched_at

    held_figis = [p.figi for p in snapshot.positions]
    held_instruments = await fetch_instruments_by_figi(client, held_figis)

    used_currencies: set[str] = set()
    used_currencies |= {p.currency for p in snapshot.positions if p.currency}
    used_currencies |= {inst.currency for inst in explicit_instruments.values() if inst.currency}
    used_currencies |= {inst.currency for inst in held_instruments.values() if inst.currency}
    used_currencies |= {c.currency for c in snapshot.cash if c.currency}
    fx_rates = await fetch_fx_rates(client, sorted(used_currencies), target.base_currency)

    # Build per-instrument Holdings (real positions + zero-qty placeholders for explicit-target tickers).
    holdings_by_figi: dict[str, Holding] = {}
    for pos in snapshot.positions:
        inst = held_instruments[pos.figi]
        currency = (pos.currency or inst.currency or target.base_currency).upper()
        fx = _fx(fx_rates, currency)
        holdings_by_figi[pos.figi] = Holding(
            figi=pos.figi,
            ticker=inst.ticker,
            name=inst.name,
            quantity=pos.quantity,
            last_price=pos.current_price,
            currency=currency,
            lot=inst.lot or 1,
            fx_to_base=fx,
            instrument_type=inst.instrument_type,
            class_code=inst.class_code,
            bond_type=inst.bond_type,
            nominal_currency=inst.nominal_currency,
            maturity_date=inst.maturity_date,
        )

    target_explicit_tickers = target.explicit_tickers
    explicit_figi_by_ticker = {t: explicit_instruments[t].figi for t in target_explicit_tickers}
    missing_figis = [
        figi for figi in explicit_figi_by_ticker.values() if figi not in holdings_by_figi
    ]
    missing_prices = await fetch_last_prices(client, missing_figis)
    for ticker, figi in explicit_figi_by_ticker.items():
        if figi in holdings_by_figi:
            continue
        inst = explicit_instruments[ticker]
        price = missing_prices.get(figi)
        if price is None:
            raise RuntimeError(f"No last price for explicit-target ticker {ticker} ({figi})")
        fx = _fx(fx_rates, inst.currency)
        holdings_by_figi[figi] = Holding(
            figi=figi,
            ticker=ticker,
            name=inst.name,
            quantity=Decimal(0),
            last_price=price,
            currency=(inst.currency or target.base_currency).upper(),
            lot=inst.lot or 1,
            fx_to_base=fx,
            instrument_type=inst.instrument_type,
            class_code=inst.class_code,
            bond_type=inst.bond_type,
            nominal_currency=inst.nominal_currency,
            maturity_date=inst.maturity_date,
        )

    # Classify each instrument holding into a bucket (or untracked).
    buckets_list = list(target.buckets)
    cash_bucket = target.cash_bucket
    bucket_holdings: dict[int, list[Holding]] = {id(b): [] for b in buckets_list}
    untracked: list[Holding] = []

    for holding in holdings_by_figi.values():
        bucket = find_bucket(
            buckets_list,
            ticker=holding.ticker,
            instrument_type=holding.instrument_type,
            class_code=holding.class_code,
            bond_type=holding.bond_type,
            nominal_currency=holding.nominal_currency,
            maturity_date=holding.maturity_date,
            as_of=as_of,
        )
        if bucket is None:
            untracked.append(holding)
        else:
            bucket_holdings[id(bucket)].append(holding)

    # Route cash balances:
    #   1. If a non-cash bucket lists this currency in `cash_currencies` → that bucket.
    #   2. Else if currency == base_currency → cash_bucket.
    #   3. Else → untracked (e.g. USD cash with no USD bucket in target).
    currency_to_bucket: dict[str, TargetBucket] = {}
    for b in buckets_list:
        for cur in b.cash_currencies:
            currency_to_bucket[cur.upper()] = b

    free_cash_breakdown: dict[str, Decimal] = {}
    for cb in snapshot.cash:
        currency = cb.currency.upper()
        if cb.amount == 0:
            continue
        fx = _fx(fx_rates, currency)
        cash_holding = Holding(
            figi="",
            ticker=currency,
            name=f"{currency} cash",
            quantity=cb.amount,
            last_price=Decimal(1),
            currency=currency,
            lot=1,
            fx_to_base=fx,
            instrument_type="currency",
        )
        owner_bucket = currency_to_bucket.get(currency)
        if owner_bucket is None and currency == target.base_currency.upper():
            owner_bucket = cash_bucket
        if owner_bucket is not None:
            bucket_holdings[id(owner_bucket)].append(cash_holding)
            free_cash_breakdown[currency] = free_cash_breakdown.get(currency, Decimal(0)) + cb.amount
        else:
            untracked.append(cash_holding)

    bucket_states: list[BucketState] = []
    for b in buckets_list:
        holdings = sorted(
            bucket_holdings[id(b)],
            key=lambda h: (-h.value_base, h.ticker),
        )
        bucket_states.append(BucketState(bucket=b, holdings=tuple(holdings)))

    return PortfolioState(
        buckets=tuple(bucket_states),
        untracked_holdings=tuple(untracked),
        free_cash_breakdown=free_cash_breakdown,
        base_currency=target.base_currency,
        fetched_at=as_of,
    )




def _fx(rates: dict[str, Decimal], currency: str) -> Decimal:
    rate = rates.get((currency or "").upper())
    if rate is None:
        raise RuntimeError(f"Missing FX rate for currency {currency!r}")
    return rate
