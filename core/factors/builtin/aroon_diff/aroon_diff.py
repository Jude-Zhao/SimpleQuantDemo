"""Aroon diff factor (分类: 动量)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.registry import register_factor
from core.factors.utils import pivot_price_field, validate_factor_matrix


def _aroon_up_pos(x: np.ndarray) -> float:
    """Aroon-up: (argmax + 1) / len over a rolling window of highs."""
    return (float(np.argmax(x)) + 1.0) / float(len(x))


def _aroon_down_pos(x: np.ndarray) -> float:
    """Aroon-down: (argmin + 1) / len over a rolling window of lows."""
    return (float(np.argmin(x)) + 1.0) / float(len(x))


@register_factor("aroon_diff")
class AroonDiffFactor(FactorBuilder):
    """25-day Aroon up-down difference."""

    registry_name = "aroon_diff"
    display_name = "25日Aroon差值"
    category = "动量"
    description = "25日窗口内最高价位置减去最低价位置，衡量突破方向"
    formula = "(iH+1)/25 - (iL+1)/25"
    direction = "positive"
    params_schema: dict = {}

    @property
    def name(self) -> str:
        return "aroon_diff"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        high = pivot_price_field(price_data, field="high", universe=universe)
        low = pivot_price_field(price_data, field="low", universe=universe)
        aroon_up = high.rolling(25).apply(_aroon_up_pos, raw=True)
        aroon_down = low.rolling(25).apply(_aroon_down_pos, raw=True)
        factor = aroon_up - aroon_down
        factor.index.name = "date"
        validate_factor_matrix(factor, universe, name=self.name)
        return factor