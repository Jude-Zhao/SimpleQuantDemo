"""B6：调参脚本迁移测试（统一 API + 年度盈利比例）。

年度盈利比例构造（BUG-10）：输入为日收益序列，先经 calculate_yearly_returns
按年聚合出年度收益，再对年度收益算盈利比例。不是把年度收益列表直接喂给
比例函数——那样 [1, -1.1] 会得 0.5 而非 0。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.backtest import calculate_yearly_returns
from research.tune_strategy_params import (
    COST_BPS,
    SCORE_W_DRAWDOWN,
    SCORE_W_RETURN,
    SCORE_W_STABILITY,
    _backtest,
    _metrics,
    _score,
    _year_profit_ratio,
)


def _daily(series: list[float], start: str = "2026-01-05") -> pd.Series:
    return pd.Series(series, index=pd.bdate_range(start, periods=len(series)))


def test_year_profit_ratio_from_daily_returns() -> None:
    """日收益 [1, 1.1] → 年度收益 (1+1)×(1+1.1)−1=3.2 → 盈利比例 1。"""
    yearly = calculate_yearly_returns(_daily([1.0, 1.1]))
    assert yearly == pytest.approx({2026: 3.2}, abs=1e-12)
    assert _year_profit_ratio(yearly) == 1.0

    # 日收益 [1, -1.1] → 年度收益 (1+1)×(1−1.1)−1=−1.2 → 0
    yearly_loss = calculate_yearly_returns(_daily([1.0, -1.1]))
    assert yearly_loss == pytest.approx({2026: -1.2}, abs=1e-12)
    assert _year_profit_ratio(yearly_loss) == 0.0


def test_year_profit_ratio_consecutive_daily_all_ones() -> None:
    """日收益 [1..1]（跨两个年度）→ 每年收益 3.0 → 盈利比例 1。"""
    idx = pd.DatetimeIndex(["2025-12-30", "2025-12-31", "2026-01-02", "2026-01-05"])
    daily = pd.Series([1.0, 1.0, 1.0, 1.0], index=idx)  # 两天 2025、两天 2026
    yearly = calculate_yearly_returns(daily)
    assert set(yearly) == {2025, 2026}
    assert all(v == pytest.approx(3.0) for v in yearly.values())
    assert _year_profit_ratio(yearly) == 1.0


def test_year_profit_ratio_boundaries() -> None:
    """全盈 1、全亏 0、一盈一亏 0.5、零收益不算盈利、微小差异不发散、空 0。"""
    assert _year_profit_ratio({2024: 0.10, 2025: 0.11}) == 1.0
    assert _year_profit_ratio({2024: -0.10, 2025: -0.11}) == 0.0
    assert _year_profit_ratio({2024: 0.10, 2025: -0.11}) == 0.5
    # 零收益不算盈利
    assert _year_profit_ratio({2024: 0.0, 2025: 0.05}) == 0.5
    # 微小差异不发散（旧 worst/(worst-best) 会得 -49999.5 之类）
    score = _score({"annual": 0.05, "vol": 0.1, "mdd": -0.10,
                    "yearly": {2024: 0.10, 2025: 0.100001}})
    assert np.isfinite(score)
    # 空输入
    assert _year_profit_ratio({}) == 0.0
    assert _year_profit_ratio({2024: float("nan")}) == 0.0


def test_score_weights_preserved() -> None:
    """评分权重保持：RETURN=.5 / STABILITY=.3 / DRAWDOWN=.2，公式不变。"""
    assert (SCORE_W_RETURN, SCORE_W_STABILITY, SCORE_W_DRAWDOWN) == (0.5, 0.3, 0.2)
    m = {"annual": 0.10, "vol": 0.10, "mdd": -0.25, "yearly": {2024: 0.5, 2025: -0.5}}
    # 手算：ra = 0.10/0.10 = 1.0；stab = 0.5；mdd_pen = (-0.25)/(-0.25) = 1.0
    expected = 0.5 * 1.0 + 0.3 * 0.5 + 0.2 * (1.0 - 1.0)
    assert _score(m) == pytest.approx(expected)


def test_cost_bps_matches_framework_default() -> None:
    """COST_BPS 与统一框架一致取 0.5，不硬编码旧值 1。"""
    from core.backtest.models import ExecutionConfig

    assert COST_BPS == ExecutionConfig().transaction_cost_bps == 0.5


def test_backtest_uses_public_api_no_local_ledger() -> None:
    """_backtest 经统一框架执行：净值/费用/换手与 execute_backtest 直接结果一致。"""
    # 6 只证券（调参模块 TOP_N=5，需要合格数 >= 5 才生成目标行）
    dates = pd.bdate_range("2026-01-05", periods=30)
    rng = np.random.default_rng(3)
    cols = ["A", "B", "C", "D", "E", "F"]
    close = pd.DataFrame(
        np.cumprod(1.0 + rng.normal(0.0, 0.01, (30, 6)), axis=0),
        index=dates, columns=cols,
    )
    scores = pd.DataFrame(rng.uniform(0.1, 1.0, (30, 6)), index=dates, columns=cols)

    from core.backtest import BacktestConfig, ExecutionConfig
    from core.backtest.engine import execute_backtest
    from core.backtest.targets import build_target_weights

    cfg = BacktestConfig(rebalance_freq="5d", top_n=5, max_weight=1.0, min_weight=0.0,
                         weight_mode="equal", transaction_cost_bps=COST_BPS)
    plan = build_target_weights(scores, close.index, cfg)
    direct = execute_backtest(close, plan.target_weights,
                              ExecutionConfig(initial_cash=1.0, transaction_cost_bps=COST_BPS))

    # _backtest 接受 DataFrame composite（与调参脚本调用方式一致）
    result, n_reb = _backtest(close, scores, "equal")

    assert result.equity_curve.equals(direct.equity_curve)
    assert result.trades.equals(direct.trades)
    assert result.fees.equals(direct.fees)
    # _metrics 映射统一指标，不重算
    m = _metrics(result, n_reb)
    assert m["yearly"] == calculate_yearly_returns(result.daily_returns)
    assert m["n_reb"] == n_reb
