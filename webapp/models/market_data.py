"""Market data ORM models (daily bar + minute bar cache tables)."""

from __future__ import annotations

from sqlalchemy import Column, Date, DateTime, Float, Index, String

from webapp.models.database import Base


class EtfDailyBar(Base):
    """ETF daily OHLCV cache table."""

    __tablename__ = "etf_daily_bar"

    id = Column(String, primary_key=True)  # sec_code + "_" + trade_date
    sec_code = Column(String, nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    amount = Column(Float)
    source = Column(String)  # akshare / baostock

    __table_args__ = (
        Index("ix_daily_bar_sec_date", "sec_code", "trade_date", unique=True),
    )


class EtfMinuteBar(Base):
    """ETF minute-bar OHLCV cache table."""

    __tablename__ = "etf_minute_bar"

    id = Column(String, primary_key=True)  # sec_code + "_" + datetime + "_" + period
    sec_code = Column(String, nullable=False, index=True)
    trade_datetime = Column(DateTime, nullable=False, index=True)
    period = Column(String, nullable=False, index=True)  # 1m / 5m / 15m / 30m / 60m
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    amount = Column(Float)
    source = Column(String)

    __table_args__ = (
        Index("ix_minute_bar_sec_dt_period", "sec_code", "trade_datetime", "period", unique=True),
    )
