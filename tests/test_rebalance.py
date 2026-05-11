from datetime import datetime
from decimal import Decimal

from models import Holding, PortfolioState
from rebalance import compute_drift, suggest_buys
from target import TargetCategory, TargetLeaf, Target


def build_target(spec: list[tuple[str, Decimal, list[tuple[str, Decimal]]]]) -> Target:
    """spec = [(category_name, category_weight_pct, [(ticker, ticker_weight_in_cat), ...])]"""
    categories = []
    leaves = []
    for cat_name, cat_w, tickers in spec:
        cat_tickers = {t: w for t, w in tickers}
        categories.append(TargetCategory(name=cat_name, weight_pct=cat_w, tickers=cat_tickers))
        for t, w in tickers:
            leaves.append(
                TargetLeaf(
                    ticker=t,
                    category=cat_name,
                    category_weight_pct=cat_w,
                    ticker_weight_in_cat_pct=w,
                )
            )
    return Target(base_currency="RUB", categories=categories, leaves=leaves)


def H(
    ticker: str,
    quantity: int | str | Decimal,
    price: int | str | Decimal,
    lot: int = 1,
    currency: str = "RUB",
    fx: Decimal = Decimal(1),
) -> Holding:
    return Holding(
        figi=f"FIGI_{ticker}",
        ticker=ticker,
        name=ticker,
        quantity=Decimal(str(quantity)),
        last_price=Decimal(str(price)),
        currency=currency,
        lot=lot,
        fx_to_base=fx,
    )


def state(
    target_holdings: list[Holding],
    free_cash: Decimal = Decimal(0),
    untracked: list[Holding] | None = None,
) -> PortfolioState:
    return PortfolioState(
        target_holdings=target_holdings,
        untracked_holdings=untracked or [],
        free_cash_base=free_cash,
        free_cash_breakdown={"RUB": free_cash} if free_cash > 0 else {},
        base_currency="RUB",
        fetched_at=datetime(2026, 5, 10, 14, 32),
    )


def test_portfolio_at_target_n_zero_no_suggestions() -> None:
    target = build_target([
        ("stocks", Decimal(60), [("SBER", Decimal(100))]),
        ("bonds", Decimal(40), [("OFZ", Decimal(100))]),
    ])
    holdings = [H("SBER", 60, 1), H("OFZ", 40, 1)]
    res = suggest_buys(state(holdings), target, Decimal(0))
    assert res.suggestions == []
    assert res.spent == Decimal(0)
    assert res.leftover == Decimal(0)


def test_portfolio_at_target_with_n_split_proportionally() -> None:
    target = build_target([
        ("stocks", Decimal(60), [("SBER", Decimal(100))]),
        ("bonds", Decimal(40), [("OFZ", Decimal(100))]),
    ])
    holdings = [H("SBER", 60, 1), H("OFZ", 40, 1)]
    res = suggest_buys(state(holdings), target, Decimal(100))
    suggestions = {s.ticker: s.total_cost_base for s in res.suggestions}
    assert suggestions == {"SBER": Decimal(60), "OFZ": Decimal(40)}
    assert res.spent == Decimal(100)
    assert res.leftover == Decimal(0)


def test_missing_ticker_gets_full_allocation_with_lots() -> None:
    target = build_target([
        ("stocks", Decimal(100), [("TGLD", Decimal(100))]),
    ])
    holdings = [H("TGLD", quantity=0, price=Decimal("12.65"), lot=10)]
    res = suggest_buys(state(holdings, free_cash=Decimal(0)), target, Decimal(1000))
    assert len(res.suggestions) == 1
    s = res.suggestions[0]
    assert s.ticker == "TGLD"
    assert s.lots == 7
    assert s.total_cost_base == Decimal("12.65") * Decimal(10) * Decimal(7)
    assert res.spent == s.total_cost_base
    assert res.leftover == Decimal(1000) - s.total_cost_base


def test_pro_rata_when_gaps_exceed_cash() -> None:
    target = build_target([
        ("stocks", Decimal(50), [("A", Decimal(100))]),
        ("bonds", Decimal(50), [("B", Decimal(100))]),
    ])
    holdings = [H("A", 0, 1), H("B", 0, 1)]
    res = suggest_buys(state(holdings, free_cash=Decimal(0)), target, Decimal(100))
    by = {s.ticker: s.total_cost_base for s in res.suggestions}
    assert by["A"] == Decimal(50)
    assert by["B"] == Decimal(50)
    assert res.spent == Decimal(100)


def test_lot_rounding_leftover() -> None:
    target = build_target([
        ("stocks", Decimal(100), [("SBER", Decimal(100))]),
    ])
    holdings = [H("SBER", quantity=0, price=Decimal("305.40"), lot=10)]
    res = suggest_buys(state(holdings), target, Decimal(5000))
    assert len(res.suggestions) == 1
    s = res.suggestions[0]
    assert s.lots == 1
    assert s.total_cost_base == Decimal("3054.00")
    assert res.leftover == Decimal(5000) - Decimal("3054.00")


def test_untracked_position_ignored_in_buy_math() -> None:
    target = build_target([
        ("stocks", Decimal(100), [("SBER", Decimal(100))]),
    ])
    sber = H("SBER", 0, 1)
    weird = H("MOEX", 100, 1)
    res = suggest_buys(state([sber], untracked=[weird]), target, Decimal(100))
    assert len(res.suggestions) == 1
    assert res.suggestions[0].ticker == "SBER"


def test_rebalance_with_no_arg_deploys_free_cash() -> None:
    target = build_target([
        ("stocks", Decimal(50), [("A", Decimal(100))]),
        ("bonds", Decimal(50), [("B", Decimal(100))]),
    ])
    holdings = [H("A", 100, 1), H("B", 100, 1)]
    res = suggest_buys(state(holdings, free_cash=Decimal(50)), target, Decimal(0))
    assert res.used_free_cash is True
    assert res.cash_to_deploy == Decimal(50)
    by = {s.ticker: s.total_cost_base for s in res.suggestions}
    assert by == {"A": Decimal(25), "B": Decimal(25)}


def test_rebalance_with_no_cash_returns_empty() -> None:
    target = build_target([
        ("stocks", Decimal(100), [("A", Decimal(100))]),
    ])
    holdings = [H("A", 100, 1)]
    res = suggest_buys(state(holdings, free_cash=Decimal(0)), target, Decimal(0))
    assert res.suggestions == []
    assert res.spent == Decimal(0)


def test_compute_drift_shows_overweight_and_underweight() -> None:
    target = build_target([
        ("stocks", Decimal(60), [("SBER", Decimal(100))]),
        ("bonds", Decimal(40), [("OFZ", Decimal(100))]),
    ])
    holdings = [H("SBER", 80, 1), H("OFZ", 20, 1)]
    cats, leaves = compute_drift(state(holdings), target)
    sber = next(l for l in leaves if l.leaf.ticker == "SBER")
    ofz = next(l for l in leaves if l.leaf.ticker == "OFZ")
    assert sber.current_pct == Decimal(80)
    assert sber.drift_pp == Decimal(20)
    assert ofz.current_pct == Decimal(20)
    assert ofz.drift_pp == Decimal(-20)
    stocks_cat = next(c for c in cats if c.category.name == "stocks")
    assert stocks_cat.current_pct == Decimal(80)
    assert stocks_cat.drift_pp == Decimal(20)
