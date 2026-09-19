"""线性回归斜率动量因子 (分类: 动量).

底层逻辑：趋势越陡峭且越"干净"（斜率相对于噪声更大）的标的，其趋势延续的概率
越高，用对数价格线性回归斜率来预测未来 5 日收益。
与既有动量因子 macd_hist（MACD 柱状图，基于指数均线交叉）信号来源不同。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.registry import register_factor
from core.factors.utils import pivot_price_field, validate_factor_matrix


@register_factor("momentum_linreg_slope")
class MomentumLinRegSlope(FactorBuilder):
    """过去 window 日对数价格 OLS 线性回归斜率，经同期波动率标准化（类 t 统计量）。"""

    registry_name = "momentum_linreg_slope"
    display_name = "线性回归斜率动量"
    category = "动量"
    description = "过去一个窗口内对数价格的线性回归斜率除以同期收益标准差；斜率越陡、噪声越小时动量越强，未来5日延续上涨的概率越高。"
    formula = "F = OLS_slope(ln(P))_{t-w:t} / std(dlnP)_{t-w:t}"
    direction = "positive"
    params_schema = {
        "window": {"type": "int", "default": 20, "min": 1, "max": 252,
                   "step": 1, "label": "窗口天数"},
    }

    def __init__(self, window: int = 20) -> None:
        self.window = window

    @property
    def name(self) -> str:
        return "momentum_linreg_slope"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        close = pivot_price_field(price_data, field="close", universe=universe)
        w = self.window
        logclose = np.log(close)
        x = np.arange(w, dtype=float)
        x_m = x.mean()
        x_var = (1.0 / w) * ((x - x_m) ** 2).sum()

        def _slope(y: np.ndarray) -> float:
            if np.isnan(y).any():
                return np.nan
            y_m = y.mean()
            slope = (((x - x_m) * (y - y_m)).sum() / w) / x_var
            return float(slope)

        def _vol(y: np.ndarray) -> float:
            return float(np.std(y))

        slope = logclose.rolling(w).apply(_slope, raw=True)
        rets = logclose.diff()
        vol = rets.rolling(w).apply(_vol, raw=True)
        factor = slope / vol.replace(0.0, np.nan)
        factor.index.name = "date"

        validate_factor_matrix(factor, universe, name=self.name)
        return factor