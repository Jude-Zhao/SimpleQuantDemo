"""20-day psychological line factor (分类: 量能)."""

from __future__ import annotations

import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.registry import register_factor
from core.factors.utils import pivot_price_field, validate_factor_matrix


@register_factor("psy20")
class PSY20Factor(FactorBuilder):
    """20-day up-day ratio (psychological line), funding/follow-through direction."""

    registry_name = "psy20"
    display_name = "20日心理线"
    category = "量能"
    description = "20日内上涨日占比，资金方向信号"
    formula = "mean(ret>0, 20)"
    direction = "positive"
    params_schema: dict = {}

    @property
    def name(self) -> str:
        return "psy20"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        close = pivot_price_field(price_data, field="close", universe=universe)
        ret = close.pct_change(fill_method=None)
        # Keep NaN where ret is NaN (untraded/not-yet-listed) so the factor
        # does not give a false 0 score to securities without price data.
        factor = (ret > 0.0).where(ret.notna()).rolling(20).mean()
        factor.index.name = "date"
        validate_factor_matrix(factor, universe, name=self.name)
        return factor