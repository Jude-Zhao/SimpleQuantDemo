"""Cached data source wrapper.

Wraps a primary (and optional secondary) data source with read-through
caching. Cache read/write functions are injected so the core layer
does not depend on any ORM or webapp code.
"""

from __future__ import annotations

from typing import Callable

import pandas as pd

from core.data.base import DataSource


class CachedDataSource(DataSource):
    """Data source wrapper with read-through caching.

    Args:
        primary_source: Main data source to fetch from on cache miss.
        secondary_source: Fallback data source if primary fails.
        cache_reader: Callable(sec_codes, start_date, end_date, period) -> DataFrame
            Returns cached data (or empty DataFrame if no cache).
        cache_writer: Callable(df, period) -> None
            Writes fresh data into the cache.
    """

    def __init__(
        self,
        primary_source: DataSource,
        secondary_source: DataSource | None = None,
        cache_reader: Callable | None = None,
        cache_writer: Callable | None = None,
    ) -> None:
        self.primary = primary_source
        self.secondary = secondary_source
        self.cache_reader = cache_reader
        self.cache_writer = cache_writer

    def get_etf_price(
        self,
        start_date: str | pd.Timestamp | None = None,
        end_date: str | pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """Return daily ETF price data (cached)."""
        return self.get_etf_price_by_codes(
            sec_codes=self.get_universe(),
            start_date=start_date,
            end_date=end_date,
            period="daily",
        )

    def get_etf_price_by_codes(
        self,
        sec_codes: list[str],
        start_date: str | pd.Timestamp | None = None,
        end_date: str | pd.Timestamp | None = None,
        period: str = "daily",
    ) -> pd.DataFrame:
        """Fetch ETF price data with caching.

        Tries cache first. On miss (or partial miss), fetches from the
        primary source (falling back to secondary), then writes to cache.
        """
        if not sec_codes:
            return pd.DataFrame(columns=["date", "sec", "open", "high", "low", "close", "volume", "amount"])

        # 1. Try cache
        cached = pd.DataFrame()
        if self.cache_reader is not None:
            cached = self.cache_reader(sec_codes, start_date, end_date, period)
            if cached is None:
                cached = pd.DataFrame()

        # Check which codes are fully covered by cache
        if not cached.empty:
            cached_codes = set(cached["sec"].unique())
            missing_codes = [c for c in sec_codes if c not in cached_codes]
            if not missing_codes:
                return cached.sort_values(["date", "sec"]).reset_index(drop=True)
            fetch_codes = missing_codes
        else:
            fetch_codes = list(sec_codes)

        # 2. Fetch from primary source
        fresh_df = self._fetch_from_source(
            self.primary, fetch_codes, start_date, end_date, period, "primary"
        )

        # 3. Fallback to secondary if primary failed
        if fresh_df.empty and self.secondary is not None:
            fresh_df = self._fetch_from_source(
                self.secondary, fetch_codes, start_date, end_date, period, "secondary"
            )

        # 4. Write to cache
        if self.cache_writer is not None and not fresh_df.empty:
            self.cache_writer(fresh_df, period)

        # 5. Merge cached + fresh data
        if cached.empty:
            result = fresh_df
        elif fresh_df.empty:
            result = cached
        else:
            result = pd.concat([cached, fresh_df], ignore_index=True)
            result = result.drop_duplicates(subset=["date", "sec"], keep="last")

        return result.sort_values(["date", "sec"]).reset_index(drop=True)

    def _fetch_from_source(
        self,
        source: DataSource,
        sec_codes: list[str],
        start_date: str | pd.Timestamp | None,
        end_date: str | pd.Timestamp | None,
        period: str,
        source_name: str,
    ) -> pd.DataFrame:
        """Try to fetch data from a source. Returns empty DataFrame on failure."""
        try:
            if hasattr(source, "get_etf_price_by_codes"):
                df = source.get_etf_price_by_codes(
                    sec_codes=sec_codes,
                    start_date=start_date,
                    end_date=end_date,
                    period=period,
                )
            else:
                # Fall back to get_etf_price (daily only, full universe)
                df = source.get_etf_price(start_date=start_date, end_date=end_date)
                if not df.empty:
                    df = df[df["sec"].isin(sec_codes)]
            return df
        except Exception:
            return pd.DataFrame()

    def get_macro_factors(
        self,
        start_date: str | pd.Timestamp | None = None,
        end_date: str | pd.Timestamp | None = None,
        trading_dates=None,
    ) -> pd.DataFrame:
        """Macro factors pass through to primary.

        The webapp caches macro data separately in macro_daily /
        macro_monthly tables via webapp.services.macro_service.
        """
        return self.primary.get_macro_factors(start_date, end_date, trading_dates)

    def get_universe(self) -> list[str]:
        """Return universe from primary source."""
        return self.primary.get_universe()
