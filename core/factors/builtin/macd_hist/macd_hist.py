"""MACD histogram factor (分类: 动量)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.registry import register_factor
from core.factors.utils import pivot_price_field, validate_factor_matrix


@register_factor("macd_hist")
class MACDHistFactor(FactorBuilder):
    """MACD histogram normalized by close price (short-term continuation)."""

    registry_name = "macd_hist"
    display_name = "MACD柱状图(归一化)"
    category = "动量"
    description = "12/26 EMA 差离值(DIF-DEA)按收盘价归一化，短期趋势延续指标"
    formula = "(DIF-DEA)/close; DIF=EMA12-EMA26, DEA=EMA9(DIF)"
    direction = "positive"
    params_schema: dict = {}

    @property
    def name(self) -> str:
        return "macd_hist"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        close = pivot_price_field(price_data, field="close", universe=universe)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        factor = (dif - dea) / close.replace(0.0, np.nan)
        factor.index.name = "date"
        validate_factor_matrix(factor, universe, name=self.name)
        return factor