"""Data service layer.

Wraps the core data source with SQLite caching for the webapp.
Provides convenience functions used by other webapp services.
"""

from __future__ import annotations

import logging
import threading

import pandas as pd
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.data.cached_source import CachedDataSource
from webapp.config import get_config
from webapp.models.market_data import EtfCacheCoverage, EtfDailyBar

logger = logging.getLogger(__name__)

_config = get_config()

# 行情修订号（F17）：任何成功写入日线的调用后递增。展示层缓存键携带该值
# ——历史修订（改旧价/补洞）不改变证券集合与最大日期，仅靠二者做键会命中
# 过期结果。纯查询只读不递增。
_data_revision_lock = threading.Lock()
_data_revision = 0


def get_data_revision() -> int:
    """返回当前行情修订号（只读，供展示层缓存键使用）。"""
    with _data_revision_lock:
        return _data_revision


def _bump_data_revision() -> None:
    global _data_revision
    with _data_revision_lock:
        _data_revision += 1


def _get_primary_source():
    """Get primary data source instance.

    Uses AkShare (Tencent 后复权 hfq, stable long history) as the sole ETF
    data source. Raises when AkShare is unavailable — no silent fallback.

    腾讯请求节流区间从 datasource 配置注入（模块级全局节拍，见
    akshare_source.set_tencent_interval_range）。
    """
    from core.data.akshare_source import (
        AkShareDataSource,
        ensure_akshare_available,
        set_tencent_interval_range,
    )

    ensure_akshare_available()
    source = AkShareDataSource()
    ds_cfg = get_config().datasource
    set_tencent_interval_range(ds_cfg.tencent_min_interval, ds_cfg.tencent_max_interval)
    return source


def _cache_reader(db: Session):
    """Create a cache reader function bound to a DB session."""

    def reader(sec_codes: list[str], start_date, end_date, period: str = "daily") -> pd.DataFrame:
        return _read_daily_cache(db, sec_codes, start_date, end_date)

    return reader


def _read_daily_cache(db: Session, sec_codes, start_date, end_date) -> pd.DataFrame:
    start = pd.to_datetime(start_date).date() if start_date else None
    end = pd.to_datetime(end_date).date() if end_date else None

    query = db.query(EtfDailyBar).filter(EtfDailyBar.sec_code.in_(sec_codes))
    if start:
        query = query.filter(EtfDailyBar.trade_date >= start)
    if end:
        query = query.filter(EtfDailyBar.trade_date <= end)

    rows = query.all()
    if not rows:
        return pd.DataFrame()

    data = [
        {
            "date": pd.Timestamp(r.trade_date),
            "sec": r.sec_code,
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "volume": r.volume,
            "amount": r.amount,
        }
        for r in rows
    ]
    return pd.DataFrame(data)


def _cache_writer(db: Session):
    """Create a cache writer function bound to a DB session."""

    def writer(df: pd.DataFrame, period: str = "daily") -> None:
        if df.empty:
            return
        _write_daily_cache(db, df)

    return writer


def _write_daily_cache(db: Session, df: pd.DataFrame) -> None:
    upsert_daily_bars(db, df)


# 单条 upsert 语句携带的行数上限：首页级补拉约 30 标的 × 250 日 ≈ 7500 行，
# 分块避免单条语句过大；SQLite 变量上限（999×早期版本）远高于每块列数。
_UPSERT_CHUNK = 1000


def upsert_daily_bars(db: Session, df: pd.DataFrame, commit: bool = True) -> int:
    """按 (sec_code, trade_date) 原子 upsert 日线缓存，返回收到的行数。

    F01/F04 最小修复：收到的行覆盖同键旧值（源端修订可入库，两次读取
    结果一致）；响应遗漏的日期不删除、旧行保留。先查后插的旧实现会让
    修订值只进返回不进库，并发双写还会触发唯一键竞争。
    """
    if df is None or df.empty:
        return 0

    rows = []
    for _, row in df.iterrows():
        sec = row["sec"]
        date_val = pd.to_datetime(row["date"]).date()
        rows.append(
            {
                "id": f"{sec}_{date_val.isoformat()}",
                "sec_code": sec,
                "trade_date": date_val,
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row.get("close", 0)),
                "volume": float(row.get("volume", 0)),
                "amount": float(row.get("amount", 0)),
                "source": row.get("source", ""),
            }
        )

    count = 0
    for i in range(0, len(rows), _UPSERT_CHUNK):
        chunk = rows[i : i + _UPSERT_CHUNK]
        stmt = sqlite_insert(EtfDailyBar).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=[EtfDailyBar.sec_code, EtfDailyBar.trade_date],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "amount": stmt.excluded.amount,
                "source": stmt.excluded.source,
            },
        )
        db.execute(stmt)
        count += len(chunk)
    if commit:
        db.commit()
    if count:
        # F17: 写入成功后推进修订号（commit=False 的同步路径由调用方稍后
        # 提交；此处先行递增，回滚只会造成一次多余的重算，无害）
        _bump_data_revision()
    return count


def upsert_coverage_rows(
    db: Session,
    sec_codes: list[str],
    start_date: str | pd.Timestamp | None,
    end_date: str | pd.Timestamp | None,
    commit: bool = True,
) -> None:
    """记录源覆盖元数据：源已被成功请求 [start, end] 且返回内容已入缓存。

    fetched_from 取历史最小、fetched_to 取历史最大（单调扩张）；start 为
    None 的无界请求不产生 start 侧覆盖语义，跳过。``commit=False`` 留待
    调用方与行情写入同一事务提交（同步路径保持单 commit 不变量）。
    """
    if not sec_codes or start_date is None:
        return
    start = pd.to_datetime(start_date).date()
    end = pd.to_datetime(end_date).date() if end_date else start
    existing = {
        r.sec_code: r
        for r in db.query(EtfCacheCoverage)
        .filter(EtfCacheCoverage.sec_code.in_(list(sec_codes)))
        .all()
    }
    for sec in sec_codes:
        row = existing.get(sec)
        if row is None:
            db.add(
                EtfCacheCoverage(sec_code=sec, fetched_from=start, fetched_to=end)
            )
            continue
        if start < row.fetched_from:
            row.fetched_from = start
        if end > row.fetched_to:
            row.fetched_to = end
    if commit:
        db.commit()


def _coverage_reader(db: Session):
    """Create a coverage reader function bound to a DB session.

    返回 start 侧已有源覆盖证明的证券集合（fetched_from <= 请求 start）。
    """

    def reader(sec_codes: list[str], start_date, end_date, period: str = "daily") -> set[str]:
        if not sec_codes or start_date is None:
            return set()
        start = pd.to_datetime(start_date).date()
        rows = (
            db.query(EtfCacheCoverage.sec_code)
            .filter(
                EtfCacheCoverage.sec_code.in_(sec_codes),
                EtfCacheCoverage.fetched_from <= start,
            )
            .all()
        )
        return {r.sec_code for r in rows}

    return reader


def _coverage_writer(db: Session):
    """Create a coverage writer function bound to a DB session."""

    def writer(sec_codes: list[str], start_date, end_date, period: str = "daily") -> None:
        upsert_coverage_rows(db, sec_codes, start_date, end_date)

    return writer


def _cache_date_bounds(db: Session):
    """全库（etf_daily_bar 全表跨标的）最新交易日查询，供缓存覆盖判定把请
    求 end 截断到已知最新交易日（见 CachedDataSource._split_by_coverage）。
    start 侧覆盖由 EtfCacheCoverage 元数据判定（见 _coverage_reader）。"""

    def _latest() -> pd.Timestamp | None:
        max_date = db.query(func.max(EtfDailyBar.trade_date)).scalar()
        return pd.Timestamp(max_date) if max_date is not None else None

    return _latest


def get_cached_source(db: Session) -> CachedDataSource:
    """Get a CachedDataSource instance backed by SQLite."""
    primary = _get_primary_source()

    if not _config.datasource.cache_enabled:
        return CachedDataSource(primary_source=primary)

    return CachedDataSource(
        primary_source=primary,
        cache_reader=_cache_reader(db),
        cache_writer=_cache_writer(db),
        cache_latest_date=_cache_date_bounds(db),
        coverage_reader=_coverage_reader(db),
        coverage_writer=_coverage_writer(db),
    )


def get_etf_price(
    db: Session,
    sec_codes: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
    period: str = "daily",
) -> pd.DataFrame:
    """Get ETF price data (cached)."""
    source = get_cached_source(db)
    df = source.get_etf_price_by_codes(
        sec_codes=sec_codes,
        start_date=start_date,
        end_date=end_date,
        period=period,
    )
    # BUG-04: 补齐失败的证券透出为显式告警，不让调用方误以为数据完整
    missing = getattr(source, "incomplete_codes", [])
    if missing:
        logger.warning(
            "行情读取不完整：以下证券在请求区间 [%s, %s] period=%s 内无任何数据源返回: %s",
            start_date, end_date, period, missing,
        )
    return df


def get_etf_list(db: Session | None = None) -> list[dict]:
    """Get list of available ETFs.

    Returns the active universe from ``universe_items`` (single source of
    truth for the watchlist). The ``category`` field comes from the
    classification rule engine's ``asset_type`` dimension to stay consistent
    with the classification page and strategy constraint validation.

    Returns an empty list when no DB session is available.
    """
    if db is None:
        return []
    from webapp.services.classification_service import classify_universe
    from webapp.services.universe_service import list_active_universe

    items = list_active_universe(db)
    if not items:
        return []
    results = classify_universe(db)
    cat_by_sec = {r.sec_code: r.categories for r in results}

    return [
        {
            "sec_code": item.sec_code,
            "sec_name": item.sec_name,
            "category": cat_by_sec.get(item.sec_code, {}).get("asset_type", ""),
        }
        for item in items
    ]
