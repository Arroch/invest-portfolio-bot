from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from target import TargetBucket


@dataclass(frozen=True)
class Holding:
    figi: str
    ticker: str
    name: str
    quantity: Decimal
    last_price: Decimal
    currency: str
    lot: int
    fx_to_base: Decimal
    instrument_type: str = ""
    class_code: str = ""
    bond_type: str | None = None
    nominal_currency: str | None = None
    maturity_date: datetime | None = None

    @property
    def value_base(self) -> Decimal:
        return self.quantity * self.last_price * self.fx_to_base

    @property
    def lot_cost_base(self) -> Decimal:
        return self.last_price * self.fx_to_base * Decimal(self.lot)

    @property
    def is_cash(self) -> bool:
        return self.instrument_type == "currency"


@dataclass(frozen=True)
class BucketState:
    bucket: TargetBucket
    holdings: tuple[Holding, ...] = field(default_factory=tuple)

    @property
    def current_value_base(self) -> Decimal:
        return sum((h.value_base for h in self.holdings), Decimal(0))


@dataclass(frozen=True)
class PortfolioState:
    buckets: tuple[BucketState, ...]
    untracked_holdings: tuple[Holding, ...]
    free_cash_breakdown: dict[str, Decimal]
    base_currency: str
    fetched_at: datetime

    @property
    def cash_bucket(self) -> BucketState | None:
        for bs in self.buckets:
            if bs.bucket.is_cash:
                return bs
        return None

    @property
    def free_cash_base(self) -> Decimal:
        cb = self.cash_bucket
        return cb.current_value_base if cb is not None else Decimal(0)

    @property
    def total_base(self) -> Decimal:
        tracked = sum((b.current_value_base for b in self.buckets), Decimal(0))
        untracked = sum((h.value_base for h in self.untracked_holdings), Decimal(0))
        return tracked + untracked
