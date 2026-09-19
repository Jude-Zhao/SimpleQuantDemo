"""Cached data source wrapper.

Wraps a primary data source with read-through caching. Cache read/write
functions are injected so the core layer does not depend on any ORM or
webapp code.
"""

from __future__ import annotations

import logging
from typing import Callable

import pandas as pd

from core.data.base import DataSource

logger = logging.getLogger(__name__)

# 系统支持的行情周期白名单（备用源 baostock 移除后仅保留日线）。
# 非法周期在入口直接拒绝（BUG-06 验收：非法周期报错，不静默降级/吞异常）。
SUPPORTED_PERIODS = frozenset({"daily", "d"})

# 标准结果列：源失败且无缓存时也返回带列的空结果
_RESULT_COLUMNS = ["date", "sec", "open", "high", "low", "close", "volume", "amount"]


class CachedDataSource(DataSource):
    """Data source wrapper with read-through caching.

    Args:
        primary_source: Main data source to fetch from on cache miss.
        cache_reader: Callable(sec_codes, start_date, end_date, period) -> DataFrame
            Returns cached data (or empty DataFrame if no cache).
        cache_writer: Callable(df, period) -> None
            Writes fresh data into the cache.
        cache_latest_date: Callable() -> Timestamp | None
            全库已知最新交易日，供覆盖判定把请求 end 截断到已知最新交易日。
        coverage_reader: Callable(sec_codes, start_date, end_date, period) -> set[str]
            返回 start 侧已有源覆盖证明的证券集合（该证券的源曾被成功请求
            且请求起点早于等于本次 start，见 EtfCacheCoverage）。
        coverage_writer: Callable(sec_codes, start_date, end_date, period) -> None
            成功补拉并写入缓存后记录源侧覆盖区间。
    """

    def __init__(
        self,
        primary_source: DataSource,
        cache_reader: Callable | None = None,
        cache_writer: Callable | None = None,
        cache_latest_date: Callable[[], pd.Timestamp | None] | None = None,
        coverage_reader: Callable | None = None,
        coverage_writer: Callable | None = None,
    ) -> None:
        self.primary = primary_source
        self.cache_reader = cache_reader
        self.cache_writer = cache_writer
        # 全库（跨所有标的）最新交易日查询，供覆盖判定把请求 end 截断到已
        # 知最新交易日（见 _split_by_coverage）；未注入时退化为用本次缓存
        # 帧最大日期近似。start 侧覆盖由 coverage_reader 提供的元数据判定。
        self.cache_latest_date = cache_latest_date
        self.coverage_reader = coverage_reader
        self.coverage_writer = coverage_writer
        # 上一次请求中源侧仍无法补齐的证券（显式不完整状态；完整时为空）
        self.incomplete_codes: list[str] = []

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
        primary source, then writes to cache.
        """
        if period not in SUPPORTED_PERIODS:
            raise ValueError(
                f"不支持的行情周期: {period!r}，支持: {sorted(SUPPORTED_PERIODS)}"
            )
        if not sec_codes:
            return pd.DataFrame(columns=["date", "sec", "open", "high", "low", "close", "volume", "amount"])

        # 1. Try cache
        cached = pd.DataFrame()
        if self.cache_reader is not None:
            cached = self.cache_reader(sec_codes, start_date, end_date, period)
            if cached is None:
                cached = pd.DataFrame()

        # Check which codes are provably covered by the cache (BUG-04):
        # per-security date-range judgment — A being covered never masks B
        # being incomplete.
        covered, uncovered = self._split_by_coverage(
            cached, sec_codes, start_date, end_date, period
        )
        if not uncovered:
            return cached.sort_values(["date", "sec"]).reset_index(drop=True)
        fetch_codes = uncovered

        # 2. Fetch uncovered codes from primary
        fresh_df = self._fetch_from_source(
            self.primary, fetch_codes, start_date, end_date, period, "primary"
        )

        # 2b. 主源仍缺少的证券记录为显式不完整状态（incomplete_codes +
        # 告警日志），不静默当作完整命中
        still_missing = self._codes_missing(fresh_df, fetch_codes)
        self.incomplete_codes = list(still_missing)
        if self.incomplete_codes:
            logger.warning(
                "行情补齐失败：sec=%s range=[%s, %s] period=%s（主源无数据）",
                self.incomplete_codes, start_date, end_date, period,
            )

        # 4. Write to cache
        if not fresh_df.empty and self.cache_writer is not None:
            self.cache_writer(fresh_df, period)
            # F03: 缓存写入成功后记录“源侧已被请求过 [start, end]”的覆盖
            # 元数据；下次请求据此把“上市晚于请求起点”的合法间隔与真正的
            # 历史缺失区分开。元数据写入失败只降级为下次重复补拉。
            if self.coverage_writer is not None:
                try:
                    self.coverage_writer(fetch_codes, start_date, end_date, period)
                except Exception:
                    logger.exception(
                        "覆盖元数据写入失败（不影响本次行情结果）：sec=%s range=[%s, %s]",
                        fetch_codes, start_date, end_date,
                    )

        # 5. Merge cached + fresh data
        if cached.empty and fresh_df.empty:
            # 主源失败且无缓存：返回带标准列的空结果，
            # 不因缺列在排序处抛 KeyError 掩盖真实失败原因
            return pd.DataFrame(columns=_RESULT_COLUMNS)
        if cached.empty:
            result = fresh_df
        elif fresh_df.empty:
            result = cached
        else:
            result = pd.concat([cached, fresh_df], ignore_index=True)
            result = result.drop_duplicates(subset=["date", "sec"], keep="last")

        return result.sort_values(["date", "sec"]).reset_index(drop=True)

    @staticmethod
    def _codes_missing(df: pd.DataFrame, sec_codes: list[str]) -> list[str]:
        """Return codes absent from ``df``."""
        if df is None or df.empty:
            return list(sec_codes)
        present = set(df["sec"].unique())
        return [c for c in sec_codes if c not in present]

    def _cache_latest_known_date(self, cached: pd.DataFrame) -> pd.Timestamp | None:
        """全库已知最新交易日：优先注入查询，否则用本次缓存帧最大日期近似。"""
        if self.cache_latest_date is not None:
            return self.cache_latest_date()
        if cached.empty:
            return None
        return pd.Timestamp(cached["date"].max())

    def _split_by_coverage(
        self,
        cached: pd.DataFrame,
        sec_codes: list[str],
        start_date: str | pd.Timestamp | None,
        end_date: str | pd.Timestamp | None,
        period: str = "daily",
    ) -> tuple[list[str], list[str]]:
        """Split requested codes into cache-covered and uncovered (BUG-04).

        覆盖证明依据与局限：
        - 无边界请求（start/end 均为 None）语义是“读取缓存内全部历史”，
          证券出现在缓存中即视为命中，不存在部分日期误判为完整命中的问题。
        - 有边界请求采用首尾快路径：仅当请求区间 ⊆ 该证券缓存数据的
          [min_date, max_date] 时才视为覆盖。局限：缓存区间内部缺失的
          交易日（洞）无法由首尾判断发现——该完整性由写入侧 upsert 语义
          保证（F01/F04：响应遗漏日期保留旧行，不整段删除）；判断不按
          自然日数量推断交易日，正常周末/节假日不会误报为缺口。
        - **end 截断（方案 A，2026-09-13）**：effective_end = min(end,
          global_max)。global_max 为全库已知最新交易日（注入查询；退化为
          缓存帧最大日期）。全库没有任何标的有晚于它的数据，即不存在已知
          的更新交易日。新交易日由显式同步写入缓存后 global_max 前移，
          落后于它的标的才触发补拉。
        - **start 侧（F03 修复，2026-09-19）**：不再把请求 start 抬高到
          缓存帧最小日期——那会把“没有更早数据”循环论证为“无需更早数
          据”，多年历史缺失（缓存从 2024 年起而请求 2021 年起）会被静默
          判为覆盖且永不自愈。改为：sec_min <= start 直接覆盖；否则查
          coverage_reader 注入的源覆盖元数据（该证券的源曾被成功请求过且
          请求起点 <= 本次 start，即上市晚于请求起点或历史已补齐的合法情
          形）——有证明则覆盖，无证明则保守补拉。元数据缺失时退化为严格
          判定（节假日起点会多一次补拉，元数据建立后不再发生）。
        - 周期维度由 cache_reader 按 period 过滤（daily/minute 分表），
          日频与分钟缓存不会互混。
        """
        if cached.empty:
            return [], list(sec_codes)

        if start_date is None and end_date is None:
            cached_codes = set(cached["sec"].unique())
            covered = [c for c in sec_codes if c in cached_codes]
            uncovered = [c for c in sec_codes if c not in cached_codes]
            return covered, uncovered

        start = pd.Timestamp(start_date) if start_date is not None else None
        end = pd.Timestamp(end_date) if end_date is not None else None
        effective_start = start
        effective_end = end
        if end is not None:
            global_max = self._cache_latest_known_date(cached)
            if global_max is not None and end > global_max:
                effective_end = global_max
        start_covered: set[str] = set()
        if start is not None and self.coverage_reader is not None:
            try:
                start_covered = self.coverage_reader(sec_codes, start, end, period) or set()
            except Exception:
                logger.exception("覆盖元数据读取失败，按无证明处理：sec=%s", sec_codes)
        covered: list[str] = []
        uncovered: list[str] = []
        for c in sec_codes:
            sub = cached[cached["sec"] == c]
            if sub.empty:
                uncovered.append(c)
                continue
            sec_min = pd.Timestamp(sub["date"].min())
            sec_max = pd.Timestamp(sub["date"].max())
            start_ok = effective_start is None or sec_min <= effective_start or c in start_covered
            end_ok = effective_end is None or sec_max >= effective_end
            if start_ok and end_ok:
                covered.append(c)
            else:
                uncovered.append(c)
        return covered, uncovered

    def _fetch_from_source(
        self,
        source: DataSource,
        sec_codes: list[str],
        start_date: str | pd.Timestamp | None,
        end_date: str | pd.Timestamp | None,
        period: str,
        source_name: str,
    ) -> pd.DataFrame:
        """Try to fetch data from a source. Returns empty DataFrame on failure.

        源异常（网络/接口失败等）兜底返回空并记录异常堆栈，供上层回退缓存；
        非法参数类异常（如周期校验）在入口已拦截，不会进入本方法。
        """
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
            logger.exception(
                "数据源获取失败（source=%s sec=%s range=[%s, %s] period=%s），"
                "按空数据处理并回退",
                source_name, sec_codes, start_date, end_date, period,
            )
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
