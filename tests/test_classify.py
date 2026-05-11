from datetime import datetime
from decimal import Decimal

from classify import classify_holding, find_bucket
from target import BucketFilter, TargetBucket


AS_OF = datetime(2026, 5, 11)


def bucket_filter(name: str, **filter_kwargs) -> TargetBucket:
    return TargetBucket(
        name=name,
        category="bonds",
        portfolio_weight_pct=Decimal(10),
        filter_=BucketFilter(**filter_kwargs),
    )


def bucket_tickers(name: str, *tickers: str) -> TargetBucket:
    return TargetBucket(
        name=name,
        category="stocks",
        portfolio_weight_pct=Decimal(10),
        explicit_tickers=tuple((t, Decimal(100 / len(tickers))) for t in tickers),
    )


def H(**kwargs) -> dict:
    defaults = dict(
        ticker="X",
        instrument_type="bond",
        class_code="TQOB",
        bond_type="unspecified",
        nominal_currency="rub",
        maturity_date=None,
        as_of=AS_OF,
    )
    defaults.update(kwargs)
    return defaults


def test_explicit_ticker_match() -> None:
    b = bucket_tickers("sber", "SBER")
    assert classify_holding(b, **H(ticker="SBER")) is True
    assert classify_holding(b, **H(ticker="LKOH")) is False


def test_explicit_ticker_match_case_insensitive() -> None:
    b = bucket_tickers("sber", "SBER")
    assert classify_holding(b, **H(ticker="sber")) is True


def test_filter_replaced_bond() -> None:
    b = bucket_filter("replaced", bond_type="replaced")
    assert classify_holding(b, **H(bond_type="replaced", class_code="TQCB")) is True
    assert classify_holding(b, **H(bond_type="unspecified", class_code="TQOB")) is False


def test_filter_ofz_via_class_code() -> None:
    b = bucket_filter("ofz", bond_type="ofz")
    assert classify_holding(b, **H(class_code="TQOB", bond_type="unspecified")) is True
    assert classify_holding(b, **H(class_code="TQCB", bond_type="unspecified")) is False


def test_filter_corp_via_class_code() -> None:
    b = bucket_filter("corp", bond_type="corp")
    assert classify_holding(b, **H(class_code="TQCB")) is True
    assert classify_holding(b, **H(class_code="TQTE")) is True
    assert classify_holding(b, **H(class_code="TQOB")) is False


def test_filter_nominal_currency_not_rub() -> None:
    b = bucket_filter("foreign_nominal", nominal_currency="not_rub")
    assert classify_holding(b, **H(nominal_currency="usd")) is True
    assert classify_holding(b, **H(nominal_currency="rub")) is False


def test_filter_maturity_range() -> None:
    b = bucket_filter("two_to_four", maturity_min_years=Decimal(2), maturity_max_years=Decimal(4))
    # 3 years from AS_OF
    in_range = datetime(2029, 5, 11)
    # 5 years
    too_long = datetime(2031, 5, 11)
    # 1 year
    too_short = datetime(2027, 5, 11)
    assert classify_holding(b, **H(maturity_date=in_range)) is True
    assert classify_holding(b, **H(maturity_date=too_long)) is False
    assert classify_holding(b, **H(maturity_date=too_short)) is False


def test_filter_combination_anded() -> None:
    b = bucket_filter("ofz_mid", bond_type="ofz", maturity_max_years=Decimal(7))
    matching = datetime(2030, 5, 11)
    assert classify_holding(b, **H(class_code="TQOB", maturity_date=matching)) is True
    assert classify_holding(b, **H(class_code="TQCB", maturity_date=matching)) is False
    assert classify_holding(b, **H(class_code="TQOB", maturity_date=datetime(2040, 5, 11))) is False


def test_filter_missing_metadata_does_not_match() -> None:
    b = bucket_filter("short", maturity_max_years=Decimal(3))
    assert classify_holding(b, **H(maturity_date=None)) is False


def test_find_bucket_prefers_explicit_over_filter() -> None:
    explicit = bucket_tickers("sber", "SBER")
    filt = bucket_filter("all_bonds", bond_type="any")
    buckets = [filt, explicit]
    assert find_bucket(buckets, **H(ticker="SBER", instrument_type="share")) is explicit


def test_find_bucket_returns_first_matching_filter() -> None:
    short = bucket_filter("ofz_short", bond_type="ofz", maturity_max_years=Decimal(3))
    long = bucket_filter("ofz_long", bond_type="ofz")
    buckets = [short, long]
    assert find_bucket(buckets, **H(class_code="TQOB", maturity_date=datetime(2028, 5, 11))) is short
    assert find_bucket(buckets, **H(class_code="TQOB", maturity_date=datetime(2036, 5, 11))) is long


def test_find_bucket_none_when_no_match() -> None:
    b = bucket_filter("ofz", bond_type="ofz")
    assert find_bucket([b], **H(class_code="TQBR", instrument_type="share")) is None
