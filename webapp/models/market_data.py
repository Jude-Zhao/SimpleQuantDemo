"""Market data ORM model (daily bar cache table)."""

from __future__ import annotations

from sqlalchemy import Column, Date, DateTime, Float, Index, String, func

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


class EtfCacheCoverage(Base):
    """Per-security source-coverage metadata for the ETF daily cache (F03).

    记录最近一次成功源请求的区间：源已被请求过 [fetched_from, fetched_to]，
    且其返回内容已写入缓存。覆盖判定据此区分“上市晚于请求起点”的合法
    间隔与“历史缺失”——fetched_from <= 请求 start 即视为 start 侧已覆盖，
    见 ``CachedDataSource._split_by_coverage``。
    """

    __tablename__ = "etf_cache_coverage"

    sec_code = Column(String, primary_key=True)
    fetched_from = Column(Date, nullable=False)
    fetched_to = Column(Date, nullable=False)
    updated_at = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
