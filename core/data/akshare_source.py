"""AkShare data source adapter for ETF price and macro data.

ETF price uses fund_etf_hist_sina (Sina source, stable and long history).
Macro data adapters are implemented field by field since AkShare function
signatures vary by endpoint and version.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd

from core.data.base import DataSource


class AkShareDataUnavailable(RuntimeError):
    """Raised when AkShare is not installed or a field adapter is not ready."""


def ensure_akshare_available():
    """Import AkShare lazily so local CSV workflows do not require it at import time."""
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:
        raise AkShareDataUnavailable(
            "AkShare is not installed. Install project dependencies before using live data."
        ) from exc
    return ak


class AkShareDataSource(DataSource):
    """AkShare data source implementing the DataSource interface.

    Provides ETF daily OHLCV data via fund_etf_hist_sina (Sina backend).
    Macro factors are fetched field-by-field with explicit adapters.
    """

    def __init__(self) -> None:
        self._universe_cache: list[str] | None = None

    # ── ETF price ────────────────────────────────────────────

    def get_etf_price(
        self,
        start_date: str | pd.Timestamp | None = None,
        end_date: str | pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """Return daily ETF OHLCV data for the default universe."""
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
        """Fetch ETF price data for specific codes from AkShare (Sina source).

        Args:
            sec_codes: list of ETF codes like ["510300.SH", "159915.SZ"]
            start_date: start date (inclusive)
            end_date: end date (inclusive)
            period: only "daily" is supported for now
        """
        if not sec_codes:
            return pd.DataFrame(columns=["date", "sec", "open", "high", "low", "close", "volume", "amount"])

        if period not in ("daily", "d"):
            return pd.DataFrame(columns=["date", "sec", "open", "high", "low", "close", "volume", "amount"])

        ak = ensure_akshare_available()

        all_frames: list[pd.DataFrame] = []
        for sec_code in sec_codes:
            try:
                sina_code = self._to_sina_code(sec_code)
                df = ak.fund_etf_hist_sina(symbol=sina_code)
                if df.empty:
                    continue

                df = df.rename(columns={
                    "date": "date",
                    "open": "open",
                    "high": "high",
                    "low": "low",
                    "close": "close",
                    "volume": "volume",
                    "amount": "amount",
                })
                df["sec"] = sec_code
                df["date"] = pd.to_datetime(df["date"])

                # Filter by date range
                if start_date is not None:
                    df = df[df["date"] >= pd.Timestamp(start_date)]
                if end_date is not None:
                    df = df[df["date"] <= pd.Timestamp(end_date)]

                if not df.empty:
                    all_frames.append(df[["date", "sec", "open", "high", "low", "close", "volume", "amount"]])
            except Exception:
                continue

        if not all_frames:
            return pd.DataFrame(columns=["date", "sec", "open", "high", "low", "close", "volume", "amount"])

        result = pd.concat(all_frames, ignore_index=True)
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            result[col] = pd.to_numeric(result[col], errors="coerce")

        return result.sort_values(["date", "sec"]).reset_index(drop=True)

    @staticmethod
    def _to_sina_code(sec_code: str) -> str:
        """Convert 510300.SH -> sh510300 for Sina ETF API."""
        code, market = sec_code.split(".")
        return f"{market.lower()}{code}"

    # ── Macro factors ────────────────────────────────────────

    # Field name -> fetcher method (must match webapp macro_service field names)
    _DAILY_FETCHERS: dict[str, str] = {
        "shibor_3m": "_fetch_shibor_3m",
        "fr007": "_fetch_fr007",
        "cn_gov_1y": "_fetch_cn_gov_1y",
        "cn_gov_10y": "_fetch_cn_gov_10y",
        "usd_cny": "_fetch_usd_cny",
        "copper": "_fetch_copper",
        "gold": "_fetch_gold",
        "rebar": "_fetch_rebar",
        "csi300_pe": "_fetch_csi300_pe",
        "csi1000_pe": "_fetch_csi1000_pe",
        "qvix_300etf": "_fetch_qvix",
        "spx": "_fetch_spx",
        "ixic": "_fetch_ixic",
        "hsi": "_fetch_hsi",
    }

    def get_macro_factors(
        self,
        start_date: str | pd.Timestamp | None = None,
        end_date: str | pd.Timestamp | None = None,
        trading_dates: Sequence[pd.Timestamp] | None = None,
    ) -> pd.DataFrame:
        """Fetch all available daily macro factors from AkShare.

        Returns a DataFrame indexed by date with one column per field.
        Fields that fail to fetch are silently skipped.
        """
        ak = ensure_akshare_available()
        frames: dict[str, pd.Series] = {}

        # Bond yields are fetched together in one pass (both tenors share a request)
        try:
            bond_yields = self._fetch_bond_yields(ak)
            for name, series in bond_yields.items():
                if series is not None and not series.empty:
                    frames[name] = series
        except Exception:
            pass

        for name, method_name in self._DAILY_FETCHERS.items():
            if name in ("cn_gov_1y", "cn_gov_10y"):
                continue  # already fetched above
            fetcher = getattr(self, method_name, None)
            if fetcher is None:
                continue
            try:
                series = fetcher(ak)
                if series is not None and not series.empty:
                    frames[name] = series
            except Exception:
                continue

        if not frames:
            return pd.DataFrame()

        df = pd.DataFrame(frames)
        df.index = pd.to_datetime(df.index)

        if start_date is not None:
            df = df[df.index >= pd.Timestamp(start_date)]
        if end_date is not None:
            df = df[df.index <= pd.Timestamp(end_date)]

        return df.sort_index()

    # ── Individual macro field adapters ──────────────────────

    def _fetch_shibor_3m(self, ak) -> pd.Series:
        df = ak.rate_interbank(
            market="上海银行同业拆借市场",
            symbol="Shibor人民币",
            indicator="3月",
        )
        df = df.rename(columns={"报告日": "date", "利率": "value"})
        df["date"] = pd.to_datetime(df["date"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df.set_index("date")["value"]

    def _fetch_fr007(self, ak) -> pd.Series:
        """Fetch FR007 repo rate.

        repo_rate_hist only returns ~1 month per call and fails on large
        ranges, so we fetch month by month (in parallel) and concatenate.
        """
        start = pd.Timestamp("2021-01-01")
        end = pd.Timestamp.now()

        # Build month windows
        windows: list[tuple[str, str]] = []
        cursor = start
        while cursor <= end:
            month_end = cursor + pd.offsets.MonthEnd(0)
            windows.append((cursor.strftime("%Y%m%d"), month_end.strftime("%Y%m%d")))
            cursor = month_end + pd.Timedelta(days=1)

        from concurrent.futures import ThreadPoolExecutor

        def fetch_one(win: tuple[str, str]) -> pd.Series:
            s, e = win
            try:
                df = ak.repo_rate_hist(start_date=s, end_date=e)
                if df.empty:
                    return pd.Series(dtype=float)
                df["date"] = pd.to_datetime(df["date"])
                df["FR007"] = pd.to_numeric(df["FR007"], errors="coerce")
                return df.set_index("date")["FR007"].dropna()
            except Exception:
                return pd.Series(dtype=float)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(fetch_one, windows))

        all_frames = [s for s in results if not s.empty]
        if not all_frames:
            return pd.Series(dtype=float)
        result = pd.concat(all_frames)
        return result[~result.index.duplicated(keep="last")].sort_index()

    def _fetch_cn_gov_1y(self, ak) -> pd.Series:
        return self._fetch_bond_yield(ak, "1年")

    def _fetch_cn_gov_10y(self, ak) -> pd.Series:
        return self._fetch_bond_yield(ak, "10年")

    def _fetch_bond_yields(self, ak) -> dict[str, pd.Series]:
        """Fetch both 1Y and 10Y treasury yields in one pass.

        bond_china_yield returns all tenors per request, so we request
        once per year chunk and split columns afterwards.
        """
        all_dfs = []
        start_year = 2020
        end_year = pd.Timestamp.now().year
        for year in range(start_year, end_year + 1):
            try:
                df = ak.bond_china_yield(
                    start_date=f"{year}0101",
                    end_date=f"{year}1231",
                )
                if df.empty:
                    continue
                df = df[df["曲线名称"] == "中债国债收益率曲线"]
                if df.empty:
                    continue
                all_dfs.append(df[["日期", "1年", "10年"]].copy())
            except Exception:
                continue

        out: dict[str, pd.Series] = {}
        if not all_dfs:
            return out

        df = pd.concat(all_dfs, ignore_index=True)
        df["日期"] = pd.to_datetime(df["日期"])
        df = df.drop_duplicates(subset=["日期"], keep="last").sort_values("日期")

        for col, key in (("1年", "cn_gov_1y"), ("10年", "cn_gov_10y")):
            series = pd.to_numeric(df[col], errors="coerce")
            out[key] = pd.Series(series.values, index=df["日期"]).dropna()

        return out

    def _fetch_bond_yield(self, ak, tenor_col: str) -> pd.Series:
        """Fetch China government bond yield for a given tenor (single field)."""
        yields = self._fetch_bond_yields(ak)
        return yields.get("cn_gov_1y" if tenor_col == "1年" else "cn_gov_10y", pd.Series(dtype=float))

    def _fetch_usd_cny(self, ak) -> pd.Series:
        # currency_boc_safe: full history from 1994, one request, ~7s
        df = ak.currency_boc_safe()
        if "日期" not in df.columns or "美元" not in df.columns:
            return pd.Series(dtype=float)
        df["date"] = pd.to_datetime(df["日期"])
        df["value"] = pd.to_numeric(df["美元"], errors="coerce")
        # Column is 100 USD -> CNY, convert to USD/CNY
        df["value"] = df["value"] / 100
        return df.set_index("date")["value"].sort_index()

    def _fetch_copper(self, ak) -> pd.Series:
        return self._fetch_futures_main(ak, "CU0")

    def _fetch_gold(self, ak) -> pd.Series:
        return self._fetch_futures_main(ak, "AU0")

    def _fetch_rebar(self, ak) -> pd.Series:
        return self._fetch_futures_main(ak, "RB0")

    def _fetch_futures_main(self, ak, symbol: str) -> pd.Series:
        df = ak.futures_main_sina(symbol=symbol)
        df = df.rename(columns={"日期": "date", "收盘价": "value"})
        df["date"] = pd.to_datetime(df["date"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df.set_index("date")["value"].sort_index()

    def _fetch_csi300_pe(self, ak) -> pd.Series:
        return self._fetch_index_pe(ak, "000300")

    def _fetch_csi1000_pe(self, ak) -> pd.Series:
        return self._fetch_index_pe(ak, "000852")

    def _fetch_index_pe(self, ak, index_code: str) -> pd.Series:
        df = ak.stock_zh_index_hist_csindex(symbol=index_code)
        df = df.rename(columns={"日期": "date", "滚动市盈率": "value"})
        df["date"] = pd.to_datetime(df["date"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df.set_index("date")["value"].sort_index()

    def _fetch_qvix(self, ak) -> pd.Series:
        df = ak.index_option_300etf_qvix()
        df["date"] = pd.to_datetime(df["date"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["close"])
        return df.set_index("date")["close"].sort_index()

    def _fetch_spx(self, ak) -> pd.Series:
        df = ak.index_us_stock_sina(symbol=".INX")
        df["date"] = pd.to_datetime(df["date"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return df.set_index("date")["close"].sort_index()

    def _fetch_ixic(self, ak) -> pd.Series:
        df = ak.index_us_stock_sina(symbol=".IXIC")
        df["date"] = pd.to_datetime(df["date"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return df.set_index("date")["close"].sort_index()

    def _fetch_hsi(self, ak) -> pd.Series:
        df = ak.stock_hk_index_daily_sina(symbol="HSI")
        df["date"] = pd.to_datetime(df["date"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return df.set_index("date")["close"].sort_index()

    # ── Universe ─────────────────────────────────────────────

    def get_universe(self) -> list[str]:
        """Return default ETF universe (same curated list as Baostock)."""
        if self._universe_cache is not None:
            return self._universe_cache
        self._universe_cache = [
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
        return self._universe_cache

