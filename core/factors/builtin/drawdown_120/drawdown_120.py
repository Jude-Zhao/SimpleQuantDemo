"""120-day max drawdown factor (分类: 波动)."""

from __future__ import annotations

import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.registry import register_factor
from core.factors.utils import pivot_price_field, validate_factor_matrix


@register_factor("drawdown_120")
class Drawdown120Factor(FactorBuilder):
    """120-day max drawdown; shallower drawdown scores higher (path risk)."""

    registry_name = "drawdown_120"
    display_name = "120日最大回撤"
    category = "波动"
    description = "过去120日最深回撤，回撤越浅分越高"
    formula = "min(close/cummax - 1, 120)"
    direction = "positive"
    params_schema: dict = {}

    @property
    def name(self) -> str:
        return "drawdown_120"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        close = pivot_price_field(price_data, field="close", universe=universe)
        running_max = close.cummax()
        dd = close / running_max - 1.0
        factor = dd.rolling(120, min_periods=1).min()
        factor.index.name = "date"
        validate_factor_matrix(factor, universe, name=self.name)
        return factor