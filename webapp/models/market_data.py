"""Market data ORM model (daily bar cache table)."""

from __future__ import annotations

from sqlalchemy import Column, Date, Float, Index, String

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
    source = Column(String)  # 数据来源，当前为 akshare（腾讯 fqkline hfq）

    __table_args__ = (
        Index("ix_daily_bar_sec_date", "sec_code", "trade_date", unique=True),
    )
