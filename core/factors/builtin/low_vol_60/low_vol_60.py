"""60-day low volatility factor (分类: 波动)."""

from __future__ import annotations

import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.registry import register_factor
from core.factors.utils import pivot_price_field, validate_factor_matrix


@register_factor("low_vol_60")
class LowVol60Factor(FactorBuilder):
    """60-day low volatility (negated)."""

    registry_name = "low_vol_60"
    display_name = "60日低波动"
    category = "波动"
    description = "60日收益标准差的相反数，低波动得高分"
    formula = "-std(ret,60)"
    direction = "positive"
    params_schema: dict = {}

    @property
    def name(self) -> str:
        return "low_vol_60"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        close = pivot_price_field(price_data, field="close", universe=universe)
        ret = close.pct_change(fill_method=None)
        vol60 = ret.rolling(60).std().clip(lower=1e-8)
        factor = -vol60
        factor.index.name = "date"
        validate_factor_matrix(factor, universe, name=self.name)
        return factor