from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from models import BucketState, Holding, PortfolioState
from target import BucketFilter, Target, TargetBucket, TargetCategory

DRIFT_CAP_PP = Decimal(2)  # don't suggest a lot if it pushes bucket drift > +N pp


@dataclass(frozen=True)
class BucketDrift:
    bucket_state: BucketState
    current_value: Decimal
    target_value: Decimal
    current_pct: Decimal
    target_pct: Decimal
    drift_pp: Decimal


@dataclass(frozen=True)
class CategoryDrift:
    category: TargetCategory
    current_value: Decimal
    current_pct: Decimal
    target_pct: Decimal
    drift_pp: Decimal
    buckets: list[BucketDrift] = field(default_factory=list)


@dataclass(frozen=True)
class BuySuggestion:
    """Per-ticker recommendation with concrete lot count."""

    ticker: str
    name: str
    bucket_name: str
    lots: int
    units: Decimal
    unit_price_base: Decimal
    total_cost_base: Decimal


@dataclass(frozen=True)
class BucketAllocation:
    """Per-bucket recommendation when no specific ticker is held / configured.

    User picks any instrument matching the bucket's filter and puts `amount_base` into it.
    """

    bucket_name: str
    category: str
    filter_summary: str
    amount_base: Decimal


@dataclass(frozen=True)
class UninferrableBucket:
    """Underweight bucket where the bot cannot infer what to buy
    (no holdings, no explicit tickers, no filter)."""

    bucket_name: str
    category: str
    target_pct: Decimal
    gap_base: Decimal


@dataclass(frozen=True)
class RebalanceResult:
    cash_to_deploy: Decimal
    extra_cash: Decimal
    used_free_cash: bool
    spent: Decimal           # sum of concrete BuySuggestion costs
    reserved: Decimal        # sum of BucketAllocation amounts (no lots yet)
    leftover: Decimal
    suggestions: list[BuySuggestion]
    bucket_allocations: list[BucketAllocation] = field(default_factory=list)
    uninferrable_buckets: list[UninferrableBucket] = field(default_factory=list)


def compute_drift(
    state: PortfolioState, target: Target
) -> tuple[list[CategoryDrift], list[BucketDrift]]:
    total = state.total_base

    bucket_drifts: list[BucketDrift] = []
    for bs in state.buckets:
        cv = bs.current_value_base
        tv = total * bs.bucket.portfolio_weight_pct / Decimal(100)
        current_pct = _pct(cv, total)
        bucket_drifts.append(
            BucketDrift(
                bucket_state=bs,
                current_value=cv,
                target_value=tv,
                current_pct=current_pct,
                target_pct=bs.bucket.portfolio_weight_pct,
                drift_pp=current_pct - bs.bucket.portfolio_weight_pct,
            )
        )

    cat_drifts: list[CategoryDrift] = []
    for cat in target.categories:
        cat_buckets = [bd for bd in bucket_drifts if bd.bucket_state.bucket.category == cat.name]
        cv = sum((bd.current_value for bd in cat_buckets), Decimal(0))
        current_pct = _pct(cv, total)
        target_pct = cat.portfolio_weight_pct
        cat_drifts.append(
            CategoryDrift(
                category=cat,
                current_value=cv,
                current_pct=current_pct,
                target_pct=target_pct,
                drift_pp=current_pct - target_pct,
                buckets=cat_buckets,
            )
        )

    return cat_drifts, bucket_drifts


def suggest_buys(
    state: PortfolioState, target: Target, extra_cash: Decimal
) -> RebalanceResult:
    if extra_cash < 0:
        raise ValueError("extra_cash must be non-negative")

    used_free_cash = extra_cash == 0
    cash_pool_value = state.free_cash_base
    total_after = state.total_base + (Decimal(0) if used_free_cash else extra_cash)

    # Cash floor: keep at least (cash_target − DRIFT_CAP_PP) % of total in cash. We don't
    # deplete the cash bucket below the comfort zone even when /rebalance runs.
    cash_bucket_state = state.cash_bucket
    if cash_bucket_state is not None:
        cash_target_pct = cash_bucket_state.bucket.portfolio_weight_pct
        min_cash_pct = max(Decimal(0), cash_target_pct - DRIFT_CAP_PP)
        min_cash_after = min_cash_pct / Decimal(100) * total_after
        cash_to_deploy = max(
            Decimal(0),
            cash_pool_value + (Decimal(0) if used_free_cash else extra_cash) - min_cash_after,
        )
    else:
        cash_to_deploy = cash_pool_value + (Decimal(0) if used_free_cash else extra_cash)

    destinations = [bs for bs in state.buckets if not bs.bucket.is_cash]

    # Step 1: classify underweight buckets.
    #   buyable    → explicit-ticker bucket with a placeholder/holding to buy
    #   filter     → filter-mode bucket (recommend by category, not by ticker)
    #   uninferrable → no holdings, no explicit, no filter → warn
    buyable: list[tuple[BucketState, Decimal]] = []
    filter_alloc: list[tuple[BucketState, Decimal]] = []
    uninferrable: list[UninferrableBucket] = []

    for bs in destinations:
        target_value = total_after * bs.bucket.portfolio_weight_pct / Decimal(100)
        gap = target_value - bs.current_value_base
        if gap <= 0:
            continue
        if bs.bucket.has_explicit_tickers and _has_buyable_holding(bs):
            buyable.append((bs, gap))
        elif bs.bucket.has_filter:
            filter_alloc.append((bs, gap))
        else:
            uninferrable.append(
                UninferrableBucket(
                    bucket_name=bs.bucket.name,
                    category=bs.bucket.category,
                    target_pct=bs.bucket.portfolio_weight_pct,
                    gap_base=gap,
                )
            )

    # Step 2: throttle filter groups — within each (category, bond_type) cluster of >=2 filter
    # buckets, drop the smallest-gap one so cash concentrates on the most underweight.
    filter_alloc = _throttle_filter_groups(filter_alloc)

    distributable_pairs = buyable + filter_alloc
    positive_gap_sum = sum((g for _, g in distributable_pairs), Decimal(0))

    if cash_to_deploy <= 0 or positive_gap_sum <= 0:
        return RebalanceResult(
            cash_to_deploy=cash_to_deploy,
            extra_cash=extra_cash,
            used_free_cash=used_free_cash,
            spent=Decimal(0),
            reserved=Decimal(0),
            leftover=cash_to_deploy,
            suggestions=[],
            bucket_allocations=[],
            uninferrable_buckets=uninferrable,
        )

    suggestions: list[BuySuggestion] = []
    allocations: list[BucketAllocation] = []
    fallback_candidates: list[tuple[BucketState, Decimal, Decimal]] = []
    spent = Decimal(0)
    reserved = Decimal(0)

    # Step 3: main distribution — lots for buyable buckets (drift-capped), reservations for filter.
    for bs, gap in buyable:
        bucket_alloc = cash_to_deploy * gap / positive_gap_sum
        bucket_sugg, bucket_spent = _buy_in_bucket(bs, bucket_alloc, total_after)
        if bucket_sugg:
            suggestions.extend(bucket_sugg)
            spent += bucket_spent
        else:
            fallback_candidates.append((bs, gap, bucket_alloc))

    for bs, gap in filter_alloc:
        amount = cash_to_deploy * gap / positive_gap_sum
        allocations.append(
            BucketAllocation(
                bucket_name=bs.bucket.name,
                category=bs.bucket.category,
                filter_summary=_describe_filter(bs.bucket),
                amount_base=amount,
            )
        )
        reserved += amount

    # Step 4: try to upgrade explicit fallbacks (didn't fit a lot from their pro-rata share)
    # into concrete 1-lot purchases using leftover, sorted by gap desc.
    leftover = cash_to_deploy - spent - reserved
    fallback_candidates.sort(key=lambda x: -x[1])
    for bs, gap, bucket_alloc in fallback_candidates:
        outcome = _try_promote(bs, leftover, total_after)
        if outcome is None:
            # Drift cap would overshoot — drop silently. bucket_alloc stays in leftover.
            continue
        kind, payload = outcome
        if kind == "buy":
            sugg, cost = payload
            suggestions.append(sugg)
            spent += cost
            leftover -= cost
        elif kind == "hint":
            # Drift cap OK but leftover < lot_cost — save-up hint with lot info.
            allocations.append(
                BucketAllocation(
                    bucket_name=bs.bucket.name,
                    category=bs.bucket.category,
                    filter_summary=_lot_hint([(h, Decimal(1)) for h in payload]),
                    amount_base=bucket_alloc,
                )
            )
            reserved += bucket_alloc
            leftover = cash_to_deploy - spent - reserved

    return RebalanceResult(
        cash_to_deploy=cash_to_deploy,
        extra_cash=extra_cash,
        used_free_cash=used_free_cash,
        spent=spent,
        reserved=reserved,
        leftover=cash_to_deploy - spent - reserved,
        suggestions=suggestions,
        bucket_allocations=allocations,
        uninferrable_buckets=uninferrable,
    )


def _throttle_filter_groups(
    filter_alloc: list[tuple[BucketState, Decimal]],
) -> list[tuple[BucketState, Decimal]]:
    """Drop the smallest-gap bucket from each (category, bond_type) group of size >= 2.

    Concentrates cash on the most underweight bucket of each kind. Buckets without a
    bond_type filter (or bond_type=any) are kept as-is.
    """
    groups: dict[tuple[str, str], list[tuple[BucketState, Decimal]]] = defaultdict(list)
    ungrouped: list[tuple[BucketState, Decimal]] = []
    for bs, gap in filter_alloc:
        flt = bs.bucket.filter_
        if flt is None or not flt.bond_type or flt.bond_type == "any":
            ungrouped.append((bs, gap))
            continue
        groups[(bs.bucket.category, flt.bond_type)].append((bs, gap))

    result = list(ungrouped)
    for members in groups.values():
        if len(members) >= 2:
            members.sort(key=lambda x: x[1], reverse=True)
            members = members[:-1]
        result.extend(members)
    return result


def _max_addition_value(
    bucket: TargetBucket, current_value: Decimal, total_after: Decimal
) -> Decimal:
    """Max RUB that can be added to this bucket without pushing drift above +DRIFT_CAP_PP."""
    cap_pct = bucket.portfolio_weight_pct + DRIFT_CAP_PP
    return cap_pct / Decimal(100) * total_after - current_value


def _buy_in_bucket(
    bs: BucketState, bucket_alloc: Decimal, total_after: Decimal
) -> tuple[list["BuySuggestion"], Decimal]:
    """Compute lots for each holding, respecting both pro-rata share and drift cap."""
    max_addition = _max_addition_value(bs.bucket, bs.current_value_base, total_after)
    if max_addition <= 0:
        return [], Decimal(0)
    weight_pairs = _holding_distribution(bs)
    weight_sum = sum((w for _, w in weight_pairs), Decimal(0))
    if weight_sum <= 0:
        return [], Decimal(0)

    bucket_sugg: list[BuySuggestion] = []
    spent = Decimal(0)
    remaining_cap = max_addition
    for h, w in weight_pairs:
        h_alloc = bucket_alloc * w / weight_sum
        lot_cost = h.lot_cost_base
        if lot_cost <= 0:
            continue
        natural_lots = int(h_alloc // lot_cost)
        cap_lots = int(remaining_cap // lot_cost) if remaining_cap > 0 else 0
        lots = max(0, min(natural_lots, cap_lots))
        if lots <= 0:
            continue
        cost = Decimal(lots) * lot_cost
        bucket_sugg.append(
            BuySuggestion(
                ticker=h.ticker,
                name=h.name,
                bucket_name=bs.bucket.name,
                lots=lots,
                units=Decimal(lots) * Decimal(h.lot),
                unit_price_base=h.last_price * h.fx_to_base,
                total_cost_base=cost,
            )
        )
        spent += cost
        remaining_cap -= cost
    return bucket_sugg, spent


def _try_promote(
    bs: BucketState, leftover: Decimal, total_after: Decimal
) -> tuple[str, object] | None:
    """Try to upgrade an explicit-fallback bucket to a concrete 1-lot purchase.

    Returns:
      ('buy', (sugg, lot_cost))   — leftover ≥ lot_cost AND drift cap OK
      ('hint', [holdings])         — drift cap OK but leftover too small (save-up hint)
      None                         — drift cap fails → drop silently
    """
    max_addition = _max_addition_value(bs.bucket, bs.current_value_base, total_after)
    if max_addition <= 0:
        return None
    non_cash = [h for h in bs.holdings if not h.is_cash]
    if not non_cash:
        return None
    h = non_cash[0]
    lot_cost = h.lot_cost_base
    if lot_cost <= 0:
        return None
    if lot_cost > max_addition:
        return None  # buying 1 lot overshoots drift cap
    if lot_cost > leftover:
        return ("hint", non_cash)
    sugg = BuySuggestion(
        ticker=h.ticker,
        name=h.name,
        bucket_name=bs.bucket.name,
        lots=1,
        units=Decimal(h.lot),
        unit_price_base=h.last_price * h.fx_to_base,
        total_cost_base=lot_cost,
    )
    return ("buy", (sugg, lot_cost))


_BOND_TYPE_LABEL = {
    "replaced": "замещающие",
    "ofz": "ОФЗ",
    "corp": "корпоративные",
    "any": "любые облигации",
}


def _describe_filter(bucket: TargetBucket) -> str:
    """Human-readable summary of a filter — for telling the user what to buy."""
    flt = bucket.filter_
    if flt is None:
        return bucket.name
    parts: list[str] = []
    if flt.bond_type:
        parts.append(_BOND_TYPE_LABEL.get(flt.bond_type, flt.bond_type))
    if flt.nominal_currency:
        nc = flt.nominal_currency
        parts.append("номинал не в ₽" if nc == "not_rub" else f"номинал {nc.upper()}")
    if flt.class_code:
        parts.append(f"class={flt.class_code}")
    maturity = _format_maturity_range(flt)
    if maturity:
        parts.append(maturity)
    return ", ".join(parts) or bucket.name


def _format_maturity_range(flt: BucketFilter) -> str:
    lo = flt.maturity_min_years
    hi = flt.maturity_max_years
    if lo is None and hi is None:
        return ""
    if lo is None:
        return f"≤{_y(hi)}л"
    if hi is None:
        return f">{_y(lo)}л"
    return f"{_y(lo)}–{_y(hi)}л"


def _y(v: Decimal) -> str:
    s = format(v.normalize(), "f")
    return s.rstrip("0").rstrip(".") or "0"


def _lot_hint(weight_pairs: list[tuple[Holding, Decimal]]) -> str:
    """When an explicit-ticker bucket can't fit a single lot, describe the lot cost."""
    parts: list[str] = []
    for h, _ in weight_pairs:
        if h.lot_cost_base > 0:
            parts.append(f"{h.ticker} 1 лот ≈ {int(h.lot_cost_base)} ₽")
    return "; ".join(parts) if parts else ""


def _has_buyable_holding(bs: BucketState) -> bool:
    return any(not h.is_cash for h in bs.holdings)


def _holding_distribution(bs: BucketState) -> list[tuple[Holding, Decimal]]:
    """Return (holding, weight) pairs describing how bucket allocation splits among holdings.

    - Explicit-tickers bucket: use configured weights from target.yaml (within-bucket).
    - Filter-mode bucket: pro-rata by current value among non-cash holdings (equal split if all zero).

    Cash holdings (instrument_type='currency') are excluded — we never suggest buying cash.
    """
    non_cash = [h for h in bs.holdings if not h.is_cash]
    if not non_cash:
        return []

    if bs.bucket.has_explicit_tickers:
        weight_by_ticker = {
            t.upper(): w for t, w in bs.bucket.explicit_tickers
        }
        return [
            (h, weight_by_ticker.get(h.ticker.upper(), Decimal(0)))
            for h in non_cash
        ]

    # Filter mode: pro-rata by current value, fallback to equal split.
    valued = [(h, h.value_base) for h in non_cash if h.value_base > 0]
    if valued:
        return valued
    return [(h, Decimal(1)) for h in non_cash]


def _pct(part: Decimal, total: Decimal) -> Decimal:
    if total <= 0:
        return Decimal(0)
    return part / total * Decimal(100)
