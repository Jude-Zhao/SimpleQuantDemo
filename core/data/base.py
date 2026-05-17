"""Data source abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

import pandas as pd


class DataSource(ABC):
    """Abstract base class for ETF price, macro factor, and universe data."""

    @abstractmethod
    def get_etf_price(
        self,
        start_date: str | pd.Timestamp | None = None,
        end_date: str | pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """Return ETF OHLCV data with columns date/sec/open/high/low/close/volume/amount."""

    @abstractmethod
    def get_macro_factors(
        self,
        start_date: str | pd.Timestamp | None = None,
        end_date: str | pd.Timestamp | None = None,
        trading_dates: Sequence[pd.Timestamp] | None = None,
    ) -> pd.DataFrame:
        """Return daily macro data indexed by date."""

    @abstractmethod
    def get_universe(self) -> list[str]:
        """Return ETF security codes such as 510300.SH."""

