from dataclasses import dataclass
from decimal import Decimal

from models import Holding, PortfolioState
from target import Target, TargetCategory, TargetLeaf


@dataclass(frozen=True)
class LeafDrift:
    leaf: TargetLeaf
    holding: Holding
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
    leaves: list[LeafDrift]


@dataclass(frozen=True)
class BuySuggestion:
    ticker: str
    name: str
    lots: int
    units: Decimal
    unit_price_base: Decimal
    total_cost_base: Decimal


@dataclass(frozen=True)
class RebalanceResult:
    cash_to_deploy: Decimal
    extra_cash: Decimal
    used_free_cash: bool
    spent: Decimal
    leftover: Decimal
    suggestions: list[BuySuggestion]


def compute_drift(
    state: PortfolioState, target: Target
) -> tuple[list[CategoryDrift], list[LeafDrift]]:
    total = state.total_base
    holdings_by_ticker = {h.ticker: h for h in state.target_holdings}

    leaf_drifts: list[LeafDrift] = []
    for leaf in target.leaves:
        h = holdings_by_ticker.get(leaf.ticker)
        if h is None:
            raise RuntimeError(f"Internal error: missing holding for target ticker {leaf.ticker}")
        current_value = h.value_base
        current_pct = _pct(current_value, total)
        target_value = total * leaf.portfolio_weight_pct / Decimal(100)
        leaf_drifts.append(
            LeafDrift(
                leaf=leaf,
                holding=h,
                current_value=current_value,
                target_value=target_value,
                current_pct=current_pct,
                target_pct=leaf.portfolio_weight_pct,
                drift_pp=current_pct - leaf.portfolio_weight_pct,
            )
        )

    cat_drifts: list[CategoryDrift] = []
    for cat in target.categories:
        cat_leaves = [ld for ld in leaf_drifts if ld.leaf.category == cat.name]
        current_value = sum((ld.current_value for ld in cat_leaves), Decimal(0))
        current_pct = _pct(current_value, total)
        cat_drifts.append(
            CategoryDrift(
                category=cat,
                current_value=current_value,
                current_pct=current_pct,
                target_pct=cat.weight_pct,
                drift_pp=current_pct - cat.weight_pct,
                leaves=cat_leaves,
            )
        )

    return cat_drifts, leaf_drifts


def suggest_buys(
    state: PortfolioState, target: Target, extra_cash: Decimal
) -> RebalanceResult:
    """
    extra_cash > 0: user is adding new money on top of the account; deploy exactly extra_cash.
    extra_cash == 0: deploy current free cash held in the account.
    Sells are never suggested.
    """
    if extra_cash < 0:
        raise ValueError("extra_cash must be non-negative")

    used_free_cash = extra_cash == 0
    cash_to_deploy = state.free_cash_base if used_free_cash else extra_cash
    total_after = state.total_base + (extra_cash if not used_free_cash else Decimal(0))

    holdings_by_ticker = {h.ticker: h for h in state.target_holdings}
    leaf_gaps: list[tuple[TargetLeaf, Holding, Decimal]] = []
    for leaf in target.leaves:
        h = holdings_by_ticker.get(leaf.ticker)
        if h is None:
            raise RuntimeError(f"Internal error: missing holding for target ticker {leaf.ticker}")
        target_value = total_after * leaf.portfolio_weight_pct / Decimal(100)
        gap = target_value - h.value_base
        leaf_gaps.append((leaf, h, gap))

    positive_gap_sum = sum(
        (gap for _, _, gap in leaf_gaps if gap > 0), Decimal(0)
    )

    if cash_to_deploy <= 0 or positive_gap_sum <= 0:
        return RebalanceResult(
            cash_to_deploy=cash_to_deploy,
            extra_cash=extra_cash,
            used_free_cash=used_free_cash,
            spent=Decimal(0),
            leftover=cash_to_deploy,
            suggestions=[],
        )

    suggestions: list[BuySuggestion] = []
    spent = Decimal(0)
    for leaf, h, gap in leaf_gaps:
        if gap <= 0:
            continue
        allocation = cash_to_deploy * gap / positive_gap_sum
        lot_cost = h.lot_cost_base
        if lot_cost <= 0:
            continue
        lots = int(allocation // lot_cost)
        if lots <= 0:
            continue
        cost = Decimal(lots) * lot_cost
        suggestions.append(
            BuySuggestion(
                ticker=leaf.ticker,
                name=h.name,
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
    )


def _pct(part: Decimal, total: Decimal) -> Decimal:
    if total <= 0:
        return Decimal(0)
    return part / total * Decimal(100)
