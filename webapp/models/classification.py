"""Classification rule ORM model."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String

from webapp.models.database import Base, utc_now


class ClassificationRule(Base):
    """Rules for classifying ETFs into categories.

    Each rule assigns a category value (e.g. "大盘", "商品") to a category
    key (e.g. "size", "asset_type"). Rules are executed in priority order;
    higher priority rules override lower ones within the same category key.
    """

    __tablename__ = "classification_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_name = Column(String, nullable=False)
    category_key = Column(String, nullable=False, index=True)  # e.g. "size", "asset_type"
    rule_type = Column(String, nullable=False)  # manual / by_field / by_range
    config = Column(JSON, default=dict)  # rule-specific configuration
    is_active = Column(Boolean, default=True, index=True)
    priority = Column(Integer, default=100, index=True)  # lower = higher priority
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
