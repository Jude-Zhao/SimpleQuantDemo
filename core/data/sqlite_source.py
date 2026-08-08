"""SQLite data source implementation.

Reads ETF prices, macro factors, and universe from the project's
SQLite database (``data/simple_quant.db``) using the stdlib ``sqlite3``
so the core layer stays free of any ORM / webapp dependency.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Sequence

import pandas as pd

from core.data.base import DataSource
from core.data.utils import (
    align_macro_to_trading_dates,
    filter_by_date_range,
    normalize_datetime,
    standardize_etf_price,
    standardize_macro_factors,
)
from core.data.validators import (
    validate_etf_price,
    validate_macro_factors,
    validate_price_universe_coverage,
    validate_universe,
)


class SqliteDataSource(DataSource):
    """Read ETF prices, macro factors, and universe from a SQLite database."""

    def __init__(
        self,
        db_path: str | Path,
        macro_ffill: bool = True,
    ) -> None:
        self.db_path = Path(db_path)
        self.macro_ffill = macro_ffill

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    @staticmethod
    def _iso_date(value: str | pd.Timestamp | None) -> str | None:
        normalized = normalize_datetime(value)
        return normalized.strftime("%Y-%m-%d") if normalized is not None else None

    def get_etf_price(
        self,
        start_date: str | pd.Timestamp | None = None,
        end_date: str | pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        sql = (
            "SELECT trade_date AS date, sec_code AS sec, open, high, low, close, "
            "volume, amount FROM etf_daily_bar"
        )
        params: list[object] = []
        conditions: list[str] = []
        start = self._iso_date(start_date)
        end = self._iso_date(end_date)
        if start is not None:
            conditions.append("trade_date >= ?")
            params.append(start)
        if end is not None:
            conditions.append("trade_date <= ?")
            params.append(end)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY trade_date, sec_code"

        with self._connect() as conn:
            raw = pd.read_sql_query(sql, conn, params=params)

        data = standardize_etf_price(raw)
        data = filter_by_date_range(data, start_date, end_date, date_column="date")
        validate_etf_price(data)
        return data.reset_index(drop=True)

    def get_macro_factors(
        self,
        start_date: str | pd.Timestamp | None = None,
        end_date: str | pd.Timestamp | None = None,
        trading_dates: Sequence[pd.Timestamp] | None = None,
    ) -> pd.DataFrame:
        sql = "SELECT trade_date AS date, * FROM macro_daily"
        params: list[object] = []
        conditions: list[str] = []
        start = self._iso_date(start_date)
        end = self._iso_date(end_date)
        if start is not None:
            conditions.append("trade_date >= ?")
            params.append(start)
        if end is not None:
            conditions.append("trade_date <= ?")
            params.append(end)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY trade_date"

        with self._connect() as conn:
            raw = pd.read_sql_query(sql, conn, params=params)

        data = standardize_macro_factors(raw)
        data = filter_by_date_range(data, start_date, end_date)
        if trading_dates is not None:
            data = align_macro_to_trading_dates(data, trading_dates, ffill=self.macro_ffill)
        elif self.macro_ffill:
            data = data.ffill()
        validate_macro_factors(data)
        return data

    def get_universe(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT sec_code FROM universe_items WHERE is_active = 1 ORDER BY sec_code"
            ).fetchall()
        universe = [str(row[0]).strip().upper() for row in rows]
        validate_universe(universe)
        return universe

    def load_all(
        self,
        start_date: str | pd.Timestamp | None = None,
        end_date: str | pd.Timestamp | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
        """Load and validate price, aligned macro data, and universe together."""
        price_data = self.get_etf_price(start_date=start_date, end_date=end_date)
        universe = self.get_universe()
        validate_price_universe_coverage(price_data, universe)

        trading_dates = price_data["date"].drop_duplicates().sort_values().tolist()
        macro_data = self.get_macro_factors(
            start_date=start_date,
            end_date=end_date,
            trading_dates=trading_dates,
        )
        return price_data, macro_data, universe