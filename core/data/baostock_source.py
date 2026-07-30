"""Baostock data source adapter for ETF daily and minute bar data."""

from __future__ import annotations

from typing import Sequence

import pandas as pd

from core.data.base import DataSource


class BaostockDataSource(DataSource):
    """Baostock ETF data source.

    Supports daily and minute-bar data. Macro factors and universe
    are not provided by baostock and return empty/default values.
    """

    def __init__(self) -> None:
        self._logged_in = False

    def _ensure_login(self) -> None:
        """Lazy login to baostock."""
        if self._logged_in:
            return
        import baostock as bs  # type: ignore

        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"Baostock login failed: {lg.error_msg}")
        self._logged_in = True

    @staticmethod
    def _convert_to_bs_code(sec_code: str) -> str:
        """Convert 510300.SH -> sh.510300."""
        code, market = sec_code.split(".")
        return f"{market.lower()}.{code}"

    def get_etf_price(
        self,
        start_date: str | pd.Timestamp | None = None,
        end_date: str | pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """Return daily ETF OHLCV data for the default universe.

        Uses the built-in default ETF list since baostock does not
        provide an ETF listing endpoint.
        """
        universe = self.get_universe()
        return self.get_etf_price_by_codes(
            sec_codes=universe,
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
        """Fetch ETF price data for specific codes.

        Args:
            sec_codes: list of ETF codes like ["510300.SH", "159915.SZ"]
            start_date: start date (inclusive)
            end_date: end_date (inclusive)
            period: "daily", "5m", "15m", "30m", "60m"
        """
        if not sec_codes:
            return pd.DataFrame(columns=["date", "sec", "open", "high", "low", "close", "volume", "amount"])

        self._ensure_login()
        import baostock as bs  # type: ignore

        # Map period to baostock frequency parameter
        freq_map = {
            "daily": "d",
            "d": "d",
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "60m": "60",
        }
        frequency = freq_map.get(period, "d")

        start_str = pd.Timestamp(start_date).strftime("%Y-%m-%d") if start_date else "2010-01-01"
        end_str = pd.Timestamp(end_date).strftime("%Y-%m-%d") if end_date else pd.Timestamp.now().strftime("%Y-%m-%d")

        fields = "date,code,open,high,low,close,volume,amount"

        all_frames: list[pd.DataFrame] = []
        for sec_code in sec_codes:
            bs_code = self._convert_to_bs_code(sec_code)
            rs = bs.query_history_k_data_plus(
                bs_code,
                fields,
                start_date=start_str,
                end_date=end_str,
                frequency=frequency,
                adjustflag="2",  # 前复权
            )
            if rs.error_code != "0":
                continue

            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                continue

            df = pd.DataFrame(rows, columns=fields.split(","))
            df["sec"] = sec_code
            all_frames.append(df)

        if not all_frames:
            return pd.DataFrame(columns=["date", "sec", "open", "high", "low", "close", "volume", "amount"])

        result = pd.concat(all_frames, ignore_index=True)

        # Type conversion
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            result[col] = pd.to_numeric(result[col], errors="coerce")

        result["date"] = pd.to_datetime(result["date"])

        # Standardize column order (drop baostock code column)
        result = result[["date", "sec", "open", "high", "low", "close", "volume", "amount"]]
        return result.sort_values(["date", "sec"]).reset_index(drop=True)

    def get_macro_factors(
        self,
        start_date: str | pd.Timestamp | None = None,
        end_date: str | pd.Timestamp | None = None,
        trading_dates: Sequence[pd.Timestamp] | None = None,
    ) -> pd.DataFrame:
        """Baostock does not provide macro factor data. Returns empty DataFrame."""
        return pd.DataFrame()

    def get_universe(self) -> list[str]:
        """Return a default list of ETF codes.

        Baostock does not have an ETF listing API, so we return a
        curated list of major ETFs.
        """
        return [
            "510300.SH",  # 沪深300ETF
            "510500.SH",  # 中证500ETF
            "159915.SZ",  # 创业板ETF
            "518880.SH",  # 黄金ETF
            "511010.SH",  # 国债ETF
            "510050.SH",  # 上证50ETF
            "159919.SZ",  # 沪深300ETF（嘉实）
            "510180.SH",  # 上证180ETF
            "159901.SZ",  # 深100ETF
            "510880.SH",  # 红利ETF
        ]
