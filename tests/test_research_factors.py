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
    cls = resolve_factor_class("momentum")
    assert cls is not None
    assert cls.__name__ == "MomentumFactor"


def test_load_research_categories_mixes_sources() -> None:
    cats = load_research_categories()
    keys = [c.key for c in cats]
    assert "research_demo" in keys
    names = {f.name for c in cats for f in c.factors}
    assert "price_position" in names  # research custom
    assert "momentum" in names  # core built-in fallback


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