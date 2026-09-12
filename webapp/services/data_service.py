"""Data service layer.

Wraps core data sources with SQLite caching for the webapp.
Provides convenience functions used by other webapp services.
"""

from __future__ import annotations

import logging
import math

import pandas as pd
from sqlalchemy.orm import Session

from core.data.akshare_source import AkShareDataUnavailable
from core.data.baostock_source import BaostockDataSource
from core.data.cached_source import CachedDataSource
from webapp.config import get_config
from webapp.models.market_data import EtfDailyBar, EtfMinuteBar

logger = logging.getLogger(__name__)

_config = get_config()


def _validated_adj_factor(value) -> float | None:
    """BUG-05: adj_factor 必须有限且 > 0，否则按显式缺失（NULL）处理。

    不默认造 1，不用于宣称真实现价。
    """
    if value is None or pd.isna(value):
        return None
    try:
        val = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(val) or val <= 0:
        return None
    return val


def _get_primary_source():
    """Get primary data source instance.

    Uses AkShare as primary for ETF data (Tencent 后复权 hfq, stable long
    history). Falls back to Baostock if AkShare is unavailable.
    """
    try:
        from core.data.akshare_source import AkShareDataSource, ensure_akshare_available
        ensure_akshare_available()
        return AkShareDataSource()
    except Exception:
        pass
    return BaostockDataSource()


def _get_secondary_source():
    """Get secondary (fallback) data source."""
    return BaostockDataSource()


def _cache_reader(db: Session):
    """Create a cache reader function bound to a DB session."""

    def reader(sec_codes: list[str], start_date, end_date, period: str = "daily") -> pd.DataFrame:
        if period == "daily" or period == "d":
            return _read_daily_cache(db, sec_codes, start_date, end_date)
        else:
            return _read_minute_cache(db, sec_codes, start_date, end_date, period)

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
            "adj_factor": r.adj_factor,
        }
        for r in rows
    ]
    return pd.DataFrame(data)


def _read_minute_cache(db: Session, sec_codes, start_date, end_date, period: str) -> pd.DataFrame:
    start = pd.to_datetime(start_date) if start_date else None
    end = pd.to_datetime(end_date) if end_date else None

    query = db.query(EtfMinuteBar).filter(
        EtfMinuteBar.sec_code.in_(sec_codes),
        EtfMinuteBar.period == period,
    )
    if start:
        query = query.filter(EtfMinuteBar.trade_datetime >= start.normalize())
    if end:
        # BUG-06: 半开区间【开始日零点, 结束日下一日零点)，结束日盘中数据可查
        query = query.filter(EtfMinuteBar.trade_datetime < end.normalize() + pd.Timedelta(days=1))

    rows = query.all()
    if not rows:
        return pd.DataFrame()

    data = [
        {
            "date": pd.Timestamp(r.trade_datetime),
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
        if period == "daily" or period == "d":
            _write_daily_cache(db, df)
        else:
            _write_minute_cache(db, df, period)

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
            adj_factor=_validated_adj_factor(row.get("adj_factor")),
            source=row.get("source", ""),
        )
        db.add(bar)
    db.commit()


def _write_minute_cache(db: Session, df: pd.DataFrame, period: str) -> None:
    for _, row in df.iterrows():
        sec = row["sec"]
        dt_val = pd.to_datetime(row["date"])
        bar_id = f"{sec}_{dt_val.strftime('%Y%m%d%H%M')}_{period}"

        existing = db.query(EtfMinuteBar).filter_by(id=bar_id).first()
        if existing:
            continue

        bar = EtfMinuteBar(
            id=bar_id,
            sec_code=sec,
            trade_datetime=dt_val,
            period=period,
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
    secondary = _get_secondary_source()

    if not _config.datasource.cache_enabled:
        return CachedDataSource(primary_source=primary, secondary_source=secondary)

    return CachedDataSource(
        primary_source=primary,
        secondary_source=secondary,
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
