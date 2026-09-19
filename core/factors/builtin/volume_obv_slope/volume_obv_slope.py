"""OBV 资金流斜率因子 (分类: 量能).

底层逻辑：价涨量增、价跌量减的"量价配合"说明资金真实流入，用净资金流（OBV 式
累加成交量）的趋势斜率捕捉资金持续流入的标的。
与既有量能因子 mfi（典型价×量正负资金流之比） / psy20（上涨天数占比）信号来源不同。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.registry import register_factor
from core.factors.utils import pivot_price_field, validate_factor_matrix


@register_factor("volume_obv_slope")
class VolumeOBVSlope(FactorBuilder):
    """window 日内按收盘价涨跌方向累加成交量的资金流趋势斜率。"""

    registry_name = "volume_obv_slope"
    display_name = "OBV成交量斜率"
    category = "量能"
    description = "window 日内按收盘价涨跌方向累加成交量的资金流趋势斜率；资金持续净流入的标的未来5日相对占优。"
    formula = "F = OLS_slope(OBV_volume(P_t))_{t-w:t} / vol_mean"
    direction = "positive"
    params_schema = {
        "window": {"type": "int", "default": 20, "min": 1, "max": 252,
                   "step": 1, "label": "窗口天数"},
    }

    def __init__(self, window: int = 20) -> None:
        self.window = window

    @property
    def name(self) -> str:
        return "volume_obv_slope"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        close = pivot_price_field(price_data, field="close", universe=universe)
        volume = pivot_price_field(price_data, field="volume", universe=universe)
        w = self.window

        signed = np.sign(close.diff()) * volume
        obv = signed.cumsum()

        x = np.arange(w, dtype=float)
        x_m = x.mean()
        x_var = (1.0 / w) * ((x - x_m) ** 2).sum()

        def _slope(y: np.ndarray) -> float:
            if np.isnan(y).any():
                return np.nan
            y_m = y.mean()
            return float((((x - x_m) * (y - y_m)).sum() / w) / x_var)

        # 用窗口内成交量均值做缩放，得到相对资金流强度，避免量级差异主导
        vol_mean = volume.rolling(w).mean()
        factor = obv.rolling(w).apply(_slope, raw=True) / vol_mean.replace(0.0, np.nan)
        factor.index.name = "date"

        validate_factor_matrix(factor, universe, name=self.name)
        return factor