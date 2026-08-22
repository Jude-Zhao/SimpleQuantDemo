"""60-day skewness reversal factor (分类: 反转)."""

from __future__ import annotations

import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.registry import register_factor
from core.factors.utils import pivot_price_field, validate_factor_matrix


@register_factor("skewness_60_reversal")
class Skewness60ReversalFactor(FactorBuilder):
    """60-day return skewness, negated: higher-moment reversal (lottery preference)."""

    registry_name = "skewness_60_reversal"
    display_name = "60日偏度反转"
    category = "反转"
    description = "60日收益偏度的相反数，高阶矩反转(彩票偏好)——右偏资产事前被高估、未来回归"
    formula = "-skew(ret,60)"
    direction = "positive"
    params_schema: dict = {}

    @property
    def name(self) -> str:
        return "skewness_60_reversal"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        close = pivot_price_field(price_data, field="close", universe=universe)
        ret = close.pct_change(fill_method=None)
        factor = -ret.rolling(60).skew()
        factor.index.name = "date"
        validate_factor_matrix(factor, universe, name=self.name)
        return factor