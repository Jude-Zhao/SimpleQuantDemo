"""Data service layer.

Wraps the core data source with SQLite caching for the webapp.
Provides convenience functions used by other webapp services.
"""

from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy.orm import Session

from core.data.cached_source import CachedDataSource
from webapp.config import get_config
from webapp.models.market_data import EtfDailyBar

logger = logging.getLogger(__name__)

_config = get_config()


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
    for _, row in df.iterrows():
        sec = row["sec"]
        date_val = pd.to_datetime(row["date"]).date()
        bar_id = f"{sec}_{date_val.isoformat()}"

        existing = db.query(EtfDailyBar).filter_by(id=bar_id).first()
        if existing:
            continue

        bar = EtfDailyBar(
            id=bar_id,
            sec_code=sec,
            trade_date=date_val,
            open=float(row.get("open", 0)),
            high=float(row.get("high", 0)),
            low=float(row.get("low", 0)),
            close=float(row.get("close", 0)),
            volume=float(row.get("volume", 0)),
            amount=float(row.get("amount", 0)),
            source=row.get("source", ""),
        )
        db.add(bar)
    db.commit()


def get_cached_source(db: Session) -> CachedDataSource:
    """Get a CachedDataSource instance backed by SQLite."""
    primary = _get_primary_source()

    if not _config.datasource.cache_enabled:
        return CachedDataSource(primary_source=primary)

    return CachedDataSource(
        primary_source=primary,
        cache_reader=_cache_reader(db),
        cache_writer=_cache_writer(db),
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
