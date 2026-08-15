"""60-day momentum reversal factor (分类: 反转)."""

from __future__ import annotations

import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.registry import register_factor
from core.factors.utils import pivot_price_field, validate_factor_matrix


@register_factor("momentum_60_reversal")
class Momentum60ReversalFactor(FactorBuilder):
    """60-day momentum reversal (negated)."""

    registry_name = "momentum_60_reversal"
    display_name = "60日动量反转"
    category = "反转"
    description = "60日动量的相反数，中期风格/行业反转"
    formula = "-(close/close.shift(60)-1)"
    direction = "positive"
    params_schema: dict = {}

    @property
    def name(self) -> str:
        return "momentum_60_reversal"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        close = pivot_price_field(price_data, field="close", universe=universe)
        momentum_60 = close.pct_change(periods=60, fill_method=None)
        factor = -momentum_60
        factor.index.name = "date"
        validate_factor_matrix(factor, universe, name=self.name)
        return factor