"""Momentum factor implementation."""

from __future__ import annotations

import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.registry import register_factor
from core.factors.utils import pivot_price_field, validate_factor_matrix


@register_factor("momentum")
class MomentumFactor(FactorBuilder):
    """N-day close-to-close return factor.

    Measures the price trend strength over the past N trading days.
    Momentum effect: assets that have performed well recently tend to
    continue performing well in the short term.
    """

    registry_name = "momentum"
    display_name = "动量因子"
    category = "动量"
    description = "过去N个交易日的收盘价收益率，反映价格趋势强度"
    formula = "MOM(t) = close(t) / close(t-N) - 1"
    direction = "positive"
    params_schema = {
        "window": {
            "type": "int",
            "default": 5,
            "min": 1,
            "max": 252,
            "step": 1,
            "label": "窗口天数",
        }
    }

    def __init__(self, window: int = 5) -> None:
        if window <= 0:
            raise ValueError("window must be positive.")
        self.window = window

    @property
    def name(self) -> str:
        return f"momentum_{self.window}"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        close = pivot_price_field(price_data, field="close", universe=universe)
        factor = close.pct_change(periods=self.window, fill_method=None)
        factor.index.name = "date"
        validate_factor_matrix(factor, universe, name=self.name)
        return factor
