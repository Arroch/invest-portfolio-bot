from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

SUPPORTED_CURRENCIES = {"RUB", "USD", "EUR"}
WEIGHT_TOLERANCE = Decimal("0.01")

ALLOWED_FILTER_KEYS = {
    "bond_type",
    "nominal_currency",
    "maturity_max_years",
    "maturity_min_years",
    "class_code",
}
ALLOWED_BOND_TYPES = {"replaced", "ofz", "corp", "any"}
ALLOWED_BUCKET_KEYS = {
    "weight",
    "tickers",
    "match",
    "cash_currencies",
    "is_cash",
}


class TargetConfigError(ValueError):
    pass


@dataclass(frozen=True)
class BucketFilter:
    """Conditions a holding must satisfy to land in a filter-mode bucket.
    All non-None fields are AND-ed.
    """

    bond_type: str | None = None
    nominal_currency: str | None = None
    maturity_max_years: Decimal | None = None
    maturity_min_years: Decimal | None = None
    class_code: str | None = None


@dataclass(frozen=True)
class TargetBucket:
    """Unit of target allocation. Weight is absolute (% of WHOLE portfolio)."""

    name: str
    category: str
    portfolio_weight_pct: Decimal
    # Inside this bucket, multiple tickers split bucket value by these weights (sum 100).
    explicit_tickers: tuple[tuple[str, Decimal], ...] = ()
    filter_: BucketFilter | None = None
    # Cash currencies routed to this bucket instead of to the is_cash bucket.
    cash_currencies: tuple[str, ...] = ()
    # True for the special "cash pool" bucket. Excluded from /rebalance destinations.
    is_cash: bool = False

    @property
    def has_explicit_tickers(self) -> bool:
        return bool(self.explicit_tickers)

    @property
    def has_filter(self) -> bool:
        return self.filter_ is not None

    @property
    def explicit_ticker_names(self) -> tuple[str, ...]:
        return tuple(t for t, _ in self.explicit_tickers)


@dataclass(frozen=True)
class TargetCategory:
    """Display grouping. Categories carry no weight — buckets do."""

    name: str
    buckets: tuple[TargetBucket, ...]

    @property
    def portfolio_weight_pct(self) -> Decimal:
        return sum((b.portfolio_weight_pct for b in self.buckets), Decimal(0))


@dataclass(frozen=True)
class Target:
    base_currency: str
    categories: tuple[TargetCategory, ...]

    @property
    def buckets(self) -> list[TargetBucket]:
        return [b for c in self.categories for b in c.buckets]

    @property
    def cash_bucket(self) -> TargetBucket:
        for b in self.buckets:
            if b.is_cash:
                return b
        raise RuntimeError("No is_cash bucket — schema validation should have caught this")

    @property
    def explicit_tickers(self) -> list[str]:
        seen: list[str] = []
        for b in self.buckets:
            for t in b.explicit_ticker_names:
                if t not in seen:
                    seen.append(t)
        return seen


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
    for cat_name, cat_body in raw_categories.items():
        if not isinstance(cat_body, dict):
            raise TargetConfigError(f"Category {cat_name!r} must be a mapping")
        buckets_raw = cat_body.get("buckets")
        if not isinstance(buckets_raw, dict) or not buckets_raw:
            raise TargetConfigError(
                f"Category {cat_name!r}: must have a non-empty `buckets` mapping"
            )
        buckets = [_parse_bucket(name, body, cat_name) for name, body in buckets_raw.items()]
        categories.append(TargetCategory(name=cat_name, buckets=tuple(buckets)))

    all_buckets = [b for c in categories for b in c.buckets]
    total = sum((b.portfolio_weight_pct for b in all_buckets), Decimal(0))
    if abs(total - Decimal(100)) > WEIGHT_TOLERANCE:
        raise TargetConfigError(
            f"Bucket weights sum to {total}, expected 100 (across the whole portfolio)"
        )

    cash_buckets = [b for b in all_buckets if b.is_cash]
    if len(cash_buckets) != 1:
        raise TargetConfigError(
            f"Exactly one bucket must have `is_cash: true`, found {len(cash_buckets)}"
        )

    all_explicit = [t for b in all_buckets for t in b.explicit_ticker_names]
    if len(all_explicit) != len(set(all_explicit)):
        raise TargetConfigError("An explicit ticker appears in more than one bucket")

    all_cash_currencies = [c for b in all_buckets for c in b.cash_currencies]
    if len(all_cash_currencies) != len(set(all_cash_currencies)):
        raise TargetConfigError("A `cash_currencies` entry appears in more than one bucket")

    names = [b.name for b in all_buckets]
    if len(names) != len(set(names)):
        raise TargetConfigError("Duplicate bucket name across categories")

    return Target(base_currency=base_currency, categories=tuple(categories))


def _parse_bucket(name: str, body: object, cat_name: str) -> TargetBucket:
    path = f"categories.{cat_name}.buckets.{name}"
    if not isinstance(body, dict):
        raise TargetConfigError(f"{path}: must be a mapping")

    unknown = set(body) - ALLOWED_BUCKET_KEYS
    if unknown:
        raise TargetConfigError(
            f"{path}: unknown keys {sorted(unknown)}; allowed: {sorted(ALLOWED_BUCKET_KEYS)}"
        )

    weight_raw = body.get("weight")
    if weight_raw is None:
        raise TargetConfigError(f"{path}: missing `weight`")
    weight = _as_decimal(weight_raw, f"{path}.weight")
    if weight < 0:
        raise TargetConfigError(f"{path}: weight must be non-negative")

    tickers_raw = body.get("tickers")
    explicit: tuple[tuple[str, Decimal], ...] = ()
    if tickers_raw is not None:
        if not isinstance(tickers_raw, dict) or not tickers_raw:
            raise TargetConfigError(f"{path}.tickers: must be a non-empty mapping if present")
        parsed: list[tuple[str, Decimal]] = []
        for tname, tw in tickers_raw.items():
            t_upper = str(tname).strip().upper()
            if not t_upper:
                raise TargetConfigError(f"{path}.tickers: empty ticker name")
            tw_dec = _as_decimal(tw, f"{path}.tickers.{t_upper}")
            if tw_dec <= 0:
                raise TargetConfigError(f"{path}.tickers.{t_upper}: weight must be positive")
            parsed.append((t_upper, tw_dec))
        total = sum((w for _, w in parsed), Decimal(0))
        if abs(total - Decimal(100)) > WEIGHT_TOLERANCE:
            raise TargetConfigError(
                f"{path}.tickers: weights sum to {total}, expected 100"
            )
        explicit = tuple(parsed)

    filter_dict = body.get("match")
    filter_: BucketFilter | None = None
    if filter_dict is not None:
        if not isinstance(filter_dict, dict):
            raise TargetConfigError(f"{path}.match: must be a mapping")
        filter_ = _parse_filter(filter_dict, path)

    cash_currencies_raw = body.get("cash_currencies", [])
    if not isinstance(cash_currencies_raw, list):
        raise TargetConfigError(f"{path}.cash_currencies: must be a list of currency codes")
    cash_currencies = tuple(str(c).strip().upper() for c in cash_currencies_raw if str(c).strip())

    is_cash = bool(body.get("is_cash", False))

    return TargetBucket(
        name=str(name),
        category=cat_name,
        portfolio_weight_pct=weight,
        explicit_tickers=explicit,
        filter_=filter_,
        cash_currencies=cash_currencies,
        is_cash=is_cash,
    )


def _parse_filter(raw: dict, bucket_path: str) -> BucketFilter:
    unknown = set(raw) - ALLOWED_FILTER_KEYS
    if unknown:
        raise TargetConfigError(
            f"{bucket_path}.match: unknown keys {sorted(unknown)}; allowed: {sorted(ALLOWED_FILTER_KEYS)}"
        )

    bond_type = raw.get("bond_type")
    if bond_type is not None:
        bond_type = str(bond_type).lower().strip()
        if bond_type not in ALLOWED_BOND_TYPES:
            raise TargetConfigError(
                f"{bucket_path}.match.bond_type: {bond_type!r} not in {sorted(ALLOWED_BOND_TYPES)}"
            )

    nominal_currency = raw.get("nominal_currency")
    if nominal_currency is not None:
        nominal_currency = str(nominal_currency).lower().strip()

    class_code = raw.get("class_code")
    if class_code is not None:
        class_code = str(class_code).strip().upper()

    return BucketFilter(
        bond_type=bond_type,
        nominal_currency=nominal_currency,
        maturity_max_years=_optional_decimal(
            raw.get("maturity_max_years"), f"{bucket_path}.match.maturity_max_years"
        ),
        maturity_min_years=_optional_decimal(
            raw.get("maturity_min_years"), f"{bucket_path}.match.maturity_min_years"
        ),
        class_code=class_code,
    )


def _optional_decimal(value: object, field: str) -> Decimal | None:
    if value is None:
        return None
    return _as_decimal(value, field)


def _as_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise TargetConfigError(f"{field}: boolean is not a valid number")
    try:
        return Decimal(str(value))
    except Exception as e:
        raise TargetConfigError(f"{field}: cannot parse {value!r} as number") from e
