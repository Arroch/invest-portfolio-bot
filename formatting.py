from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from html import escape

from models import PortfolioState
from rebalance import (
    CategoryDrift,
    LeafDrift,
    RebalanceResult,
)


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
        for ld in cat.leaves:
            lines.append(_format_leaf_line(ld))
        lines.append("")

    if state.untracked_holdings:
        lines.append("UNTRACKED (not in target.yaml)")
        for h in state.untracked_holdings:
            qty = _money(h.quantity)
            price = _money(h.last_price * h.fx_to_base)
            value = _money(h.value_base)
            lines.append(f"  {h.figi:<14} {qty:>10} @ {price:>10} = {value:>12}")
        lines.append("")

    lines.append(f"Free cash: {_money(state.free_cash_base)} {sign}")
    if len(state.free_cash_breakdown) > 1 or (
        state.free_cash_breakdown and state.base_currency not in state.free_cash_breakdown
    ):
        parts = [f"{_money(amt)} {cur}" for cur, amt in state.free_cash_breakdown.items()]
        lines.append("  (" + ", ".join(parts) + ")")

    lines.append("</pre>")
    return "\n".join(lines)


def _format_category_line(cat: CategoryDrift) -> str:
    name = cat.category.name.upper()
    return (
        f"{name:<8} target {_pct(cat.target_pct):>5}   "
        f"current {_pct(cat.current_pct):>5}   {_pp(cat.drift_pp):>6}"
    )


def _format_leaf_line(ld: LeafDrift) -> str:
    h = ld.holding
    units = _money(h.quantity, 0) if h.quantity == h.quantity.to_integral_value() else _money(h.quantity)
    lots = int(h.quantity / Decimal(h.lot)) if h.lot > 0 else 0
    price = _money(h.last_price)
    return (
        f"  {ld.leaf.ticker:<14} "
        f"target {_pct(ld.target_pct):>5}   "
        f"current {_pct(ld.current_pct):>5}   {_pp(ld.drift_pp):>6}   "
        f"{units} ({lots} lots) @ {price}"
    )


def format_rebalance(result: RebalanceResult, base_currency: str) -> str:
    sign = CURRENCY_SIGN.get(base_currency, base_currency)
    lines: list[str] = []

    if result.used_free_cash:
        lines.append(f"<b>Using free cash: {_money(result.cash_to_deploy)} {sign}</b>")
    else:
        lines.append(f"<b>Allocating: {_money(result.extra_cash)} {sign}</b>")

    if not result.suggestions:
        if result.cash_to_deploy <= 0:
            lines.append("Nothing to deploy.")
        else:
            lines.append("Portfolio already on target — no lots to buy.")
        return "\n".join(lines)

    lines.append("Suggested buys (rounded down to whole lots):")
    lines.append("<pre>")
    for s in result.suggestions:
        line = (
            f"  {s.ticker:<14} buy {s.lots:>3} lots "
            f"× {_money(s.unit_price_base):>10} {sign} "
            f"= {_money(s.total_cost_base):>12} {sign}"
        )
        lines.append(line)
    lines.append("</pre>")
    lines.append(f"Spent: {_money(result.spent)} {sign}    Leftover: {_money(result.leftover)} {sign}")
    return "\n".join(lines)


def format_error(message: str) -> str:
    return f"⚠️ {escape(message)}"


def _money(value: Decimal, places: int = 2) -> str:
    quant = Decimal(10) ** -places if places > 0 else Decimal(1)
    rounded = value.quantize(quant, rounding=ROUND_HALF_UP)
    s = f"{rounded:,.{places}f}".replace(",", " ")
    return s


def _pct(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def _pp(value: Decimal) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)} pp"


def _time(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")
