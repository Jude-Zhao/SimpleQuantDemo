"""Sample research factor: N-day price position.

Demonstrates the research factor protocol: a class inheriting
``core.factors.base.FactorBuilder`` and registered in the *research* pool
via ``research.factors.registry.register_factor``. Once validated, migrate
it to ``core/factors/builtin/<name>/`` and register it in
``core/factors/builtin/factors.yaml`` to go live in webapp.
"""

from __future__ import annotations

import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.utils import pivot_price_field, validate_factor_matrix
from research.factors.registry import register_factor


@register_factor()
class PricePositionFactor(FactorBuilder):
    """Position of close within the N-day high-low range [0, 1].

    A value near 1 means price is near the top of its recent range; near 0
    means near the bottom. Often used as a mean-reversion / regime signal.
    """

    registry_name = "price_position"
    display_name = "价格位置"
    category = "研究"
    description = "N日高低区间内收盘价所处位置，衡量短期价格强弱与均值回归倾向"
    formula = "PP(t) = (close - min(low,N)) / (max(high,N) - min(low,N))"
    direction = "positive"
    params_schema = {
        "window": {
            "type": "int",
            "default": 20,
            "min": 1,
            "max": 252,
            "step": 1,
            "label": "窗口天数",
        }
    }

    def __init__(self, window: int = 20) -> None:
        if window <= 0:
            raise ValueError("window must be positive.")
        self.window = window

    @property
    def name(self) -> str:
        return f"price_position_{self.window}"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        close = pivot_price_field(price_data, field="close", universe=universe)
        high = pivot_price_field(price_data, field="high", universe=universe)
        low = pivot_price_field(price_data, field="low", universe=universe)

        high_max = high.rolling(self.window, min_periods=1).max()
        low_min = low.rolling(self.window, min_periods=1).min()
        span = high_max - low_min
        factor = (close - low_min) / span.replace(0.0, pd.NA)
        factor.index.name = "date"
        validate_factor_matrix(factor, universe, name=self.name)
        return factor