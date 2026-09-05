# -*- coding: utf-8 -*-
"""本地对照：复现 web 端 EAA 回测（真实库 + core 引擎），验证本地链路可信度。

仅本地诊断使用，非聚宽脚本。目标是复现 web 端 `_run_eaa` 的输出
（总收益 ~49.25%，年化 ~17.11%，最大回撤 ~16.09%），从而判断 web 与聚宽的差异
主体到底是"数据源/因子信息差异"还是"交易模型差异"。

口径完全对齐 web：
- universe = 数据库 active 30 只
- categories = core/builtin/factors.yaml 的 5 个生产因子
- composite = eaa_composite(cat_scores, exponents, beta)
- run_backtest(price_data=回测区间, factor_scores=composite,
               BacktestConfig(5d, top_n=5, weight_mode=score, max/min=1/0))
- 成交 = 后复权 close + shift(2)（web 口径）
- 回测区间 = 2024-01-01 起，到库最后交易日
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import quantstats as qs

from core.backtest.engine import BacktestConfig, run_backtest
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
    from pathlib import Path
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

    # 回测区间（仅 2024 起）与 file 列对齐
    close = pivot_price_field(price_data, field="close", universe=universe)
    close = close.sort_index()
    backtest_price = price_data[price_data["date"] >= pd.Timestamp(BACKTEST_START)]
    scores = composite.loc[composite.index >= pd.Timestamp(BACKTEST_START), :].sort_index()
    print(f"回测区间: {scores.index.min().date()} ~ {scores.index.max().date()}, "
          f"{len(scores)} 交易日")

    # web 口径 = 后复权 close + shift(2)
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

    rets = result.daily_returns.astype(float)
    eq = result.equity_curve.astype(float)
    total = eq.iloc[-1] - 1.0
    annual = qs.stats.cagr(rets, periods=252)
    vol = qs.stats.volatility(rets, periods=252)
    sharpe = qs.stats.sharpe(rets, periods=252)
    mdd = qs.stats.max_drawdown(rets)
    sortino = qs.stats.sortino(rets, periods=252)

    print("\n=== 本地复现 web / EAA 结果 ===")
    print(f"总收益        {total*100:.2f}%   (web: 49.25%)")
    print(f"年化收益      {annual*100:.2f}%   (web: 17.11%)")
    print(f"最大回撤      {mdd*100:.2f}%   (web: -16.09%)")
    print(f"夏普          {sharpe:.3f}   (web: 0.901)")
    print(f"策略波动率    {vol*100:.2f}%   (web: -)")
    print(f"索提诺        {sortino:.3f}   (web: -)")


if __name__ == "__main__":
    main()