# -*- coding: utf-8 -*-
"""本地复刻 EAA 回测，用可参数化的"信号口径 + 执行滞后 + 现金缓冲 + 佣金"，
定量拆解本地 web 引擎（run_backtest）与聚宽 eaa_strategy.py 的差异来源。

核心时序定义（close-to-close）：
  决策日 B 的信号（composite 在 B 或 B-1）确定目标权重 w_B；
  w_B 于 close[B+exec_lag] 执行买入，首个收益区间 [B+exec_lag, B+exec_lag+1]，
  即权重在 return 索引 (B+exec_lag+1) 首度生效。

  · 本地 web（run_backtest shift(2)）：signal=B，exec_lag=1 → 权重生效于索引 B+2
  · 聚宽现写法：signal=B-1，exec_lag=0（在块首日 B 收盘执行，信号取前一日）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.factors.config import list_factor_categories
from core.factors.utils import pivot_price_field
from core.synthesis import build_category_scores, eaa_composite
from core.backtest.engine import run_backtest, BacktestConfig

DATA_START = "2023-01-01"
BACKTEST_START = "2024-01-01"
EXPONENTS = {"momentum": 0.5, "reversal": 1.0, "volatility": 1.0, "volume": 1.25}
BETA = 0.5
TOP_N = 5
PERIOD = 5
CASH_BUFFER = 0.995
COMMISSION = 0.00025  # 每边；round-trip ≈ 0.25bps*2 施加在换手上


def simulate(
    composite: pd.DataFrame,
    ret: pd.DataFrame,
    comp_idx_aligned: np.ndarray,
    n: int,
    signal_mode: str = "today",
    exec_lag: int = 0,
    fire_offset: int = 0,       # 首笔调仓的 0-based 交易索引（本地0，聚宽4）
    with_buffer: bool = False,
    with_commission: bool = False,
) -> float:
    rebal = list(range(fire_offset, n, PERIOD))

    def signal_row(t):
        if signal_mode == "prev":
            ci = comp_idx_aligned[t] - 1
            return composite.iloc[ci] if ci >= 0 else pd.Series(dtype=float)
        ci = comp_idx_aligned[t]
        return composite.iloc[ci] if ci >= 0 else pd.Series(dtype=float)

    # decision: B -> {sec: w}; 用 exec_lag 确定该权重首度生效的 return 索引
    decisions = {}
    for B in rebal:
        sig = signal_row(B).dropna()
        sig = sig[sig > 0.0]
        if len(sig) >= TOP_N:
            sel = sig.sort_values(ascending=False).head(TOP_N)
            decisions[B] = (sel / sel.sum()).to_dict()

    # weight_eff[k] = 在 return 索引 k 生效的权重
    wt = pd.DataFrame(0.0, index=np.arange(n), columns=ret.columns)
    for i, B in enumerate(rebal):
        first = B + exec_lag + 1
        last = (rebal[i + 1] + exec_lag + 1 - 1) if i + 1 < len(rebal) else n
        w = decisions.get(B)
        if w is None:
            continue
        for k in range(first, last):
            if k < n:
                wt.loc[k, list(w.keys())] = list(w.values())

    # 换手与佣金（在权重变化当天计）
    cost = np.zeros(n)
    prev = pd.Series(0.0, index=ret.columns)
    for k in range(n):
        cur = wt.loc[k]
        if with_commission and cur.abs().sum() > 0:
            to_ = (cur - prev).abs().sum()
            # 佣金按成交额：卖出腿 + 买入腿各 0.25bps；近似 0.25bps*|Δw|
            cost[k] = to_ * COMMISSION
        prev = cur.copy()

    gross = (wt.to_numpy() * ret.to_numpy()).sum(axis=1)  # n
    net = gross - cost  # n
    return np.cumprod(1.0 + net)[-1] - 1.0


def main() -> None:
    from pathlib import Path
    from research.config import DEFAULT_DB_PATH
    from core.data import SqliteDataSource

    ds = SqliteDataSource(db_path=DEFAULT_DB_PATH)
    price_data, _m, universe = ds.load_all(start_date=DATA_START, end_date=None)
    categories = list_factor_categories()
    cs = build_category_scores(price_data, universe, categories)
    composite = eaa_composite(cs, EXPONENTS, BETA).sort_index()

    close = pivot_price_field(price_data, field="close", universe=universe).sort_index()
    close_bt = close.loc[close.index >= pd.Timestamp(BACKTEST_START)].sort_index()
    dates = close_bt.index
    n = len(dates)
    ret = (close_bt / close_bt.shift(1) - 1.0).fillna(0.0)
    comp_idx_aligned = composite.index.get_indexer(dates)

    # 基准：真实引擎（本地 web 口径）
    from core.factors.config import list_factor_categories as _l
    categories2 = _l()
    cs2 = build_category_scores(price_data, universe, categories2)
    comp_full = eaa_composite(cs2, EXPONENTS, BETA)
    bt_price = price_data[price_data["date"] >= pd.Timestamp(BACKTEST_START)]
    scores = comp_full.loc[comp_full.index >= pd.Timestamp(BACKTEST_START)].sort_index()
    res = run_backtest(
        price_data=bt_price, factor_scores=scores,
        config=BacktestConfig(rebalance_freq="5d", top_n=TOP_N,
                              max_weight=1.0, min_weight=0.0, weight_mode="score"),
    )
    engine_total = res.equity_curve.iloc[-1] - 1.0

    print(f"回测区间 {dates[0].date()} ~ {dates[-1].date()}, {n} 交易日")
    print(f"[引擎] run_backtest 本地 web 口径            = {engine_total*100:8.2f}%")

    scenarios = [
        # (name, signal_mode, exec_lag, fire_offset, buffer, commission)
        ("聚宽现写法 signal=prev, fire=块尾, exec=当     ", "prev",  0, 4, False, False),
        ("聚宽现写法 + 现金缓冲                           ", "prev",  0, 4, True,  False),
        ("聚宽现写法 + 现金缓冲 + 佣金                    ", "prev",  0, 4, True,  True),
        ("聚宽现写法仅改fire=块首(先隔离offset影响)        ", "prev",  0, 0, False, False),
        ("[拟定修复] fire=块首+1日, signal=prev → 等同本地   ", "prev",  0, 1, False, False),
        ("本地时序再验证 signal=当天, fire=块首, exec=B+1  ", "today", 1, 0, False, False),
    ]
    for name, smode, lag, foff, buf, comm in scenarios:
        tot = simulate(composite, ret, comp_idx_aligned, n,
                       signal_mode=smode, exec_lag=lag, fire_offset=foff,
                       with_buffer=buf, with_commission=comm)
        print(f"[模拟] {name} = {tot*100:8.2f}%")


if __name__ == "__main__":
    main()