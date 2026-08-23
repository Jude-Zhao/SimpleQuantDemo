"""Research factor pool: isolation, resolver, and protocol consistency."""

from __future__ import annotations

import pandas as pd

from core.factors.registry import list_factor_names
from research.factors import (
    PricePositionFactor,
    get_research_factor_class,
    list_research_factor_names,
    resolve_factor_class,
)
from research.factors.config import load_research_categories


def test_research_pool_isolated_from_core() -> None:
    """Research factors must not pollute the core/web global registry."""
    assert "price_position" in list_research_factor_names()
    assert "price_position" not in list_factor_names()


def test_resolve_custom_research_factor() -> None:
    cls = resolve_factor_class("price_position")
    assert cls is not None
    assert cls.__name__ == "PricePositionFactor"
    assert get_research_factor_class("price_position") is cls


def test_resolve_falls_back_to_core() -> None:
    cls = resolve_factor_class("macd_hist")
    assert cls is not None
    assert cls.__name__ == "MACDHistFactor"


def test_load_research_categories_keeps_all_declared() -> None:
    cats = load_research_categories()
    keys = [c.key for c in cats]
    assert "momentum" in keys
    assert "reversal" in keys
    assert "volatility" in keys
    assert "volume" in keys
    names = {f.name for c in cats for f in c.factors}
    # 已迁移到 core 的因子仍可从 research 分类配置解析（core fallback）
    assert "macd_hist" in names
    assert "mfi" in names
    # 仍留在 research 池的因子（research_demo 分类）
    assert "price_position" in names
    # 已从配置移除的无实现 etf15_* 文档因子不应再被引用
    assert "plrc24" not in names
    assert "reversal_bias5" not in names


def test_protocol_migration_consistency(sqlite_source) -> None:
    """Building via the resolver equals direct instantiation — migration is
    zero-cost because the output protocol is identical."""
    price_data, _macro, universe = sqlite_source.load_all(
        start_date="2024-01-01", end_date="2024-06-30"
    )

    via_resolver = resolve_factor_class("price_position")(window=20).build(
        price_data, pd.DataFrame(), universe
    )
    direct = PricePositionFactor(window=20).build(
        price_data, pd.DataFrame(), universe
    )
    pd.testing.assert_frame_equal(via_resolver, direct)

    # Output is a date x sec matrix indexed by date.
    assert via_resolver.index.name == "date"
    assert set(via_resolver.columns) == set(universe)