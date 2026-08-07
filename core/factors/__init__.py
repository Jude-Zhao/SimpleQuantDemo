"""Factor interfaces and built-in factor implementations.

The ``FactorBuilder`` base class and registry live in this package; the
concrete built-in factors live under ``core.factors.builtin`` and are
re-exported here for convenience.

New factors are auto-discovered by ``core.factors.registry.discover_factors``
on startup — see ``core/factors/README.md`` for a how-to guide.
"""

from core.factors.base import FactorBuilder
from core.factors.builtin.momentum import MomentumFactor
from core.factors.builtin.value import ReversalFactor
from core.factors.builtin.volatility import VolatilityFactor

__all__ = ["FactorBuilder", "MomentumFactor", "ReversalFactor", "VolatilityFactor"]