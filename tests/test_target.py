from decimal import Decimal
from pathlib import Path

import pytest

from target import TargetConfigError, load_target


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "target.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_happy_path_mixed_modes(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        base_currency: RUB
        categories:
          stocks:
            buckets:
              ru_etf:  { weight: 35, tickers: { TMOS@: 100 } }
              sber:    { weight: 60, tickers: { SBER: 100 } }
          cash:
            buckets:
              liquid:
                weight: 5
                is_cash: true
                tickers: { TMON@: 100 }
        """,
    )
    target = load_target(path)
    assert target.base_currency == "RUB"
    assert [c.name for c in target.categories] == ["stocks", "cash"]
    names = [b.name for b in target.buckets]
    assert names == ["ru_etf", "sber", "liquid"]
    cash = target.cash_bucket
    assert cash.is_cash
    assert cash.portfolio_weight_pct == Decimal(5)
    sber = next(b for b in target.buckets if b.name == "sber")
    assert sber.portfolio_weight_pct == Decimal(60)
    assert sber.explicit_ticker_names == ("SBER",)


def test_subcategories_filter_mode(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        base_currency: RUB
        categories:
          bonds:
            buckets:
              replaced_2_4:
                weight: 50
                match:
                  bond_type: replaced
                  maturity_min_years: 2
                  maturity_max_years: 4
              ofz_mid:
                weight: 45
                match: { bond_type: ofz, maturity_max_years: 7 }
          cash:
            buckets:
              cash: { weight: 5, is_cash: true }
        """,
    )
    target = load_target(path)
    rep = next(b for b in target.buckets if b.name == "replaced_2_4")
    assert rep.has_filter
    assert rep.filter_.bond_type == "replaced"
    assert rep.filter_.maturity_min_years == Decimal(2)
    assert rep.filter_.maturity_max_years == Decimal(4)


def test_total_must_be_100(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        base_currency: RUB
        categories:
          stocks:
            buckets:
              a: { weight: 60, tickers: { A: 100 } }
              b: { weight: 30, tickers: { B: 100 } }
          cash:
            buckets:
              cash: { weight: 5, is_cash: true }
        """,
    )
    with pytest.raises(TargetConfigError, match="weights sum to 95"):
        load_target(path)


def test_must_have_exactly_one_cash_bucket(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        base_currency: RUB
        categories:
          stocks:
            buckets:
              a: { weight: 100, tickers: { A: 100 } }
        """,
    )
    with pytest.raises(TargetConfigError, match="is_cash"):
        load_target(path)


def test_multiple_cash_buckets_rejected(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        base_currency: RUB
        categories:
          cash:
            buckets:
              c1: { weight: 50, is_cash: true }
              c2: { weight: 50, is_cash: true }
        """,
    )
    with pytest.raises(TargetConfigError, match="is_cash"):
        load_target(path)


def test_duplicate_ticker_across_buckets_rejected(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        base_currency: RUB
        categories:
          stocks:
            buckets:
              a: { weight: 50, tickers: { SBER: 100 } }
              b: { weight: 45, tickers: { SBER: 100 } }
          cash:
            buckets:
              cash: { weight: 5, is_cash: true }
        """,
    )
    with pytest.raises(TargetConfigError, match="more than one bucket"):
        load_target(path)


def test_duplicate_cash_currency_rejected(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        base_currency: RUB
        categories:
          gold:
            buckets:
              gold:    { weight: 10, cash_currencies: [XAU], tickers: { TGLD: 100 } }
              silver:  { weight: 5,  cash_currencies: [XAU] }
          stocks:
            buckets: { a: { weight: 80, tickers: { A: 100 } } }
          cash:
            buckets: { cash: { weight: 5, is_cash: true } }
        """,
    )
    with pytest.raises(TargetConfigError, match="cash_currencies"):
        load_target(path)


def test_unknown_bucket_key_rejected(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        base_currency: RUB
        categories:
          stocks:
            buckets:
              a:
                weight: 95
                tickers: { A: 100 }
                what: yes
          cash:
            buckets: { cash: { weight: 5, is_cash: true } }
        """,
    )
    with pytest.raises(TargetConfigError, match="unknown keys"):
        load_target(path)


def test_ticker_weights_within_bucket_must_sum_to_100(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        base_currency: RUB
        categories:
          stocks:
            buckets:
              a: { weight: 95, tickers: { A: 60, B: 30 } }
          cash:
            buckets: { cash: { weight: 5, is_cash: true } }
        """,
    )
    with pytest.raises(TargetConfigError, match="weights sum to 90"):
        load_target(path)


def test_category_weight_is_derived_from_buckets(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        base_currency: RUB
        categories:
          stocks:
            buckets:
              a: { weight: 35, tickers: { A: 100 } }
              b: { weight: 60, tickers: { B: 100 } }
          cash:
            buckets: { cash: { weight: 5, is_cash: true } }
        """,
    )
    target = load_target(path)
    stocks = target.categories[0]
    assert stocks.portfolio_weight_pct == Decimal(95)
