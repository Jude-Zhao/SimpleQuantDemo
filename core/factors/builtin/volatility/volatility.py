"""Volatility factor implementation."""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.registry import register_factor
from core.factors.utils import pivot_price_field, validate_factor_matrix


@register_factor("volatility")
class VolatilityFactor(FactorBuilder):
    """N-day annualized volatility factor based on close-to-close returns.

    Measures the price fluctuation over the past N trading days. The output is
    negated so that higher values are better (low volatility  ->  high score),
    matching the convention that all factors are "larger is better" upstream.
    """

    registry_name = "volatility"
    display_name = "波动率因子"
    category = "波动率"
    description = "过去N个交易日的收盘价收益率年化标准差（取反：低波动得高分）"
    formula = "VOL(t) = -std(returns(t-N+1..t)) * sqrt(annualization)"
    direction = "positive"
    params_schema = {
        "window": {
            "type": "int",
            "default": 20,
            "min": 2,
            "max": 252,
            "step": 1,
            "label": "窗口天数",
        },
        "annualization": {
            "type": "int",
            "default": 252,
            "min": 1,
            "max": 365,
            "step": 1,
            "label": "年化天数",
        },
    }

    def __init__(self, window: int = 20, annualization: int = 252) -> None:
        if window <= 1:
            raise ValueError("window must be greater than 1.")
        if annualization <= 0:
            raise ValueError("annualization must be positive.")
        self.window = window
        self.annualization = annualization

    @property
    def name(self) -> str:
        return f"volatility_{self.window}"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        close = pivot_price_field(price_data, field="close", universe=universe)
        returns = close.pct_change(fill_method=None)
        factor = -returns.rolling(window=self.window, min_periods=self.window).std() * np.sqrt(self.annualization)
        factor.index.name = "date"
        validate_factor_matrix(factor, universe, name=self.name)
        return factor
