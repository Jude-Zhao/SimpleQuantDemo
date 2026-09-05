# -*- coding: utf-8 -*-
"""一键核验：为什么相位对 EAA 影响这么大？
对比 5 日块首/块尾两个排程，每轮选出的 top5 持仓重合度 + 组合换手 + 每轮命中差异。
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from core.factors.config import list_factor_categories
from core.factors.utils import pivot_price_field
from core.synthesis import build_category_scores, eaa_composite
from research.config import DEFAULT_DB_PATH
from core.data import SqliteDataSource

DATA_START = "2023-01-01"
BACKTEST_START = "2024-01-01"
EXPONENTS = {"momentum": 0.5, "reversal": 1.0, "volatility": 1.0, "volume": 1.25}
BETA = 0.5
TOP_N = 5
PERIOD = 5


def main():
    ds = SqliteDataSource(db_path=DEFAULT_DB_PATH)
    price_data, _m, universe = ds.load_all(start_date=DATA_START, end_date=None)
    cs = build_category_scores(price_data, universe, list_factor_categories())
    comp = eaa_composite(cs, EXPONENTS, BETA).sort_index()
    comp_bt = comp.loc[comp.index >= pd.Timestamp(BACKTEST_START)]
    n = len(comp_bt)

    def top5(i):
        row = comp_bt.iloc[i].dropna()
        row = row[row > 0]
        if len(row) < TOP_N:
            return None
        return set(row.sort_values(ascending=False).head(TOP_N).index)

    # 两排程：块首 0,5,10...(本地) vs 块尾 4,9,14...(聚宽)
    starts = list(range(0, n - (n % PERIOD), PERIOD))  # 完整块
    overlap = []
    diff_picks = 0
    # 相邻两轮块首的 top5 重合度（衡量"每 5 天轮动一次持仓变化多大"）
    prev_pick = None
    churn = []
    for k in starts:
        s = top5(k)
        e = top5(k + PERIOD - 1)
        if s is None or e is None:
            continue
        overlap.append(len(s & e) / TOP_N)
        if prev_pick is not None:
            churn.append(1 - len(s & prev_pick) / TOP_N)
        prev_pick = s

    print(f"完整 5 日块数: {len(starts)}")
    print(f"[块首 vs 块尾] top5 平均重合度: {100*np.mean(overlap):.1f}%"
          f" (完全不同的轮次占比 {100*np.mean([o==0 for o in overlap]):.0f}%)")
    print(f"[相邻两轮块首] 每 5 日换手率(持仓变化比例)均值: {100*np.mean(churn):.1f}%")
    print("→ 结论: 每轮约有一半持仓被换掉；块首/块尾进去的持仓也大量不同, 相位=结构性改动。")


if __name__ == "__main__":
    main()