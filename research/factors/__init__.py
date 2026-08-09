"""Research factor pool.

Factors here follow the same ``FactorBuilder`` protocol as core factors but
are isolated from the core/web registry. Importing the package registers the
built-in research factors (see ``research/factors/registry.py``).
"""

from __future__ import annotations

from research.factors.price_position import PricePositionFactor
from research.factors.registry import (
    get_research_factor_class,
    list_research_factor_names,
    register_factor,
    resolve_factor_class,
)

__all__ = [
    "PricePositionFactor",
    "get_research_factor_class",
    "list_research_factor_names",
    "register_factor",
    "resolve_factor_class",
]