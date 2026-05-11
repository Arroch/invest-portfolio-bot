from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from html import escape

from models import Holding, PortfolioState
from rebalance import BucketDrift, CategoryDrift, RebalanceResult

CURRENCY_SIGN = {"RUB": "₽", "USD": "$", "EUR": "€"}


def format_portfolio(
    state: PortfolioState, cat_drifts: list[CategoryDrift]
) -> str:
    sign = CURRENCY_SIGN.get(state.base_currency, state.base_currency)
    lines: list[str] = []
    lines.append(f"<b>Portfolio: {_money(state.total_base)} {sign}</b>")
    lines.append(f"<i>as of {_time(state.fetched_at)}</i>")
    lines.append("")
    lines.append("<pre>")

    for cat in cat_drifts:
        lines.append(_format_category_line(cat))
        for bd in cat.buckets:
            lines.append(_format_bucket_line(bd))
            non_cash = [h for h in bd.bucket_state.holdings if not h.is_cash]
            cash_h = [h for h in bd.bucket_state.holdings if h.is_cash]
            for h in non_cash:
                lines.append(_format_instrument_line(h))
            for h in cash_h:
                lines.append(_format_cash_line(h, state.base_currency))
            if not bd.bucket_state.holdings:
                lines.append("      (empty)")
        lines.append("")

    if state.untracked_holdings:
        lines.append("UNTRACKED (didn't match any bucket)")
        for h in state.untracked_holdings:
            if h.is_cash:
                lines.append(_format_cash_line(h, state.base_currency, indent="  "))
            else:
                lines.append(_format_instrument_line(h, indent="  "))
        lines.append("")

    lines.append("</pre>")
    return "\n".join(lines)


def _format_category_line(cat: CategoryDrift) -> str:
    name = cat.category.name.upper()
    return (
        f"{name:<10} target {_pct(cat.target_pct):>6}   "
        f"current {_pct(cat.current_pct):>6}   {_pp(cat.drift_pp):>8}"
    )


def _format_bucket_line(bd: BucketDrift) -> str:
    name = bd.bucket_state.bucket.name
    return (
        f"  {name:<18} target {_pct(bd.target_pct):>6}   "
        f"current {_pct(bd.current_pct):>6}   {_pp(bd.drift_pp):>8}"
    )


def _format_instrument_line(h: Holding, indent: str = "      ") -> str:
    qty = (
        _money(h.quantity, 0)
        if h.quantity == h.quantity.to_integral_value()
        else _money(h.quantity, 4)
    )
    lots = int(h.quantity / Decimal(h.lot)) if h.lot > 0 else 0
    price = _money(h.last_price)
    cur = h.currency or ""
    return (
        f"{indent}{h.ticker:<14} {qty:>10} ({lots} lots) @ {price:>10} {cur:<3}  "
        f"= {_money(h.value_base):>12}"
    )


def _format_cash_line(h: Holding, base_currency: str, indent: str = "      ") -> str:
    qty = _money(h.quantity)
    cur = h.currency or ""
    in_base = (
        f"  = {_money(h.value_base)} {base_currency}" if cur != base_currency else ""
    )
    return f"{indent}{('cash ' + cur):<14} {qty:>10} {cur}{in_base}"


def format_rebalance(result: RebalanceResult, base_currency: str) -> str:
    sign = CURRENCY_SIGN.get(base_currency, base_currency)
    lines: list[str] = []

    if result.used_free_cash:
        lines.append(f"<b>Using cash pool: {_money(result.cash_to_deploy)} {sign}</b>")
    else:
        lines.append(
            f"<b>Allocating: {_money(result.extra_cash)} {sign} + cash pool "
            f"{_money(result.cash_to_deploy - result.extra_cash)} {sign} = "
            f"{_money(result.cash_to_deploy)} {sign}</b>"
        )

    if not result.suggestions:
        if result.cash_to_deploy <= 0:
            lines.append("Nothing to deploy.")
        else:
            lines.append("No underweight bucket has a buyable holding — see warnings below.")
    else:
        lines.append("Suggested buys (rounded down to whole lots):")
        lines.append("<pre>")
        for s in result.suggestions:
            line = (
                f"  [{s.bucket_name:<14}] {s.ticker:<14} buy {s.lots:>3} lots "
                f"× {_money(s.unit_price_base):>10} {sign} "
                f"= {_money(s.total_cost_base):>12} {sign}"
            )
            lines.append(line)
        lines.append("</pre>")
        lines.append(
            f"Spent: {_money(result.spent)} {sign}    Leftover: {_money(result.leftover)} {sign}"
        )

    if result.empty_underweight_buckets:
        lines.append("")
        lines.append("<b>⚠ Empty underweight buckets (no matching holding to buy):</b>")
        for w in result.empty_underweight_buckets:
            lines.append(
                f"  [{w.category}/{w.bucket_name}] target {_pct(w.target_pct)} — "
                f"gap {_money(w.gap_base)} {sign}. Add a matching ticker manually."
            )

    return "\n".join(lines)


def format_error(message: str) -> str:
    return f"⚠️ {escape(message)}"


def _money(value: Decimal, places: int = 2) -> str:
    quant = Decimal(10) ** -places if places > 0 else Decimal(1)
    rounded = value.quantize(quant, rounding=ROUND_HALF_UP)
    s = f"{rounded:,.{places}f}".replace(",", " ")
    return s


def _pct(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%"


def _pp(value: Decimal) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)} pp"


def _time(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")
