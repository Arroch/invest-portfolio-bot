from datetime import datetime
from decimal import Decimal

from models import BucketState, Holding, PortfolioState
from rebalance import compute_drift, suggest_buys
from target import BucketFilter, Target, TargetBucket, TargetCategory


def _cash_bucket(weight: Decimal = Decimal(5)) -> TargetBucket:
    return TargetBucket(
        name="cash",
        category="cash",
        portfolio_weight_pct=weight,
        is_cash=True,
    )


def build_target_tickers(
    spec: list[tuple[str, list[tuple[str, Decimal]]]],
    cash_weight: Decimal = Decimal(0),
) -> Target:
    """spec = [(bucket_name, [(ticker, abs_weight_pct)])] — but flat single-ticker buckets.

    Each tuple here becomes one bucket per ticker for simplicity. Total weight (incl. cash) = 100.
    """
    buckets = []
    for cat_name, entries in spec:
        cat_buckets = []
        for ticker, weight in entries:
            cat_buckets.append(
                TargetBucket(
                    name=ticker.lower(),
                    category=cat_name,
                    portfolio_weight_pct=weight,
                    explicit_tickers=((ticker, Decimal(100)),),
                )
            )
        buckets.append(TargetCategory(name=cat_name, buckets=tuple(cat_buckets)))
    if cash_weight > 0:
        buckets.append(
            TargetCategory(name="cash", buckets=(_cash_bucket(cash_weight),))
        )
    else:
        buckets.append(TargetCategory(name="cash", buckets=(_cash_bucket(Decimal(0)),)))
    return Target(base_currency="RUB", categories=tuple(buckets))


def build_target_filter_bonds(
    weights_and_filters: list[tuple[str, Decimal, BucketFilter]],
) -> Target:
    buckets = tuple(
        TargetBucket(
            name=name,
            category="bonds",
            portfolio_weight_pct=w,
            filter_=flt,
        )
        for name, w, flt in weights_and_filters
    )
    total = sum(w for _, w, _ in weights_and_filters)
    cash_weight = Decimal(100) - total
    cat_bonds = TargetCategory(name="bonds", buckets=buckets)
    cat_cash = TargetCategory(name="cash", buckets=(_cash_bucket(cash_weight),))
    return Target(base_currency="RUB", categories=(cat_bonds, cat_cash))


def H(
    ticker: str,
    quantity: int | str | Decimal,
    price: int | str | Decimal,
    lot: int = 1,
    currency: str = "RUB",
    fx: Decimal = Decimal(1),
    instrument_type: str = "share",
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
        instrument_type=instrument_type,
    )


def Cash(amount: int | str | Decimal, currency: str = "RUB", fx: Decimal = Decimal(1)) -> Holding:
    return Holding(
        figi="",
        ticker=currency,
        name=f"{currency} cash",
        quantity=Decimal(str(amount)),
        last_price=Decimal(1),
        currency=currency,
        lot=1,
        fx_to_base=fx,
        instrument_type="currency",
    )


def state_from(
    target: Target,
    bucket_holdings: dict[str, list[Holding]],
    untracked: list[Holding] | None = None,
) -> PortfolioState:
    bucket_states = tuple(
        BucketState(bucket=b, holdings=tuple(bucket_holdings.get(b.name, [])))
        for b in target.buckets
    )
    return PortfolioState(
        buckets=bucket_states,
        untracked_holdings=tuple(untracked or []),
        free_cash_breakdown={},
        base_currency="RUB",
        fetched_at=datetime(2026, 5, 11, 14, 32),
    )


def test_portfolio_at_target_n_zero_no_suggestions() -> None:
    target = build_target_tickers(
        [("stocks", [("SBER", Decimal(60))]), ("bonds", [("OFZ", Decimal(40))])],
    )
    s = state_from(target, {"sber": [H("SBER", 60, 1)], "ofz": [H("OFZ", 40, 1)]})
    res = suggest_buys(s, target, Decimal(0))
    assert res.suggestions == []
    assert res.spent == Decimal(0)


def test_cash_excluded_from_destinations() -> None:
    """Cash bucket never gets buy suggestions even when underweight."""
    target = build_target_tickers([("stocks", [("SBER", Decimal(60))])], cash_weight=Decimal(40))
    # Stocks underweight, cash overweight
    s = state_from(target, {
        "sber": [H("SBER", 10, 1)],   # 10
        "cash": [Cash(90)],            # 90
    })
    res = suggest_buys(s, target, Decimal(0))
    # All cash deployed to SBER (only destination)
    assert len(res.suggestions) == 1
    assert res.suggestions[0].ticker == "SBER"
    assert res.suggestions[0].total_cost_base == Decimal(90)


def test_extra_cash_adds_to_pool() -> None:
    target = build_target_tickers([("stocks", [("SBER", Decimal(95))])], cash_weight=Decimal(5))
    s = state_from(target, {"sber": [H("SBER", 95, 1)], "cash": [Cash(5)]})
    res = suggest_buys(s, target, Decimal(100))
    # cash_to_deploy = 5 (cash pool) + 100 (extra) = 105
    # gap_SBER = 0.95 × (100+100) - 95 = 95
    # only positive gap, all 105 goes to SBER
    assert res.cash_to_deploy == Decimal(105)
    assert res.suggestions[0].ticker == "SBER"
    assert res.spent == Decimal(105)


def test_filter_bucket_with_one_holding() -> None:
    target = build_target_filter_bonds([
        ("replaced", Decimal(40), BucketFilter(bond_type="replaced")),
        ("ofz_long", Decimal(55), BucketFilter(bond_type="ofz")),
    ])
    s = state_from(target, {
        "replaced": [H("RPL", 0, 1000, instrument_type="bond")],
        "ofz_long": [H("OFZ", 0, 1000, instrument_type="bond")],
        "cash": [Cash(10_000)],
    })
    res = suggest_buys(s, target, Decimal(0))
    by = {x.ticker: x.total_cost_base for x in res.suggestions}
    # gap_replaced = 0.40 × 10000 - 0 = 4000
    # gap_ofz     = 0.55 × 10000 - 0 = 5500
    # gap_cash    excluded
    # sum_pos = 9500; cash = 10_000; pro-rata: replaced gets 10000*4000/9500, ofz gets 10000*5500/9500
    # but in unit prices = 1000 with lot=1, replaced alloc ≈ 4210.5 → 4 lots = 4000
    # ofz alloc ≈ 5789.5 → 5 lots = 5000
    assert by["RPL"] == Decimal(4000)
    assert by["OFZ"] == Decimal(5000)


def test_filter_bucket_distributes_pro_rata_to_held_value() -> None:
    target = build_target_filter_bonds([
        ("ofz_long", Decimal(95), BucketFilter(bond_type="ofz")),
    ])
    a = H("OFZ_A", quantity=60, price=1, instrument_type="bond")
    b = H("OFZ_B", quantity=40, price=1, instrument_type="bond")
    s = state_from(target, {"ofz_long": [a, b], "cash": [Cash(100)]})
    res = suggest_buys(s, target, Decimal(0))
    by = {x.ticker: x.total_cost_base for x in res.suggestions}
    # gap_ofz = 0.95 × 200 - 100 = 90; cash_to_deploy = 100; pro-rata to held = 60/40
    # alloc to ofz_long = 100 * 90 / 90 = 100; split A:60, B:40
    assert by == {"OFZ_A": Decimal(60), "OFZ_B": Decimal(40)}


def test_empty_filter_bucket_reported() -> None:
    target = build_target_filter_bonds([
        ("ofz_long", Decimal(95), BucketFilter(bond_type="ofz")),
    ])
    s = state_from(target, {"ofz_long": [], "cash": [Cash(1000)]})
    res = suggest_buys(s, target, Decimal(0))
    assert res.suggestions == []
    assert len(res.empty_underweight_buckets) == 1
    assert res.empty_underweight_buckets[0].bucket_name == "ofz_long"
    # All cash stays as leftover
    assert res.leftover == Decimal(1000)


def test_bucket_with_only_cash_holding_is_not_buyable() -> None:
    """If gold bucket only contains XAU cash and no TGLD, /rebalance can't buy gold."""
    gold_bucket = TargetBucket(
        name="gold",
        category="gold",
        portfolio_weight_pct=Decimal(95),
        cash_currencies=("XAU",),
    )
    cash = _cash_bucket(Decimal(5))
    target = Target(
        base_currency="RUB",
        categories=(
            TargetCategory(name="gold", buckets=(gold_bucket,)),
            TargetCategory(name="cash", buckets=(cash,)),
        ),
    )
    s = state_from(target, {
        "gold": [Cash(1, currency="XAU", fx=Decimal(10000))],   # value 10_000
        "cash": [Cash(2000)],
    })
    res = suggest_buys(s, target, Decimal(0))
    # Gold underweight (10000/12000 ≈ 83.3% vs 95% target).
    # But no buyable holding → empty bucket warning, no suggestion.
    assert res.suggestions == []
    assert any(w.bucket_name == "gold" for w in res.empty_underweight_buckets)


def test_compute_drift_includes_cash_bucket() -> None:
    target = build_target_tickers([("stocks", [("SBER", Decimal(60))])], cash_weight=Decimal(40))
    s = state_from(target, {"sber": [H("SBER", 50, 1)], "cash": [Cash(50)]})
    cats, buckets = compute_drift(s, target)
    sber = next(b for b in buckets if b.bucket_state.bucket.name == "sber")
    cash_d = next(b for b in buckets if b.bucket_state.bucket.is_cash)
    assert sber.current_pct == Decimal(50)
    assert sber.drift_pp == Decimal(-10)
    assert cash_d.current_pct == Decimal(50)
    assert cash_d.drift_pp == Decimal(10)
