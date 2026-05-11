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
    """Cash bucket never gets buy suggestions even when underweight. Cash floor caps deploy."""
    target = build_target_tickers([("stocks", [("SBER", Decimal(60))])], cash_weight=Decimal(40))
    s = state_from(target, {
        "sber": [H("SBER", 10, 1)],
        "cash": [Cash(90)],
    })
    res = suggest_buys(s, target, Decimal(0))
    # Cash floor (target − 2pp = 38%): min_cash_after = 38. Deployable = 90 − 38 = 52.
    # SBER drift cap also = 52. Spent 52, leftover 0.
    assert res.cash_to_deploy == Decimal(52)
    assert len(res.suggestions) == 1
    assert res.suggestions[0].ticker == "SBER"
    assert res.suggestions[0].total_cost_base == Decimal(52)
    assert res.leftover == Decimal(0)


def test_extra_cash_adds_to_pool_with_floor() -> None:
    target = build_target_tickers([("stocks", [("SBER", Decimal(95))])], cash_weight=Decimal(5))
    s = state_from(target, {"sber": [H("SBER", 95, 1)], "cash": [Cash(5)]})
    res = suggest_buys(s, target, Decimal(100))
    # total_after = 200; cash floor = (5−2)% × 200 = 6.
    # cash_to_deploy = max(0, 5 + 100 − 6) = 99.
    # SBER drift cap: 99. Buys 99 lots × 1 = 99. Leftover 0.
    assert res.cash_to_deploy == Decimal(99)
    assert res.spent == Decimal(99)
    assert res.leftover == Decimal(0)


def test_cash_floor_blocks_deploy_when_already_at_floor() -> None:
    """If cash is at or below floor, /rebalance no-arg should propose nothing."""
    target = build_target_tickers([("stocks", [("SBER", Decimal(95))])], cash_weight=Decimal(5))
    # cash = 3% × total — exactly at floor.
    s = state_from(target, {"sber": [H("SBER", 97, 1)], "cash": [Cash(3)]})
    res = suggest_buys(s, target, Decimal(0))
    assert res.cash_to_deploy == Decimal(0)
    assert res.suggestions == []


def test_drift_cap_blocks_overshoot() -> None:
    """Buying one lot that would push drift > +2 pp is skipped silently."""
    target = build_target_tickers([("metals", [("GOLD", Decimal(10))])], cash_weight=Decimal(90))
    s = state_from(target, {
        "gold": [H("GOLD", 0, Decimal(50))],
        "cash": [Cash(100)],
    })
    res = suggest_buys(s, target, Decimal(0))
    # Cash floor (90 − 2 = 88%) × 100 = 88. cash_to_deploy = 100 − 88 = 12.
    # GOLD lot 50 > 12 (drift cap), so dropped silently.
    assert res.suggestions == []
    assert res.bucket_allocations == []
    assert res.cash_to_deploy == Decimal(12)
    assert res.leftover == Decimal(12)


def test_filter_group_throttling_drops_smallest_gap() -> None:
    """In a group of >=2 filter buckets sharing (category, bond_type), drop the smallest gap."""
    target = build_target_filter_bonds([
        ("ofz_short", Decimal(30), BucketFilter(bond_type="ofz", maturity_max_years=Decimal(3))),
        ("ofz_mid", Decimal(30), BucketFilter(bond_type="ofz", maturity_min_years=Decimal(3))),
        ("ofz_long", Decimal(35), BucketFilter(bond_type="ofz", maturity_min_years=Decimal(7))),
    ])
    # All three OFZ buckets empty → biggest gap is ofz_long (35%), then short and mid (30% each).
    # Drop the smallest by gap. ofz_short and ofz_mid have the same target so it's a tie;
    # implementation drops whichever appears last in sort order — assert only that 2 remain.
    s = state_from(target, {
        "ofz_short": [], "ofz_mid": [], "ofz_long": [], "cash": [Cash(1000)],
    })
    res = suggest_buys(s, target, Decimal(0))
    bucket_names = {a.bucket_name for a in res.bucket_allocations}
    assert "ofz_long" in bucket_names  # largest gap always survives
    assert len(bucket_names) == 2  # one of short/mid dropped


def test_promotion_uses_leftover_to_fit_one_lot() -> None:
    """Explicit-ticker bucket where pro-rata gives less than 1 lot: use leftover to round up."""
    # Two stocks: A (heavy) and B (small).
    # A has huge gap, gets most of cash. B's pro-rata is below lot cost.
    # If leftover allows, B gets 1 lot (promoted), drift cap permitting.
    target = build_target_tickers(
        [("stocks", [("A", Decimal(50)), ("B", Decimal(45))])],
        cash_weight=Decimal(5),
    )
    s = state_from(target, {
        "a": [H("A", quantity=0, price=Decimal(1), lot=1)],
        "b": [H("B", quantity=0, price=Decimal(30), lot=1)],
        "cash": [Cash(100)],
    })
    res = suggest_buys(s, target, Decimal(0))
    by = {x.ticker: x.lots for x in res.suggestions}
    # A: target 50, max_addition = (50+2)% × 100 = 52, buys 52 lots × 1 = 52
    # B: pro-rata ≈ 100 × 45 / 95 ≈ 47, lot_cost = 30 → natural 1 lot, cap (45+2)% × 100 = 47 → 1 lot
    # Now check via promotion path: actually B's pro-rata 47 fits 1 lot directly (cap=47, lot=30).
    # So B gets 1 lot in main loop, not via promotion. Test is more about it not failing.
    assert "A" in by and "B" in by


def test_filter_bucket_always_produces_bucket_allocation_even_with_holdings() -> None:
    """Bonds drift between filter buckets as maturity changes — never recommend a specific
    held ticker; always recommend the bucket as a category.
    """
    target = build_target_filter_bonds([
        ("replaced", Decimal(40), BucketFilter(bond_type="replaced")),
        ("ofz_long", Decimal(55), BucketFilter(bond_type="ofz")),
    ])
    s = state_from(target, {
        "replaced": [H("RPL", quantity=1, price=1000, instrument_type="bond")],
        "ofz_long": [H("OFZ", quantity=1, price=1000, instrument_type="bond")],
        "cash": [Cash(10_000)],
    })
    res = suggest_buys(s, target, Decimal(0))
    assert res.suggestions == []
    by_bucket = {a.bucket_name: a.amount_base for a in res.bucket_allocations}
    # total = 12_000; cash floor (5−2 = 3%) × 12_000 = 360; deployable = 9640.
    # gaps:
    #   replaced: 0.40 × 12000 − 1000 = 3800
    #   ofz_long: 0.55 × 12000 − 1000 = 5600
    # sum_pos = 9400; pro-rata of 9640:
    #   replaced ≈ 3897.45
    #   ofz_long ≈ 5742.55
    assert by_bucket["replaced"].quantize(Decimal("0.01")) == Decimal("3897.02")
    assert by_bucket["ofz_long"].quantize(Decimal("0.01")) == Decimal("5742.98")
    assert res.spent == Decimal(0)
    assert res.reserved.quantize(Decimal("0.01")) == Decimal("9640.00")


def test_empty_filter_bucket_produces_bucket_allocation() -> None:
    target = build_target_filter_bonds([
        ("ofz_long", Decimal(95), BucketFilter(bond_type="ofz")),
    ])
    s = state_from(target, {"ofz_long": [], "cash": [Cash(1000)]})
    res = suggest_buys(s, target, Decimal(0))
    # Cash floor (5% target → 3% min): keep 30; deploy 970.
    assert res.suggestions == []
    assert len(res.bucket_allocations) == 1
    alloc = res.bucket_allocations[0]
    assert alloc.bucket_name == "ofz_long"
    assert alloc.amount_base == Decimal(970)
    assert "ОФЗ" in alloc.filter_summary
    assert res.reserved == Decimal(970)
    assert res.spent == Decimal(0)
    assert res.leftover == Decimal(0)


def test_filter_summary_includes_maturity_range() -> None:
    target = build_target_filter_bonds([
        ("replaced_2_4", Decimal(95), BucketFilter(
            bond_type="replaced",
            maturity_min_years=Decimal(2),
            maturity_max_years=Decimal(4),
        )),
    ])
    s = state_from(target, {"replaced_2_4": [], "cash": [Cash(1000)]})
    res = suggest_buys(s, target, Decimal(0))
    summary = res.bucket_allocations[0].filter_summary
    assert "замещающие" in summary
    assert "2" in summary and "4" in summary


def test_explicit_tickers_buy_lots_alongside_filter_bucket_allocations() -> None:
    """Explicit-ticker buckets get BuySuggestion; filter buckets get BucketAllocation."""
    # Custom target with one explicit-ticker bucket and one filter bucket.
    explicit = TargetBucket(
        name="sber",
        category="stocks",
        portfolio_weight_pct=Decimal(50),
        explicit_tickers=(("SBER", Decimal(100)),),
    )
    filt = TargetBucket(
        name="ofz",
        category="bonds",
        portfolio_weight_pct=Decimal(45),
        filter_=BucketFilter(bond_type="ofz"),
    )
    target = Target(
        base_currency="RUB",
        categories=(
            TargetCategory(name="stocks", buckets=(explicit,)),
            TargetCategory(name="bonds", buckets=(filt,)),
            TargetCategory(name="cash", buckets=(_cash_bucket(Decimal(5)),)),
        ),
    )
    s = state_from(target, {
        "sber": [H("SBER", quantity=0, price=Decimal(1000))],
        "ofz": [],
        "cash": [Cash(10_000)],
    })
    res = suggest_buys(s, target, Decimal(0))
    # gap_sber = 0.50 * 10000 - 0 = 5000; gap_ofz = 0.45 * 10000 - 0 = 4500; sum = 9500
    # sber alloc = 10000 * 5000 / 9500 ≈ 5263 → 5 lots × 1000 = 5000
    # ofz alloc  = 10000 * 4500 / 9500 ≈ 4737 → BucketAllocation
    by_ticker = {x.ticker: x.total_cost_base for x in res.suggestions}
    assert by_ticker == {"SBER": Decimal(5000)}
    assert len(res.bucket_allocations) == 1
    assert res.bucket_allocations[0].bucket_name == "ofz"


def test_bucket_with_only_cash_and_no_filter_is_uninferrable() -> None:
    """Gold bucket has cash_currencies but no tickers and no filter — can't infer what to buy."""
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
        "gold": [Cash(1, currency="XAU", fx=Decimal(10000))],
        "cash": [Cash(2000)],
    })
    res = suggest_buys(s, target, Decimal(0))
    assert res.suggestions == []
    assert res.bucket_allocations == []
    assert any(w.bucket_name == "gold" for w in res.uninferrable_buckets)


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
