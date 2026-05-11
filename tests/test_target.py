from decimal import Decimal
from pathlib import Path

import pytest

from target import TargetConfigError, load_target


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "target.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_happy_path(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        base_currency: RUB
        categories:
          stocks:
            weight: 60
            tickers:
              SBER: 40
              LKOH: 30
              YNDX: 30
          bonds:
            weight: 30
            tickers:
              SU26240RMFS0: 100
          gold:
            weight: 10
            tickers:
              TGLD: 100
        """,
    )
    target = load_target(path)
    assert target.base_currency == "RUB"
    assert [c.name for c in target.categories] == ["stocks", "bonds", "gold"]
    assert target.tickers == ["SBER", "LKOH", "YNDX", "SU26240RMFS0", "TGLD"]
    sber = next(leaf for leaf in target.leaves if leaf.ticker == "SBER")
    assert sber.portfolio_weight_pct == Decimal("24")  # 60 * 40 / 100


def test_category_sum_not_100(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        base_currency: RUB
        categories:
          stocks:
            weight: 60
            tickers: { SBER: 100 }
          bonds:
            weight: 39
            tickers: { SU26240RMFS0: 100 }
        """,
    )
    with pytest.raises(TargetConfigError, match="Category weights sum to 99"):
        load_target(path)


def test_ticker_sum_not_100(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        base_currency: RUB
        categories:
          stocks:
            weight: 100
            tickers:
              SBER: 60
              LKOH: 30
        """,
    )
    with pytest.raises(TargetConfigError, match="sum to 90"):
        load_target(path)


def test_empty_tickers(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        base_currency: RUB
        categories:
          stocks:
            weight: 100
            tickers: {}
        """,
    )
    with pytest.raises(TargetConfigError, match="non-empty"):
        load_target(path)


def test_unknown_currency(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        base_currency: GBP
        categories:
          stocks:
            weight: 100
            tickers: { SBER: 100 }
        """,
    )
    with pytest.raises(TargetConfigError, match="base_currency"):
        load_target(path)


def test_duplicate_ticker_across_categories(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        base_currency: RUB
        categories:
          stocks:
            weight: 50
            tickers: { SBER: 100 }
          bonds:
            weight: 50
            tickers: { SBER: 100 }
        """,
    )
    with pytest.raises(TargetConfigError, match="more than one category"):
        load_target(path)
