"""60-day MA slope reversal factor (分类: 反转)."""

from __future__ import annotations

import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.registry import register_factor
from core.factors.utils import pivot_price_field, validate_factor_matrix


@register_factor("ma60_slope_reversal")
class MA60SlopeReversalFactor(FactorBuilder):
    """60-day MA slope reversal (negated)."""

    registry_name = "ma60_slope_reversal"
    display_name = "60日均线斜率反转"
    category = "反转"
    description = "60日均线20日斜率的相反数，中期趋势过度延伸反转"
    formula = "-(MA60_t/MA60.shift(20)-1)"
    direction = "positive"
    params_schema: dict = {}

    @property
    def name(self) -> str:
        return "ma60_slope_reversal"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        close = pivot_price_field(price_data, field="close", universe=universe)
        ma60 = close.rolling(60).mean()
        ma60_slope = ma60 / ma60.shift(20) - 1.0
        factor = -ma60_slope
        factor.index.name = "date"
        validate_factor_matrix(factor, universe, name=self.name)
        return factor