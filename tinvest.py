from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from t_tech.invest import AsyncClient, InstrumentIdType, MoneyValue, Quotation
from t_tech.invest.utils import money_to_decimal, quotation_to_decimal

MOEX_CLASS_CODES = {
    "TQBR",      # российские акции (MOEX)
    "TQOB",      # ОФЗ
    "TQTF",      # рублёвые ETF
    "TQCB",      # корп. облигации
    "TQTE",      # корп. облигации
    "TQTD",      # корп. облигации
    "TQIR",      # иностранные акции на MOEX
    "CETS",      # валютная секция MOEX
    "SPBRU",     # СПБ Биржа (RUB-листинг T-фондов: TMOS@, TMON@, и т.п.)
    "SPBHKEX",   # СПБ Биржа Hong Kong
}

FX_TICKER_BY_CURRENCY = {
    "USD": "USD000UTSTOM",
    "EUR": "EUR_RUB__TOM",
    "HKD": "HKDRUB_TOM",
    "CNY": "CNYRUB_TOM",
    "GBP": "GBPRUB_TOM",
    "CHF": "CHFRUB_TOM",
    "JPY": "JPYRUB_TOM",
    "TRY": "TRYRUB_TOM",
    "KZT": "KZTRUB_TOM",
    "BYN": "BYNRUB_TOM",
    # Precious metals priced as "currency" by T-Invest.
    # GLDRUB_TOM = 1 gram of gold in RUB on MOEX. If totals look off by ~31.1x for
    # XAU-priced positions, the instrument quantity is in troy ounces, not grams —
    # multiply this rate by 31.1035 to convert.
    "XAU": "GLDRUB_TOM",
    "XAG": "SLVRUB_TOM",
}

# T-Invest BondType enum -> our filter token
BOND_TYPE_NAME = {
    "BOND_TYPE_REPLACED": "replaced",
    "BOND_TYPE_UNSPECIFIED": "unspecified",
}


@dataclass(frozen=True)
class Instrument:
    ticker: str
    figi: str
    lot: int
    currency: str
    name: str
    class_code: str = ""
    instrument_type: str = ""       # "bond" | "share" | "etf" | ...
    # Bond-only metadata (None for non-bonds):
    bond_type: str | None = None    # our token: "replaced" | "unspecified"
    nominal_currency: str | None = None  # lowercase: "rub", "usd"
    maturity_date: datetime | None = None


@dataclass(frozen=True)
class CashBalance:
    currency: str
    amount: Decimal


@dataclass(frozen=True)
class RawPosition:
    figi: str
    quantity: Decimal
    current_price: Decimal
    currency: str
    instrument_type: str


@dataclass(frozen=True)
class PortfolioSnapshot:
    positions: list[RawPosition]
    cash: list[CashBalance]
    fetched_at: datetime


# Process-wide cache: instrument metadata is effectively static during the bot's lifetime.
_INSTRUMENT_CACHE: dict[str, Instrument] = {}


async def resolve_instruments(
    client: AsyncClient, tickers: list[str]
) -> dict[str, Instrument]:
    """Resolve a list of tickers to enriched Instrument records (lot, currency, bond meta)."""
    resolved: dict[str, Instrument] = {}
    for ticker in tickers:
        figi = await _resolve_figi(client, ticker)
        inst = await fetch_instrument_by_figi(client, figi, override_ticker=ticker)
        resolved[ticker] = inst
    return resolved


async def fetch_instruments_by_figi(
    client: AsyncClient, figis: list[str]
) -> dict[str, Instrument]:
    """Resolve a list of FIGIs to enriched Instrument records."""
    result: dict[str, Instrument] = {}
    for figi in figis:
        result[figi] = await fetch_instrument_by_figi(client, figi)
    return result


async def fetch_instrument_by_figi(
    client: AsyncClient, figi: str, override_ticker: str | None = None
) -> Instrument:
    cache_key = figi if override_ticker is None else f"{figi}|{override_ticker}"
    cached = _INSTRUMENT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    info = await client.instruments.get_instrument_by(
        id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=figi
    )
    base = info.instrument

    bond_type: str | None = None
    nominal_currency: str | None = None
    maturity_date: datetime | None = None
    if base.instrument_type == "bond":
        try:
            bond_resp = await client.instruments.bond_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=figi
            )
            bond = bond_resp.instrument
            bond_type_raw = getattr(bond.bond_type, "name", "") or ""
            bond_type = BOND_TYPE_NAME.get(bond_type_raw, bond_type_raw.lower())
            nominal_currency = (bond.nominal.currency or "").lower() if bond.nominal else None
            maturity_date = bond.maturity_date
        except Exception:
            # Bond metadata is best-effort; if unavailable, fall back to generic fields.
            pass

    inst = Instrument(
        ticker=(override_ticker or base.ticker or "").upper(),
        figi=base.figi,
        lot=base.lot,
        currency=(base.currency or "").upper(),
        name=base.name,
        class_code=(base.class_code or "").upper(),
        instrument_type=base.instrument_type or "",
        bond_type=bond_type,
        nominal_currency=nominal_currency,
        maturity_date=maturity_date,
    )
    _INSTRUMENT_CACHE[cache_key] = inst
    return inst


async def _resolve_figi(client: AsyncClient, ticker: str) -> str:
    response = await client.instruments.find_instrument(query=ticker)
    matches = [
        i for i in response.instruments
        if i.ticker.upper() == ticker.upper() and i.class_code in MOEX_CLASS_CODES
    ]
    if not matches:
        broader = [i for i in response.instruments if i.ticker.upper() == ticker.upper()]
        if not broader:
            raise RuntimeError(f"Ticker {ticker!r} not found in T-Invest catalog")
        raise RuntimeError(
            f"Ticker {ticker!r} found but not on MOEX boards "
            f"(found classes: {sorted({i.class_code for i in broader})})"
        )
    return matches[0].figi


async def fetch_portfolio(client: AsyncClient, account_id: str) -> PortfolioSnapshot:
    portfolio = await client.operations.get_portfolio(account_id=account_id)
    positions_response = await client.operations.get_positions(account_id=account_id)
    fetched_at = datetime.now(timezone.utc)

    positions: list[RawPosition] = []
    for p in portfolio.positions:
        if p.instrument_type == "currency":
            continue  # cash is handled separately via get_positions
        positions.append(
            RawPosition(
                figi=p.figi,
                quantity=_quotation_or_zero(p.quantity),
                current_price=_money_or_quotation(p.current_price),
                currency=_money_currency(p.current_price),
                instrument_type=p.instrument_type or "",
            )
        )

    cash: list[CashBalance] = []
    for mv in positions_response.money:
        currency = (mv.currency or "").upper()
        if not currency:
            continue
        cash.append(CashBalance(currency=currency, amount=money_to_decimal(mv)))

    return PortfolioSnapshot(positions=positions, cash=cash, fetched_at=fetched_at)


async def fetch_last_prices(
    client: AsyncClient, figis: list[str]
) -> dict[str, Decimal]:
    if not figis:
        return {}
    response = await client.market_data.get_last_prices(figi=figis)
    return {lp.figi: quotation_to_decimal(lp.price) for lp in response.last_prices}


async def fetch_fx_rates(
    client: AsyncClient, currencies: list[str], base_currency: str
) -> dict[str, Decimal]:
    """Return rate for converting 1 unit of <currency> into <base_currency>."""
    rates: dict[str, Decimal] = {base_currency: Decimal(1)}
    needed = [c for c in currencies if c != base_currency]
    if not needed:
        return rates

    for cur in needed:
        ticker = FX_TICKER_BY_CURRENCY.get(cur)
        if ticker is None:
            raise RuntimeError(
                f"No FX ticker mapping for {cur!r}; add it to FX_TICKER_BY_CURRENCY"
            )
        figi = await _resolve_figi(client, ticker)
        prices = await fetch_last_prices(client, [figi])
        rate = prices.get(figi)
        if rate is None or rate <= 0:
            raise RuntimeError(f"FX rate for {cur} unavailable")
        rates[cur] = rate
    return rates


def _quotation_or_zero(q: Quotation | None) -> Decimal:
    if q is None:
        return Decimal(0)
    return quotation_to_decimal(q)


def _money_or_quotation(m: MoneyValue | Quotation | None) -> Decimal:
    if m is None:
        return Decimal(0)
    if isinstance(m, MoneyValue):
        return money_to_decimal(m)
    return quotation_to_decimal(m)


def _money_currency(m: MoneyValue | None) -> str:
    if m is None:
        return ""
    return (m.currency or "").upper()
