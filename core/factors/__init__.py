"""Factor interfaces and built-in factor implementations.

The ``FactorBuilder`` base class and registry live in this package; the
concrete built-in factors live under ``core.factors.builtin`` and are
re-exported here for convenience.

New factors are auto-discovered by ``core.factors.registry.discover_factors``
on startup — see ``core/factors/README.md`` for a how-to guide.
"""

from core.factors.base import FactorBuilder
from core.factors.builtin.aroon_diff.aroon_diff import AroonDiffFactor
from core.factors.builtin.low_vol_60.low_vol_60 import LowVol60Factor
from core.factors.builtin.ma60_slope_reversal.ma60_slope_reversal import (
    MA60SlopeReversalFactor,
)
from core.factors.builtin.momentum_60_reversal.momentum_60_reversal import (
    Momentum60ReversalFactor,
)
from core.factors.builtin.money_flow_20.money_flow_20 import MoneyFlow20Factor

__all__ = [
    "FactorBuilder",
    "AroonDiffFactor",
    "Momentum60ReversalFactor",
    "MA60SlopeReversalFactor",
    "LowVol60Factor",
    "MoneyFlow20Factor",
]