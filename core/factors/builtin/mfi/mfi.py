"""14-day Money Flow Index factor (分类: 量能)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.registry import register_factor
from core.factors.utils import pivot_price_field, validate_factor_matrix


@register_factor("mfi")
class MFIFactor(FactorBuilder):
    """14-day Money Flow Index, volume-based (live DB has amount=0 for ETFs)."""

    registry_name = "mfi"
    display_name = "14日资金流量指标"
    category = "量能"
    description = "典型价格变化×成交量的14日正负资金流之比(volume替代amount)"
    formula = "pos_flow/(pos_flow+neg_flow); flow=TP.diff()*volume, TP=(H+L+C)/3"
    direction = "positive"
    params_schema: dict = {}

    @property
    def name(self) -> str:
        return "mfi"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        high = pivot_price_field(price_data, field="high", universe=universe)
        low = pivot_price_field(price_data, field="low", universe=universe)
        close = pivot_price_field(price_data, field="close", universe=universe)
        volume = pivot_price_field(price_data, field="volume", universe=universe)

        typical = (high + low + close) / 3.0
        flow = typical.diff() * volume
        pos_flow = flow.where(flow > 0.0, 0.0).rolling(14).sum()
        neg_flow = (-flow.where(flow < 0.0, 0.0)).rolling(14).sum()
        denom = (pos_flow + neg_flow).replace(0.0, np.nan)
        factor = pos_flow / denom
        # 窗口内任一流量不可计算（缺失观测）即无效，缺失不得当作零流量参与窗口
        factor = factor.where(flow.rolling(14).count() == 14)
        factor.index.name = "date"
        validate_factor_matrix(factor, universe, name=self.name)
        return factor