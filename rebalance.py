from dataclasses import dataclass, field
from decimal import Decimal

from models import BucketState, Holding, PortfolioState
from target import Target, TargetCategory


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
    ticker: str
    name: str
    bucket_name: str
    lots: int
    units: Decimal
    unit_price_base: Decimal
    total_cost_base: Decimal


@dataclass(frozen=True)
class EmptyBucketWarning:
    bucket_name: str
    category: str
    target_pct: Decimal
    gap_base: Decimal


@dataclass(frozen=True)
class RebalanceResult:
    cash_to_deploy: Decimal
    extra_cash: Decimal
    used_free_cash: bool
    spent: Decimal
    leftover: Decimal
    suggestions: list[BuySuggestion]
    empty_underweight_buckets: list[EmptyBucketWarning] = field(default_factory=list)


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
    cash_to_deploy = cash_pool_value + (Decimal(0) if used_free_cash else extra_cash)
    total_after = state.total_base + (Decimal(0) if used_free_cash else extra_cash)

    # Destination buckets = everything except the cash pool.
    destinations = [bs for bs in state.buckets if not bs.bucket.is_cash]

    bucket_gaps: list[tuple[BucketState, Decimal]] = []
    empty_underweights: list[EmptyBucketWarning] = []
    for bs in destinations:
        target_value = total_after * bs.bucket.portfolio_weight_pct / Decimal(100)
        gap = target_value - bs.current_value_base
        bucket_gaps.append((bs, gap))
        if gap > 0 and not _has_buyable_holding(bs):
            empty_underweights.append(
                EmptyBucketWarning(
                    bucket_name=bs.bucket.name,
                    category=bs.bucket.category,
                    target_pct=bs.bucket.portfolio_weight_pct,
                    gap_base=gap,
                )
            )

    distributable = [(bs, g) for bs, g in bucket_gaps if g > 0 and _has_buyable_holding(bs)]
    positive_gap_sum = sum((g for _, g in distributable), Decimal(0))

    if cash_to_deploy <= 0 or positive_gap_sum <= 0:
        return RebalanceResult(
            cash_to_deploy=cash_to_deploy,
            extra_cash=extra_cash,
            used_free_cash=used_free_cash,
            spent=Decimal(0),
            leftover=cash_to_deploy,
            suggestions=[],
            empty_underweight_buckets=empty_underweights,
        )

    suggestions: list[BuySuggestion] = []
    spent = Decimal(0)
    for bs, gap in distributable:
        bucket_alloc = cash_to_deploy * gap / positive_gap_sum
        weight_pairs = _holding_distribution(bs)
        weight_sum = sum((w for _, w in weight_pairs), Decimal(0))
        if weight_sum <= 0:
            continue
        for h, w in weight_pairs:
            h_alloc = bucket_alloc * w / weight_sum
            lot_cost = h.lot_cost_base
            if lot_cost <= 0:
                continue
            lots = int(h_alloc // lot_cost)
            if lots <= 0:
                continue
            cost = Decimal(lots) * lot_cost
            suggestions.append(
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

    return RebalanceResult(
        cash_to_deploy=cash_to_deploy,
        extra_cash=extra_cash,
        used_free_cash=used_free_cash,
        spent=spent,
        leftover=cash_to_deploy - spent,
        suggestions=suggestions,
        empty_underweight_buckets=empty_underweights,
    )


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
