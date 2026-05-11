from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from html import escape

from models import Holding, PortfolioState
from rebalance import BucketDrift, CategoryDrift, RebalanceResult

CURRENCY_SIGN = {"RUB": "₽", "USD": "$", "EUR": "€"}

CATEGORY_EMOJI = {
    "stocks": "📊",
    "bonds": "📈",
    "gold": "🪙",
    "cash": "💰",
}


# ───────────────────────────── /portfolio ─────────────────────────────


def format_portfolio(state: PortfolioState, cat_drifts: list[CategoryDrift]) -> str:
    sign = CURRENCY_SIGN.get(state.base_currency, state.base_currency)
    lines: list[str] = []
    lines.append(f"💼 <b>{_money(state.total_base)} {sign}</b>")
    lines.append(f"<i>на {_time(state.fetched_at)}</i>")

    for cat in cat_drifts:
        lines.append("")
        emoji = CATEGORY_EMOJI.get(cat.category.name.lower(), "•")
        lines.append(
            f"{emoji} <b>{cat.category.name.upper()}</b>  "
            f"<i>{_pct(cat.current_pct)} / {_pct(cat.target_pct)}</i>  {_pp(cat.drift_pp)}"
        )
        for bd in cat.buckets:
            lines.extend(_format_bucket_block(bd, sign))

    return "\n".join(lines)


def _format_bucket_block(bd: BucketDrift, sign: str) -> list[str]:
    lines: list[str] = []
    lines.append(
        f"  <b>{escape(bd.bucket_state.bucket.name)}</b>  "
        f"<i>{_pct(bd.current_pct)} / {_pct(bd.target_pct)}</i>  {_pp(bd.drift_pp)}"
    )
    non_cash = [h for h in bd.bucket_state.holdings if not h.is_cash]
    cash_h = [h for h in bd.bucket_state.holdings if h.is_cash]
    if not bd.bucket_state.holdings:
        lines.append("    <i>пусто</i>")
        return lines
    for h in non_cash:
        if h.quantity == 0:
            lines.append(f"    {escape(h.ticker)} — <i>не куплено</i>")
        else:
            lines.append(
                f"    {escape(h.ticker)} × {_qty(h.quantity)} = {_money(h.value_base)} {sign}"
            )
    for h in cash_h:
        cur = h.currency
        if cur == "RUB":
            lines.append(f"    {_money(h.quantity)} ₽")
        else:
            lines.append(
                f"    {_money(h.quantity, 2)} {cur} = {_money(h.value_base)} {sign}"
            )
    return lines


# ───────────────────────────── /untracked ─────────────────────────────


def format_untracked(state: PortfolioState) -> str:
    sign = CURRENCY_SIGN.get(state.base_currency, state.base_currency)
    if not state.untracked_holdings:
        return "🗑 <b>Untracked</b>\n<i>Нет позиций вне target.</i>"
    total = sum((h.value_base for h in state.untracked_holdings), Decimal(0))
    lines = [f"🗑 <b>Untracked</b>  <i>всего {_money(total)} {sign}</i>", ""]
    for h in sorted(state.untracked_holdings, key=lambda x: -x.value_base):
        if h.is_cash:
            lines.append(f"{h.currency} {_money(h.quantity)} = {_money(h.value_base)} {sign}")
        else:
            ticker = h.ticker if h.ticker else h.figi
            lines.append(
                f"{escape(ticker)} × {_qty(h.quantity)} = {_money(h.value_base)} {sign}"
            )
    return "\n".join(lines)


# ───────────────────────────── /rebalance ─────────────────────────────


def format_rebalance(result: RebalanceResult, base_currency: str) -> str:
    sign = CURRENCY_SIGN.get(base_currency, base_currency)
    lines: list[str] = []
    lines.append("💸 <b>План распределения</b>")
    if result.used_free_cash:
        lines.append(f"<i>Из {_money(result.cash_to_deploy)} {sign} свободных средств</i>")
    else:
        pool_part = result.cash_to_deploy - result.extra_cash
        if pool_part > 0:
            lines.append(
                f"<i>{_money(result.extra_cash)} {sign} + cash pool {_money(pool_part)} {sign} = "
                f"{_money(result.cash_to_deploy)} {sign}</i>"
            )
        else:
            lines.append(f"<i>{_money(result.extra_cash)} {sign} новых средств</i>")

    if not result.suggestions and not result.bucket_allocations:
        lines.append("")
        lines.append("<i>Нечего предлагать — портфель в пределах допусков.</i>")
        return "\n".join(lines + _rebalance_warnings(result, sign))

    if result.suggestions:
        lines.append("")
        for s in result.suggestions:
            lines.append(
                f"<b>{escape(s.ticker)}</b> × {s.lots} = <b>{_money(s.total_cost_base)} {sign}</b>  "
                f"<i>({escape(s.bucket_name)})</i>"
            )

    if result.bucket_allocations:
        lines.append("")
        lines.append("<b>Резервы по категориям</b> <i>(тикер выбираешь сам)</i>")
        for a in result.bucket_allocations:
            lines.append(
                f"<b>{escape(a.bucket_name)}</b> ≈ <b>{_money(a.amount_base)} {sign}</b>  "
                f"<i>{escape(a.filter_summary)}</i>"
            )

    lines.append("")
    summary_parts = [f"💵 Потратить: <b>{_money(result.spent)} {sign}</b>"]
    if result.reserved > 0:
        summary_parts.append(f"📌 Резерв: <b>{_money(result.reserved)} {sign}</b>")
    if result.leftover > 0:
        summary_parts.append(f"💰 Остаток: <b>{_money(result.leftover)} {sign}</b>")
    lines.extend(summary_parts)

    lines.extend(_rebalance_warnings(result, sign))
    return "\n".join(lines)


def _rebalance_warnings(result: RebalanceResult, sign: str) -> list[str]:
    if not result.uninferrable_buckets:
        return []
    lines = ["", "⚠️ <b>Не знаю что предложить:</b>"]
    for w in result.uninferrable_buckets:
        lines.append(
            f"  {escape(w.category)}/{escape(w.bucket_name)} — target {_pct(w.target_pct)}, "
            f"добавь тикер или filter"
        )
    return lines


# ───────────────────────────── misc ─────────────────────────────


def format_error(message: str) -> str:
    return f"⚠️ {escape(message)}"


def _money(value: Decimal, places: int = 2) -> str:
    quant = Decimal(10) ** -places if places > 0 else Decimal(1)
    rounded = value.quantize(quant, rounding=ROUND_HALF_UP)
    s = f"{rounded:,.{places}f}".replace(",", " ")
    return s


def _qty(value: Decimal) -> str:
    if value == value.to_integral_value():
        return _money(value, 0)
    return _money(value, 4).rstrip("0").rstrip(".") or "0"


def _pct(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def _pp(value: Decimal) -> str:
    if value == 0:
        return ""
    sign = "+" if value > 0 else "−"
    abs_v = abs(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"<i>({sign}{abs_v} pp)</i>"


def _time(dt: datetime) -> str:
    return dt.strftime("%d.%m %H:%M")
