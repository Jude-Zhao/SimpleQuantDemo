"""Dedicated tests for the SqliteDataSource adapter."""

from __future__ import annotations

import re

import pandas as pd
import pytest

from core.data.exceptions import DataValidationError
from core.data.sqlite_source import SqliteDataSource

SECURITY_CODE_PATTERN = re.compile(r"^\d{6}\.(SH|SZ)$")


def test_get_universe_returns_active_codes(sqlite_source: SqliteDataSource) -> None:
    universe = sqlite_source.get_universe()

    assert len(universe) == 12
    assert len(set(universe)) == len(universe)
    assert all(SECURITY_CODE_PATTERN.match(code) for code in universe)


def test_get_etf_price_columns_and_full_range(sqlite_source: SqliteDataSource) -> None:
    df = sqlite_source.get_etf_price()

    assert list(df.columns) == ["date", "sec", "open", "high", "low", "close", "volume", "amount"]
    assert df["sec"].nunique() == 12
    assert df["date"].min() == pd.Timestamp("2024-01-01")
    assert df["date"].max() == pd.Timestamp("2026-03-13")


def test_get_etf_price_date_filter(sqlite_source: SqliteDataSource) -> None:
    df = sqlite_source.get_etf_price(start_date="2024-03-04", end_date="2024-03-08")

    assert df["date"].nunique() == 5
    assert df["date"].min() == pd.Timestamp("2024-03-04")
    assert df["date"].max() == pd.Timestamp("2024-03-08")


def test_get_macro_factors_returns_daily_wide_table(sqlite_source: SqliteDataSource) -> None:
    df = sqlite_source.get_macro_factors()

    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.name == "date"
    assert "shibor_3m" in df.columns
    assert "hsi" in df.columns
    assert df.index.min() == pd.Timestamp("2024-01-01")
    assert df.index.max() == pd.Timestamp("2026-03-13")


def test_load_all_consistent(sqlite_source: SqliteDataSource) -> None:
    price_data, macro_data, universe = sqlite_source.load_all()

    assert sorted(price_data["sec"].unique()) == sorted(universe)
    assert macro_data.shape[0] == price_data["date"].nunique()


def test_load_all_price_universe_mismatch_raises(tmp_path) -> None:
    from tests.conftest import create_test_db

    db_path = tmp_path / "mismatch.db"
    create_test_db(db_path)

    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO universe_items (id, sec_code, sec_name, is_active) "
            "VALUES (999, '159928.SZ', 'extra', 1)"
        )

    source = SqliteDataSource(db_path)
    with pytest.raises(DataValidationError):
        source.load_all()