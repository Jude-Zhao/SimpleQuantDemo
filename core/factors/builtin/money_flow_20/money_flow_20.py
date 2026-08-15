"""20-day money-flow factor (分类: 量能)."""

from __future__ import annotations

import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.registry import register_factor
from core.factors.utils import pivot_price_field, validate_factor_matrix


@register_factor("money_flow_20")
class MoneyFlow20Factor(FactorBuilder):
    """20-day volume-weighted return money-flow direction.

    Uses ``volume`` as the weight proxy (the live DB has amount=0 for all
    active ETFs; user confirmed 2026-08-15).
    """

    registry_name = "money_flow_20"
    display_name = "20日资金流方向"
    category = "量能"
    description = "20日成交量加权的收益(volume替代amount)，资金流方向"
    formula = "sum(ret*volume,20)/sum(volume,20)"
    direction = "positive"
    params_schema: dict = {}

    @property
    def name(self) -> str:
        return "money_flow_20"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        close = pivot_price_field(price_data, field="close", universe=universe)
        volume = pivot_price_field(price_data, field="volume", universe=universe)
        ret = close.pct_change(fill_method=None)
        vol_sum = volume.rolling(20).sum().replace(0.0, pd.NA)
        factor = (ret * volume).rolling(20).sum() / vol_sum
        factor.index.name = "date"
        validate_factor_matrix(factor, universe, name=self.name)
        return factor