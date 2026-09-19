"""AkShare data source adapter for ETF price and macro data.

ETF price uses the Tencent fqkline 后复权(hfq) endpoint as the primary source
(with Sina ``fund_etf_hist_sina`` as fallback). Macro data adapters are
implemented field by field since AkShare function signatures vary by endpoint
and version.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Sequence

import pandas as pd

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

from core.data.base import DataSource
from core.data.default_universe import DEFAULT_ACTIVE_CODES

logger = logging.getLogger(__name__)


class AkShareDataUnavailable(RuntimeError):
    """Raised when AkShare is not installed or a field adapter is not ready."""


class TencentSourceError(RuntimeError):
    """腾讯行情接口不可用（网络失败 / WAF 拦截 / 非法响应），向上透传失败原因。"""


# 模拟浏览器请求头：requests 默认 UA 是典型爬虫特征，容易被 WAF 识别。
_TENCENT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://gu.qq.com/",
}

# 重试退避序列（秒）：WAF 拦截多为频率风控，固定短间隔快速重试只会加剧拦截。
_TENCENT_RETRY_DELAYS = (1.0, 2.0, 4.0)

# 全局请求间隔区间（秒）：跨实例、跨线程生效，每次请求在区间内均匀抖动。
# 均值 1.0s ≈ 60 请求/分钟，压在 WAF 风控阈值（~100 请求/分钟，估计值）之下。
_TENCENT_INTERVAL_RANGE = (0.75, 1.25)

_throttle_lock = threading.Lock()
_next_request_at = 0.0


def set_tencent_interval_range(low: float, high: float) -> None:
    """装配层按配置覆盖全局节流区间（节拍为全进程共享，故为模块级 setter）。"""
    global _TENCENT_INTERVAL_RANGE
    if low <= 0 or high < low:
        raise ValueError(f"非法的腾讯请求间隔区间: ({low}, {high})")
    _TENCENT_INTERVAL_RANGE = (float(low), float(high))


def _throttle() -> None:
    """串行化腾讯请求并保持全局抖动间隔（持锁 sleep，多任务共享一个节拍）。"""
    global _next_request_at
    with _throttle_lock:
        now = time.monotonic()
        wait = _next_request_at - now
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _next_request_at = max(now, _next_request_at) + random.uniform(*_TENCENT_INTERVAL_RANGE)


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

    Provides ETF daily OHLCV data via the Tencent fqkline 后复权 endpoint
    (primary), falling back to Sina ``fund_etf_hist_sina``. Macro factors are
    fetched field-by-field with explicit adapters.
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
        """Fetch ETF price data for specific codes from AkShare.

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
            # BUG-05: 后复权(hfq)是唯一允许入库的口径；禁止回退其他数据源
            # （Sina 等）。响应内缺 hfqday 的 day 回退（无复权事件证券的
            # 常态，见 _tencent_get）仍被接受，但会显式告警并以
            # source="akshare(hfq=day)" 打标，口径可识别、不静默混作 hfq。
            try:
                df = self._fetch_hfq_price(ak, sec_code, start_date, end_date)
            except TencentSourceError:
                # WAF 拦截/网络故障是 IP 级问题，继续请求剩余标的只会加剧
                # 风控——快速失败并把原因透传给上层展示。
                raise
            if not df.empty:
                all_frames.append(df)

        if not all_frames:
            return pd.DataFrame(columns=["date", "sec", "open", "high", "low", "close", "volume", "amount"])

        result = pd.concat(all_frames, ignore_index=True)
        numeric_cols = ["open", "high", "low", "close", "volume", "amount"]
        for col in numeric_cols:
            result[col] = pd.to_numeric(result[col], errors="coerce")

        return result.sort_values(["date", "sec"]).reset_index(drop=True)

    @staticmethod
    def _to_sina_code(sec_code: str) -> str:
        """Convert 510300.SH -> sh510300 for Sina/Tencent ETF API."""
        code, market = sec_code.split(".")
        return f"{market.lower()}{code}"

    def _fetch_hfq_price(
        self,
        ak,
        sec_code: str,
        start_date: str | pd.Timestamp | None,
        end_date: str | pd.Timestamp | None,
    ) -> pd.DataFrame:
        """Fetch 后复权(hfq) OHLCV from the Tencent fqkline endpoint.

        Tencent is the only reachable source that correctly adjusts for ETF
        share splits in this deployment's network (Eastmoney is blocked;
        baostock's adjust flag is silently ignored for ETFs and would leave
        split cliffs). The endpoint caps each request at 640 rows (~2.6
        years of trading days), so history is fetched in multi-year
        segments; only the hfq series is fetched (no unadjusted series or
        derived adjustment factor).
        """
        hs_code = self._to_sina_code(sec_code)
        start = pd.Timestamp(start_date).strftime("%Y-%m-%d") if start_date else "1990-01-01"
        end = pd.Timestamp(end_date).strftime("%Y-%m-%d") if end_date else pd.Timestamp.now().strftime("%Y-%m-%d")
        if start[:4] > end[:4]:
            return pd.DataFrame()

        hfq_rows, hfq_fallback = self._tencent_fetch_range(hs_code, start, end, "hfq")
        if not hfq_rows:
            return pd.DataFrame()

        df = pd.DataFrame([self._tencent_row_map(r, sec_code) for r in hfq_rows])
        df = df.drop_duplicates(subset="date", keep="last")
        df = df.sort_values("date").reset_index(drop=True)

        # F02: 响应缺 hfqday 而回退 day（未复权价）时打可区分标记，口径
        # 不再与真 hfq 行不可区分；一旦证券发生复权事件，后续 hfqday 重拉
        # 会经 upsert 覆盖这些行，序列回归自洽。
        df["source"] = "akshare(hfq=day)" if hfq_fallback else "akshare"
        return self._filter_by_date(df, start_date, end_date)

    @classmethod
    def _tencent_row_map(cls, row: list, sec_code: str) -> dict:
        """Map a Tencent kline row [date, open, close, high, low, vol, ...]."""
        return {
            "date": pd.Timestamp(row[0]),
            "sec": sec_code,
            "open": float(row[1]),
            "close": float(row[2]),
            "high": float(row[3]),
            "low": float(row[4]),
            "volume": float(row[5]) if len(row) > 5 else 0.0,
            "amount": float(row[6]) if len(row) > 6 and row[6] else 0.0,
        }

    # 单请求上限 640 行 ≈ 2.6 年交易日，按 2 年分段保证不截断。
    _SEGMENT_YEARS = 2

    @staticmethod
    def _tencent_fetch_range(
        hs_code: str,
        start: str,
        end: str,
        fq: str,
        retries: int = 3,
    ) -> tuple[list[list], bool]:
        """Fetch kline rows for a date range, split into multi-year segments.

        Returns ``(rows, fell_back)``：任一分段发生口径回退（F02，见
        ``_tencent_get``）即 ``fell_back=True``，供调用方对整批行打标。
        """
        if requests is None:
            return [], False
        out: list[list] = []
        any_fallback = False
        start_year = int(start[:4])
        end_year = int(end[:4])
        year = start_year
        while year <= end_year:
            seg_end_year = min(year + AkShareDataSource._SEGMENT_YEARS - 1, end_year)
            seg_start = f"{year}-01-01"
            seg_end = f"{seg_end_year}-12-31" if seg_end_year < end_year else end
            rows, fell_back = AkShareDataSource._tencent_get(hs_code, seg_start, seg_end, fq, retries)
            out.extend(rows)
            any_fallback = any_fallback or fell_back
            year = seg_end_year + 1
        return out, any_fallback

    @staticmethod
    def _tencent_get(hs_code: str, start: str, end: str, fq: str, retries: int = 3) -> tuple[list[list], bool]:
        """Fetch one kline segment with browser headers, throttling, and
        exponential backoff.

        Returns ``(rows, fell_back)``：响应缺少请求口径字段而回退 day 时为
        True（F02；正常应答不重试，直接返回）。

        WAF 拦截（HTTP 501 / 拦截页）为分钟级 IP 封禁，重试无益且可能加剧，
        立即抛 ``TencentSourceError``；网络异常 / 非法 JSON / 其他非 200
        瞬时错误按 (1.0, 2.0, 4.0) 退避重试，耗尽抛 ``TencentSourceError``
        （携带最后一次失败原因，供同步任务展示）。接口正常应答（含空数据）
        不重试，直接返回。
        """
        url = (
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param={hs_code},day,{start},{end},640,{fq}"
        )
        last_error: str | None = None
        for attempt in range(retries):
            if attempt:
                delay = _TENCENT_RETRY_DELAYS[min(attempt - 1, len(_TENCENT_RETRY_DELAYS) - 1)]
                time.sleep(delay)
            _throttle()
            try:
                resp = requests.get(url, headers=_TENCENT_HEADERS, timeout=20)
            except Exception as exc:
                last_error = f"网络请求失败: {type(exc).__name__}: {exc}"
                continue
            if resp.status_code == 501 or "waf.tencent.com" in resp.text[:500]:
                raise TencentSourceError(
                    f"腾讯行情接口被 WAF 拦截(HTTP {resp.status_code})，IP 封禁为分钟级，请稍后重试"
                )
            if resp.status_code != 200:
                last_error = f"接口返回异常(HTTP {resp.status_code})"
                continue
            try:
                entry = resp.json().get("data", {}).get(hs_code, {})
            except ValueError:
                last_error = "响应不是合法 JSON（疑似 WAF 拦截页）"
                continue
            if isinstance(entry, list):
                entry = {}
            key = "hfqday" if fq == "hfq" else ("qfqday" if fq == "qfq" else "day")
            fell_back = False
            if key not in entry:
                # F02: hfq/qfq 请求缺目标字段时回退 day（未复权口径）——经
                # 实证，无复权事件证券（如货币 ETF 511990）腾讯本就只返回
                # day，属常态而非故障；但不得静默混作 hfq 入库，实际以 day
                # 行替代时必须告警并由 _fetch_hfq_price 打可区分的 source
                # 标记。标的关键整体缺失（如未上市）无行可返，不算回退。
                key = "day"
                fell_back = fq != "day" and bool(entry.get("day"))
                if fell_back:
                    logger.warning(
                        "腾讯响应缺少 %s 字段，回退 day（未复权）口径："
                        "sec=%s range=[%s, %s]（常见于无复权事件证券，hfq≡day）",
                        "hfqday" if fq == "hfq" else "qfqday", hs_code, start, end,
                    )
            return entry.get(key) or [], fell_back
        raise TencentSourceError(
            f"腾讯行情接口连续 {retries} 次请求失败：{last_error}"
        )

    @staticmethod
    def _filter_by_date(
        df: pd.DataFrame,
        start_date: str | pd.Timestamp | None,
        end_date: str | pd.Timestamp | None,
    ) -> pd.DataFrame:
        if start_date is not None:
            df = df[df["date"] >= pd.Timestamp(start_date)]
        if end_date is not None:
            df = df[df["date"] <= pd.Timestamp(end_date)]
        return df

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
        """Return the shared default ETF universe (same single source as the
        webapp seed pool — see core.data.default_universe)."""
        if self._universe_cache is not None:
            return self._universe_cache
        self._universe_cache = list(DEFAULT_ACTIVE_CODES)
        return self._universe_cache

