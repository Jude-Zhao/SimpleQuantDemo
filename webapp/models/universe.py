"""Universe (ETF pool) ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String

from webapp.models.database import Base


class UniverseItem(Base):
    """ETF universe / watchlist items.

    Tracks which ETFs are currently in the active investment pool.
    Items are soft-deleted (is_active=False) to preserve history.
    """

    __tablename__ = "universe_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sec_code = Column(String, unique=True, nullable=False, index=True)
    sec_name = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, index=True)
    added_at = Column(DateTime, default=datetime.utcnow)
    removed_at = Column(DateTime, nullable=True)
    meta = Column(JSON, default=dict)  # category, fund_size, track_index, etc.
