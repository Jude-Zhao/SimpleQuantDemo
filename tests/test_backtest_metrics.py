"""B4：唯一指标函数 calculate_metrics / calculate_yearly_returns 的精确测试。

参考公式独立构造预期（不调用被测函数产生预期值）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.backtest.metrics import calculate_metrics, calculate_yearly_returns
from core.backtest.models import BacktestResult, MetricsConfig


def _make_result(
    equity: list[float],
    daily: list[float],
    *,
    fees: list[float] | None = None,
    turnover: list[float] | None = None,
    execution_log: list[dict] | None = None,
    initial_cash: float = 1.0,
    index: list[str] | None = None,
) -> BacktestResult:
    n = len(equity)
    idx = pd.DatetimeIndex(index or [f"2026-01-{i + 1:02d}" for i in range(n)], name="date")
    empty_df = pd.DataFrame(np.zeros((n, 1)), index=idx, columns=["A"])
    return BacktestResult(
        equity_curve=pd.Series(equity, index=idx, name="equity"),
        daily_returns=pd.Series(daily, index=idx, name="daily_return"),
        weights=empty_df,
        turnover=pd.Series(turnover or [0.0] * n, index=idx, name="turnover"),
        costs=pd.Series([0.0] * n, index=idx, name="cost"),
        rebalance_dates=pd.DatetimeIndex([], name="date"),
        fees=pd.Series(fees or [0.0] * n, index=idx, name="fee"),
        execution_log=execution_log or [],
        initial_cash=initial_cash,
    )


def test_exact_baseline_case() -> None:
    """净值 [1.0, 0.999, 0.999, 0.999]、日收益 [0, -0.001, 0, 0]：
    有效期间数 3、total_return=-0.001、max_drawdown=-0.001。"""
    result = _make_result(
        [1.0, 0.999, 0.999, 0.999],
        [0.0, -0.001, 0.0, 0.0],
    )
    m = calculate_metrics(result)
    assert m["total_return"] == pytest.approx(-0.001)
    assert m["max_drawdown"] == pytest.approx(-0.001)
    # 年化按 3 个有效期间
    assert m["annual_return"] == pytest.approx((1 - 0.001) ** (252 / 3) - 1, rel=1e-10)
    # 零收益日仍是有效期间：波动率 = std([-0.001,0,0], ddof=1)*sqrt(252)
    expected_vol = float(np.std([-0.001, 0.0, 0.0], ddof=1) * np.sqrt(252))
    assert m["annual_volatility"] == pytest.approx(expected_vol, rel=1e-10)


def test_profit_and_loss_with_reference_formulas() -> None:
    """有盈亏序列：sharpe/sortino 按全样本超额、不中心化。"""
    r = [0.0, 0.01, -0.005, 0.002]
    equity = list(np.cumprod(1 + np.array(r)))
    result = _make_result(equity, r)
    m = calculate_metrics(result, MetricsConfig(risk_free_rate=0.0))

    rf_daily = 0.0
    excess = np.array(r[1:]) - rf_daily
    n = 3
    total = equity[-1] - 1.0
    assert m["total_return"] == pytest.approx(total, rel=1e-12)
    assert m["annual_return"] == pytest.approx((1 + total) ** (252 / n) - 1, rel=1e-10)
    assert m["annual_volatility"] == pytest.approx(
        np.std(np.array(r[1:]), ddof=1) * np.sqrt(252), rel=1e-10
    )
    assert m["sharpe"] == pytest.approx(
        np.mean(excess) / np.std(excess, ddof=1) * np.sqrt(252), rel=1e-10
    )
    downside_rms = np.sqrt(np.mean(np.minimum(excess, 0.0) ** 2))
    assert m["sortino"] == pytest.approx(
        np.mean(excess) * np.sqrt(252) / downside_rms, rel=1e-10
    )
    # win_rate：正期间 / 非零期间 = 2/3
    assert m["win_rate"] == pytest.approx(2 / 3, rel=1e-12)
    assert m["max_drawdown"] == pytest.approx(equity[2] / equity[1] - 1, rel=1e-10)


def test_all_zero_returns() -> None:
    """全零（rf=0）：total 0、mdd 0、vol 0、sharpe 0、sortino 0（无下行）、win_rate 0。"""
    result = _make_result([1.0, 1.0, 1.0, 1.0], [0.0, 0.0, 0.0, 0.0])
    m = calculate_metrics(result, MetricsConfig(risk_free_rate=0.0))
    assert m["total_return"] == 0.0
    assert m["max_drawdown"] == 0.0
    assert m["annual_volatility"] == 0.0
    assert m["sharpe"] == 0.0
    assert m["sortino"] == 0.0
    assert m["calmar"] == 0.0
    assert m["win_rate"] == 0.0

    # 默认 rf=0.01 时全零名义收益的每日超额均为 -rf_daily（存在下行），
    # sortino 按定义为 -sqrt(252)，不是 0。
    m_default = calculate_metrics(result)
    assert m_default["sortino"] == pytest.approx(-np.sqrt(252), rel=1e-12)


def test_single_period() -> None:
    """单期间：n=1 → vol/sharpe 为 0；win_rate 仍有效。"""
    result = _make_result([1.0, 1.05], [0.0, 0.05])
    m = calculate_metrics(result)
    assert m["annual_volatility"] == 0.0
    assert m["sharpe"] == 0.0
    assert m["win_rate"] == 1.0
    assert m["total_return"] == pytest.approx(0.05)
    assert m["annual_return"] == pytest.approx(1.05**252 - 1, rel=1e-10)


def test_no_downside_sortino_zero() -> None:
    """全部正收益：无下行 → sortino=0（有限值约定）。"""
    result = _make_result([1.0, 1.01, 1.02], [0.0, 0.01, 0.0099])
    m = calculate_metrics(result, MetricsConfig(risk_free_rate=0.0))
    assert m["sortino"] == 0.0


def test_risk_free_rate_default_and_custom() -> None:
    """默认 rf=0.01；显式 0.02 改变 sharpe；rf 不进入净值。"""
    r = [0.0, 0.01, -0.002, 0.003]
    equity = list(np.cumprod(1 + np.array(r)))
    result = _make_result(equity, r)
    default_m = calculate_metrics(result)
    explicit_m = calculate_metrics(result, MetricsConfig(risk_free_rate=0.01))
    assert default_m["sharpe"] == explicit_m["sharpe"]

    custom = calculate_metrics(result, MetricsConfig(risk_free_rate=0.02))
    rf_daily = 1.02 ** (1 / 252) - 1
    excess = np.array(r[1:]) - rf_daily
    expected = np.mean(excess) / np.std(excess, ddof=1) * np.sqrt(252)
    assert custom["sharpe"] == pytest.approx(expected, rel=1e-10)
    assert custom["sharpe"] != pytest.approx(default_m["sharpe"], abs=1e-12)


def test_cost_sum_with_initial_cash() -> None:
    """initial_cash≠1 时 cost_sum = Σfees/initial_cash；另提供 fee_amount_sum。"""
    result = _make_result(
        [1.0, 0.999, 0.999],
        [0.0, -0.001, 0.0],
        fees=[0.0, 5.0, 0.0],
        initial_cash=1000.0,
    )
    m = calculate_metrics(result)
    assert m["fee_amount_sum"] == pytest.approx(5.0)
    assert m["cost_sum"] == pytest.approx(5.0 / 1000.0)
    # equity_curve 已按 initial_cash 归一，total_return 与本金无关
    assert m["total_return"] == pytest.approx(-0.001)


def test_rebalance_count_counts_executed_only() -> None:
    """rebalance_count = execution_log 中 status=executed 次数（no_trade 不计）。"""
    log = [
        {"status": "executed"},
        {"status": "no_trade"},
        {"status": "executed"},
        {"status": "cancelled_missing_price"},
        {"status": "unexecuted_end"},
    ]
    result = _make_result([1.0, 1.0], [0.0, 0.0], execution_log=log)
    m = calculate_metrics(result)
    assert m["rebalance_count"] == 2.0


def test_yearly_returns_grouping() -> None:
    """年界分组：(1+r) 连乘 - 1；首行基准 0 不改变年度值。"""
    idx = pd.to_datetime(["2025-12-31", "2026-01-02", "2026-01-05"])
    daily = pd.Series([0.0, 0.1, 0.2], index=idx)
    yearly = calculate_yearly_returns(daily)
    assert set(yearly) == {2025, 2026}
    assert yearly[2025] == pytest.approx(0.0)
    assert yearly[2026] == pytest.approx((1.1) * (1.2) - 1.0, rel=1e-12)


def test_yearly_returns_empty() -> None:
    assert calculate_yearly_returns(pd.Series(dtype=float)) == {}


def test_invalid_inputs_raise() -> None:
    """NaN 收益 / 非正净值 / annualization<=0 / rf<=-1 / 长度不一致 → ValueError。"""
    good = _make_result([1.0, 1.0], [0.0, 0.0])

    nan_daily = _make_result([1.0, 1.0], [0.0, float("nan")])
    with pytest.raises(ValueError):
        calculate_metrics(nan_daily)

    bad_equity = _make_result([1.0, 0.0], [0.0, -1.0])
    with pytest.raises(ValueError):
        calculate_metrics(bad_equity)

    with pytest.raises(ValueError):
        calculate_metrics(good, MetricsConfig(annualization=0))

    with pytest.raises(ValueError):
        calculate_metrics(good, MetricsConfig(risk_free_rate=-2.0))

    mismatched = BacktestResult(
        equity_curve=pd.Series([1.0, 1.0]),
        daily_returns=pd.Series([0.0, 0.0, 0.0]),
        weights=pd.DataFrame(),
        turnover=pd.Series(dtype=float),
        costs=pd.Series(dtype=float),
        rebalance_dates=pd.DatetimeIndex([]),
    )
    with pytest.raises(ValueError):
        calculate_metrics(mismatched)


def test_empty_result_returns_zero_metrics() -> None:
    result = BacktestResult(
        equity_curve=pd.Series(dtype=float, name="equity"),
        daily_returns=pd.Series(dtype=float, name="daily_return"),
        weights=pd.DataFrame(),
        turnover=pd.Series(dtype=float),
        costs=pd.Series(dtype=float),
        rebalance_dates=pd.DatetimeIndex([]),
    )
    m = calculate_metrics(result)
    assert all(v == 0.0 for v in m.values())


def test_raw_nav_without_initial_row_prepends_peak() -> None:
    """传入不含初始行的净值 [0.999, 0.999, 0.999]：回撤须为 -0.001 而非 0。"""
    result = _make_result([0.999, 0.999, 0.999], [0.0, 0.0, 0.0])
    m = calculate_metrics(result)
    assert m["max_drawdown"] == pytest.approx(-0.001)


def test_metrics_not_rounded() -> None:
    """指标不四舍五入（展示层才舍入）。"""
    result = _make_result([1.0, 1.0001234567], [0.0, 0.0001234567])
    m = calculate_metrics(result)
    assert m["total_return"] == pytest.approx(0.0001234567, rel=1e-9)
