"""Research factor pool.

Factors here follow the same ``FactorBuilder`` protocol as core factors but
are isolated from the core/web registry. Importing the package registers the
built-in research factors (see ``research/factors/registry.py``).
"""

from __future__ import annotations

from research.factors.etf15_liquidity import MFIFactor, PSY20Factor
from research.factors.etf15_momentum import (
    MACDHistFactor,
    Momentum10Factor,
    PLRC24Factor,
    RiskAdjMomentum120Factor,
)
from research.factors.etf15_reversal import (
    ReversalBias5Factor,
    Skewness60ReversalFactor,
)
from research.factors.etf15_risk import (
    Drawdown120Factor,
    LowDownsideVol60Factor,
)
from research.factors.price_position import PricePositionFactor
from research.factors.registry import (
    get_research_factor_class,
    list_research_factor_names,
    register_factor,
    resolve_factor_class,
)

__all__ = [
    "PricePositionFactor",
    "MACDHistFactor",
    "PLRC24Factor",
    "RiskAdjMomentum120Factor",
    "Momentum10Factor",
    "ReversalBias5Factor",
    "Skewness60ReversalFactor",
    "LowDownsideVol60Factor",
    "Drawdown120Factor",
    "MFIFactor",
    "PSY20Factor",
    "get_research_factor_class",
    "list_research_factor_names",
    "register_factor",
    "resolve_factor_class",
]