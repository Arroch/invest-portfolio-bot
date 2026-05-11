from datetime import datetime
from decimal import Decimal

from target import BucketFilter, TargetBucket

# Class codes that count as Russian Federation bonds (ОФЗ).
OFZ_CLASS_CODES = {"TQOB"}
# Class codes that count as corporate bonds.
CORP_CLASS_CODES = {"TQCB", "TQTE", "TQTD", "TQIR"}

DAYS_PER_YEAR = Decimal("365.25")


def classify_holding(
    bucket: TargetBucket,
    *,
    ticker: str,
    instrument_type: str,
    class_code: str,
    bond_type: str | None,
    nominal_currency: str | None,
    maturity_date: datetime | None,
    as_of: datetime,
) -> bool:
    """Return True if the holding matches this bucket's filter."""
    if bucket.has_explicit_tickers:
        return ticker.upper() in {t.upper() for t in bucket.explicit_ticker_names}

    flt = bucket.filter_
    if flt is None:
        return False

    if not _bond_type_matches(flt.bond_type, bond_type, class_code, instrument_type):
        return False
    if not _nominal_currency_matches(flt.nominal_currency, nominal_currency):
        return False
    if not _class_code_matches(flt.class_code, class_code):
        return False
    if not _maturity_matches(
        flt.maturity_max_years, flt.maturity_min_years, maturity_date, as_of
    ):
        return False
    return True


def find_bucket(
    buckets: list[TargetBucket],
    *,
    ticker: str,
    instrument_type: str,
    class_code: str,
    bond_type: str | None,
    nominal_currency: str | None,
    maturity_date: datetime | None,
    as_of: datetime,
) -> TargetBucket | None:
    """Resolve a holding to its bucket.

    Explicit-ticker bindings always win over filter-mode subcategories, regardless
    of YAML order. Within filter-mode, first match in YAML order wins.
    """
    ticker_u = ticker.upper()
    for b in buckets:
        if b.has_explicit_tickers and ticker_u in {t.upper() for t in b.explicit_ticker_names}:
            return b
    for b in buckets:
        if b.has_explicit_tickers:
            continue
        if classify_holding(
            b,
            ticker=ticker,
            instrument_type=instrument_type,
            class_code=class_code,
            bond_type=bond_type,
            nominal_currency=nominal_currency,
            maturity_date=maturity_date,
            as_of=as_of,
        ):
            return b
    return None


def _bond_type_matches(
    filter_value: str | None, bond_type: str | None, class_code: str, instrument_type: str
) -> bool:
    if filter_value is None:
        return True
    if filter_value == "any":
        return instrument_type == "bond"
    if filter_value == "replaced":
        return bond_type == "replaced"
    if filter_value == "ofz":
        return class_code.upper() in OFZ_CLASS_CODES
    if filter_value == "corp":
        return class_code.upper() in CORP_CLASS_CODES
    return False


def _nominal_currency_matches(filter_value: str | None, nominal_currency: str | None) -> bool:
    if filter_value is None:
        return True
    if nominal_currency is None:
        return False
    fv = filter_value.lower()
    nc = nominal_currency.lower()
    if fv == "not_rub":
        return nc != "rub"
    return nc == fv


def _class_code_matches(filter_value: str | None, class_code: str) -> bool:
    if filter_value is None:
        return True
    return class_code.upper() == filter_value.upper()


def _maturity_matches(
    max_years: Decimal | None,
    min_years: Decimal | None,
    maturity_date: datetime | None,
    as_of: datetime,
) -> bool:
    if max_years is None and min_years is None:
        return True
    if maturity_date is None:
        return False
    delta_days = Decimal(_diff_days(maturity_date, as_of))
    years = delta_days / DAYS_PER_YEAR
    if max_years is not None and years > max_years:
        return False
    if min_years is not None and years <= min_years:
        return False
    return True


def years_to_maturity(maturity_date: datetime | None, as_of: datetime) -> Decimal | None:
    if maturity_date is None:
        return None
    return Decimal(_diff_days(maturity_date, as_of)) / DAYS_PER_YEAR


def _diff_days(a: datetime, b: datetime) -> int:
    """Subtract two datetimes that may have mixed tz-awareness."""
    if (a.tzinfo is None) != (b.tzinfo is None):
        a = a.replace(tzinfo=None)
        b = b.replace(tzinfo=None)
    return (a - b).days
