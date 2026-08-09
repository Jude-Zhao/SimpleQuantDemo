"""Reversal factor implementation (分类: 反转)."""

from __future__ import annotations

import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.registry import register_factor
from core.factors.utils import pivot_price_field, validate_factor_matrix


@register_factor("reversal")
class ReversalFactor(FactorBuilder):
    """N-day reversal factor (negative of momentum).

    Captures the short-term reversal effect: assets that have declined
    over the past N days tend to rebound, and vice versa.
    This is a simplified value-style factor based on price mean-reversion.
    """

    registry_name = "reversal"
    display_name = "反转因子"
    category = "价值"
    description = "过去N个交易日收盘价收益率的相反数，捕捉短期反转效应"
    formula = "REV(t) = -(close(t) / close(t-N) - 1)"
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
        return f"reversal_{self.window}"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        close = pivot_price_field(price_data, field="close", universe=universe)
        factor = -close.pct_change(periods=self.window, fill_method=None)
        factor.index.name = "date"
        validate_factor_matrix(factor, universe, name=self.name)
        return factor
