"""Local CSV/Excel data source implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pandas as pd

from core.data.base import DataSource
from core.data.utils import (
    align_macro_to_trading_dates,
    filter_by_date_range,
    standardize_etf_price,
    standardize_macro_factors,
)
from core.data.validators import (
    validate_etf_price,
    validate_macro_factors,
    validate_price_universe_coverage,
    validate_universe,
)


class CsvDataSource(DataSource):
    """Read ETF prices, macro factors, and universe data from local files."""

    def __init__(
        self,
        etf_price_path: str | Path,
        macro_factors_path: str | Path,
        universe_path: str | Path,
        universe_sheet_name: str | int = 0,
        universe_code_column: str | None = None,
        macro_ffill: bool = True,
    ) -> None:
        self.etf_price_path = Path(etf_price_path)
        self.macro_factors_path = Path(macro_factors_path)
        self.universe_path = Path(universe_path)
        self.universe_sheet_name = universe_sheet_name
        self.universe_code_column = universe_code_column
        self.macro_ffill = macro_ffill

    def get_etf_price(
        self,
        start_date: str | pd.Timestamp | None = None,
        end_date: str | pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        raw = pd.read_csv(self.etf_price_path, encoding="utf-8-sig")
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
        raw = pd.read_csv(self.macro_factors_path, encoding="utf-8-sig")
        data = standardize_macro_factors(raw)
        data = filter_by_date_range(data, start_date, end_date)
        if trading_dates is not None:
            data = align_macro_to_trading_dates(data, trading_dates, ffill=self.macro_ffill)
        elif self.macro_ffill:
            data = data.ffill()
        validate_macro_factors(data)
        return data

    def get_universe(self) -> list[str]:
        data = pd.read_excel(self.universe_path, sheet_name=self.universe_sheet_name)
        data.columns = [str(column).strip() for column in data.columns]
        code_column = self.universe_code_column or self._infer_universe_code_column(data)
        universe = data[code_column].dropna().astype(str).str.strip().str.upper().tolist()
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

    @staticmethod
    def _infer_universe_code_column(data: pd.DataFrame) -> str:
        for column in data.columns:
            normalized = str(column).replace("\n", "").replace(" ", "")
            if normalized in {"证券代码", "代码", "sec", "symbol"}:
                return column
        return data.columns[0]

