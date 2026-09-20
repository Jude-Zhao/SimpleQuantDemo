"""近120日相对历史峰值的回撤因子（分类: 波动；F22 更名以匹配窗口语义）."""

from __future__ import annotations

import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.registry import register_factor
from core.factors.utils import pivot_price_field, validate_factor_matrix


@register_factor("drawdown_120")
class Drawdown120Factor(FactorBuilder):
    """Deepest drawdown over the last 120 days vs the all-history peak.

    F22：名称必须如实反映窗口语义——回撤的峰值基准是**传入行情首日以来**
    的累计最高价（含 120 日窗口之外的历史），并非纯 120 日窗口内回撤；
    随后取最近 120 日回撤的最小值。因此结果随传入历史起点改变，各入口
    （Web 预热 / 研究长历史 / 因子评估）只有以一致的历史起点调用才可比。
    当日无有效价格输出 NaN（F06）。
    """

    registry_name = "drawdown_120"
    display_name = "近120日相对历史峰值回撤"
    category = "波动"
    description = (
        "近120日内相对传入行情首日以来累计峰值的最深回撤"
        "（峰值基准含窗口外历史，随历史起点改变），回撤越浅分越高"
    )
    formula = "min(近120日: close/cummax(自数据首日) - 1)；缺失日=NaN"
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
        # 当日无有效价格时输出 NaN，缺失日不得继承历史回撤
        factor = factor.where(close.notna())
        factor.index.name = "date"
        validate_factor_matrix(factor, universe, name=self.name)
        return factor