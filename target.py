from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

SUPPORTED_CURRENCIES = {"RUB", "USD", "EUR"}
WEIGHT_TOLERANCE = Decimal("0.01")


class TargetConfigError(ValueError):
    pass


@dataclass(frozen=True)
class TargetLeaf:
    ticker: str
    category: str
    category_weight_pct: Decimal
    ticker_weight_in_cat_pct: Decimal

    @property
    def portfolio_weight_pct(self) -> Decimal:
        return self.category_weight_pct * self.ticker_weight_in_cat_pct / Decimal(100)


@dataclass(frozen=True)
class TargetCategory:
    name: str
    weight_pct: Decimal
    tickers: dict[str, Decimal]


@dataclass(frozen=True)
class Target:
    base_currency: str
    categories: list[TargetCategory]
    leaves: list[TargetLeaf]

    @property
    def tickers(self) -> list[str]:
        return [leaf.ticker for leaf in self.leaves]


def load_target(path: str | Path) -> Target:
    path = Path(path)
    if not path.exists():
        raise TargetConfigError(f"Target file not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TargetConfigError("Target file must be a YAML mapping at top level")

    base_currency = str(raw.get("base_currency", "RUB")).upper()
    if base_currency not in SUPPORTED_CURRENCIES:
        raise TargetConfigError(
            f"base_currency must be one of {sorted(SUPPORTED_CURRENCIES)}, got {base_currency!r}"
        )

    raw_categories = raw.get("categories")
    if not isinstance(raw_categories, dict) or not raw_categories:
        raise TargetConfigError("`categories` must be a non-empty mapping")

    categories: list[TargetCategory] = []
    leaves: list[TargetLeaf] = []

    for cat_name, cat_body in raw_categories.items():
        if not isinstance(cat_body, dict):
            raise TargetConfigError(f"Category {cat_name!r} must be a mapping")
        weight = cat_body.get("weight")
        if weight is None:
            raise TargetConfigError(f"Category {cat_name!r} missing `weight`")
        weight_dec = _as_decimal(weight, f"categories.{cat_name}.weight")
        if weight_dec <= 0:
            raise TargetConfigError(f"Category {cat_name!r} weight must be positive")

        tickers_raw = cat_body.get("tickers")
        if not isinstance(tickers_raw, dict) or not tickers_raw:
            raise TargetConfigError(f"Category {cat_name!r} must have non-empty `tickers`")

        tickers: dict[str, Decimal] = {}
        for ticker, t_weight in tickers_raw.items():
            ticker_str = str(ticker).strip().upper()
            if not ticker_str:
                raise TargetConfigError(f"Empty ticker in category {cat_name!r}")
            if ticker_str in tickers:
                raise TargetConfigError(
                    f"Duplicate ticker {ticker_str!r} in category {cat_name!r}"
                )
            tw = _as_decimal(t_weight, f"categories.{cat_name}.tickers.{ticker_str}")
            if tw <= 0:
                raise TargetConfigError(
                    f"Ticker {ticker_str!r} in {cat_name!r} weight must be positive"
                )
            tickers[ticker_str] = tw

        total_tickers = sum(tickers.values(), Decimal(0))
        if abs(total_tickers - Decimal(100)) > WEIGHT_TOLERANCE:
            raise TargetConfigError(
                f"Ticker weights in category {cat_name!r} sum to {total_tickers}, expected 100"
            )

        categories.append(TargetCategory(name=cat_name, weight_pct=weight_dec, tickers=tickers))
        for ticker, t_weight in tickers.items():
            leaves.append(
                TargetLeaf(
                    ticker=ticker,
                    category=cat_name,
                    category_weight_pct=weight_dec,
                    ticker_weight_in_cat_pct=t_weight,
                )
            )

    total_categories = sum(c.weight_pct for c in categories)
    if abs(total_categories - Decimal(100)) > WEIGHT_TOLERANCE:
        raise TargetConfigError(
            f"Category weights sum to {total_categories}, expected 100"
        )

    all_tickers = [leaf.ticker for leaf in leaves]
    if len(all_tickers) != len(set(all_tickers)):
        raise TargetConfigError("Ticker appears in more than one category")

    return Target(base_currency=base_currency, categories=categories, leaves=leaves)


def _as_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise TargetConfigError(f"{field}: boolean is not a valid number")
    try:
        return Decimal(str(value))
    except Exception as e:
        raise TargetConfigError(f"{field}: cannot parse {value!r} as number") from e
