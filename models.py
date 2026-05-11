from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


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

    @property
    def value_base(self) -> Decimal:
        return self.quantity * self.last_price * self.fx_to_base

    @property
    def lot_cost_base(self) -> Decimal:
        return self.last_price * self.fx_to_base * Decimal(self.lot)


@dataclass(frozen=True)
class PortfolioState:
    target_holdings: list[Holding]
    untracked_holdings: list[Holding]
    free_cash_base: Decimal
    free_cash_breakdown: dict[str, Decimal]
    base_currency: str
    fetched_at: datetime

    @property
    def total_base(self) -> Decimal:
        invested = sum((h.value_base for h in self.target_holdings), Decimal(0))
        invested += sum((h.value_base for h in self.untracked_holdings), Decimal(0))
        return invested + self.free_cash_base
