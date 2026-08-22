"""Factor interfaces and built-in factor implementations.

The ``FactorBuilder`` base class and registry live in this package; the
concrete built-in factors live under ``core.factors.builtin`` and are
re-exported here for convenience.

New factors are auto-discovered by ``core.factors.registry.discover_factors``
on startup — see ``core/factors/README.md`` for a how-to guide.
"""

from core.factors.base import FactorBuilder
from core.factors.builtin.drawdown_120.drawdown_120 import Drawdown120Factor
from core.factors.builtin.macd_hist.macd_hist import MACDHistFactor
from core.factors.builtin.mfi.mfi import MFIFactor
from core.factors.builtin.psy20.psy20 import PSY20Factor
from core.factors.builtin.skewness_60_reversal.skewness_60_reversal import (
    Skewness60ReversalFactor,
)

__all__ = [
    "FactorBuilder",
    "MACDHistFactor",
    "Skewness60ReversalFactor",
    "MFIFactor",
    "PSY20Factor",
    "Drawdown120Factor",
]