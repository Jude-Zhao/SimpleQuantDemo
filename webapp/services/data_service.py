"""Data service layer.

Wraps core data sources with SQLite caching for the webapp.
Provides convenience functions used by other webapp services.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from core.data.akshare_source import AkShareDataUnavailable
from core.data.baostock_source import BaostockDataSource
from core.data.cached_source import CachedDataSource
from webapp.config import get_config
from webapp.models.market_data import EtfDailyBar, EtfMinuteBar

_config = get_config()


def _get_primary_source():
    """Get primary data source instance.

    Tries AkShare first, falls back to baostock if unavailable.
    """
    try:
        from core.data.akshare_source import ensure_akshare_available
        ensure_akshare_available()
        # AkShare ETF price adapter is not fully implemented yet,
        # so we use baostock as the working primary for now.
        # TODO: switch to AkShare when its ETF adapter is ready.
        pass
    except AkShareDataUnavailable:
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
        query = query.filter(EtfMinuteBar.trade_datetime >= start)
    if end:
        query = query.filter(EtfMinuteBar.trade_datetime <= end)

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
    return source.get_etf_price_by_codes(
        sec_codes=sec_codes,
        start_date=start_date,
        end_date=end_date,
        period=period,
    )


def get_etf_list(db: Session | None = None) -> list[dict]:
    """Get list of available ETFs.

    Returns a curated default list for now.
    TODO: fetch from data source when available.
    """
    return [
        {"sec_code": "510300.SH", "sec_name": "沪深300ETF", "category": "宽基"},
        {"sec_code": "510500.SH", "sec_name": "中证500ETF", "category": "宽基"},
        {"sec_code": "159915.SZ", "sec_name": "创业板ETF", "category": "宽基"},
        {"sec_code": "518880.SH", "sec_name": "黄金ETF", "category": "商品"},
        {"sec_code": "511010.SH", "sec_name": "国债ETF", "category": "债券"},
        {"sec_code": "510050.SH", "sec_name": "上证50ETF", "category": "宽基"},
        {"sec_code": "510880.SH", "sec_name": "红利ETF", "category": "策略"},
        {"sec_code": "159901.SZ", "sec_name": "深100ETF", "category": "宽基"},
        {"sec_code": "510180.SH", "sec_name": "上证180ETF", "category": "宽基"},
        {"sec_code": "159919.SZ", "sec_name": "沪深300ETF(嘉实)", "category": "宽基"},
    ]
