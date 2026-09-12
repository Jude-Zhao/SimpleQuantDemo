# -*- coding: utf-8 -*-
"""本地对照：复现 web 端 EAA 回测（真实库 + 统一回测框架），验证本地链路可信度。

仅本地诊断使用，非聚宽脚本。目标是用统一框架（core.backtest + calculate_metrics）
复现 web 端 `_run_eaa` 的输出，从而判断 web 与聚宽的差异主体到底是
"数据源/因子信息差异"还是"交易模型差异"。

口径完全对齐 web：
- universe = 数据库 active 30 只
- categories = core/builtin/factors.yaml 的 5 个生产因子
- composite = eaa_composite(cat_scores, exponents, beta)
- run_backtest(price_data=回测区间, factor_scores=composite,
               BacktestConfig(5d, top_n=5, weight_mode=score, max/min=1/0))
- 成交 = 统一账本（T+1 收盘执行、份额账本、按成交金额收费）
- 回测区间 = 2024-01-01 起，到库最后交易日
- 指标 = core.backtest.calculate_metrics（唯一绩效实现，无风险利率默认 1%）
"""

from __future__ import annotations

import pandas as pd

from core.backtest import BacktestConfig, calculate_metrics, run_backtest
from core.data import SqliteDataSource
from core.factors.config import list_factor_categories
from core.factors.utils import pivot_price_field
from core.synthesis import build_category_scores, eaa_composite

DATA_START = "2023-01-01"   # warm-up（>120 交易日窗口）
BACKTEST_START = "2024-01-01"

# EAA 默认参数（与 web 生产、research/config.py 一致）
EXPONENTS = {"momentum": 0.5, "reversal": 1.0, "volatility": 1.0, "volume": 1.25}
BETA = 0.5
TOP_N = 5


def main() -> None:
    from research.config import DEFAULT_DB_PATH
    ds = SqliteDataSource(db_path=DEFAULT_DB_PATH)

    price_data, _macro, universe = ds.load_all(start_date=DATA_START, end_date=None)
    print(f"universe: {len(universe)} 只, 数据行 {len(price_data)}, 日期 "
          f"{price_data['date'].min()} ~ {price_data['date'].max()}")

    # 5 个生产因子分类
    categories = list_factor_categories()
    print("生产因子:", [i.name for c in categories if not c.is_empty for i in c.factors])

    # 构建合成矩阵（全程含 warm-up）
    cat_scores = build_category_scores(price_data, universe, categories)
    composite = eaa_composite(cat_scores, EXPONENTS, BETA)
    print(f"composite 形状: {composite.shape}, 日期 {composite.index.min()} ~ {composite.index.max()}")

    # 回测区间（仅 2024 起）与列对齐
    close = pivot_price_field(price_data, field="close", universe=universe)
    close = close.sort_index()
    backtest_price = price_data[price_data["date"] >= pd.Timestamp(BACKTEST_START)]
    scores = composite.loc[composite.index >= pd.Timestamp(BACKTEST_START), :].sort_index()
    print(f"回测区间: {scores.index.min().date()} ~ {scores.index.max().date()}, "
          f"{len(scores)} 交易日")

    # 统一账本（T+1 收盘执行、份额账本、按成交金额收费）
    result = run_backtest(
        price_data=backtest_price,
        factor_scores=scores,
        config=BacktestConfig(
            rebalance_freq="5d",
            top_n=TOP_N,
            max_weight=1.0,
            min_weight=0.0,
            weight_mode="score",   # EAA
        ),
    )

    m = calculate_metrics(result)
    print("\n=== 本地复现 web / EAA 结果（统一框架口径） ===")
    print(f"总收益        {m['total_return']*100:.2f}%")
    print(f"年化收益      {m['annual_return']*100:.2f}%")
    print(f"最大回撤      {m['max_drawdown']*100:.2f}%")
    print(f"夏普          {m['sharpe']:.3f}")
    print(f"策略波动率    {m['annual_volatility']*100:.2f}%")
    print(f"索提诺        {m['sortino']:.3f}")
    print(f"换手合计      {m['turnover_sum']:.2f}  费用合计 {m['cost_sum']:.6f}")


if __name__ == "__main__":
    main()
