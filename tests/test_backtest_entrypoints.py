"""B5：全入口使用同一框架的一致性测试。

用同一 close 与信号替换各入口的数据加载/因子构建：核心 calculate_metrics、
研究 calculate_backtest_summary、Web 序列化（_result_to_dict）三者的同名
指标与净值/日收益必须一致；不连业务库。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.backtest import BacktestConfig, calculate_metrics, run_backtest
from core.optimization import OptimizationConstraints as CoreOptimizationConstraints
from research.main import calculate_backtest_summary
from webapp.schemas.strategy import RunContext
from webapp.services.strategy_service import _result_to_dict

DATES = pd.bdate_range("2026-01-05", periods=40)
SECS = ["A", "B", "C"]


def _long_price(close: pd.DataFrame) -> pd.DataFrame:
    records = []
    for date, row in close.iterrows():
        for sec in close.columns:
            p = float(row[sec])
            records.append(
                {
                    "date": date,
                    "sec": sec,
                    "open": p,
                    "high": p,
                    "low": p,
                    "close": p,
                    "volume": 1000.0,
                    "amount": 1000.0,
                }
            )
    return pd.DataFrame(records)


def _pipeline(weight_mode: str):
    rng = np.random.default_rng(11)
    close = pd.DataFrame(
        np.cumprod(1.0 + rng.normal(0.0, 0.01, (len(DATES), len(SECS))), axis=0),
        index=DATES,
        columns=SECS,
    )
    scores = pd.DataFrame(rng.uniform(0.1, 1.0, (len(DATES), len(SECS))), index=DATES, columns=SECS)
    # 制造资格缺口：C 在部分日期缺分数（模拟预热/缺失）
    scores.iloc[:5, 2] = np.nan
    cfg = BacktestConfig(
        rebalance_freq="5d",
        top_n=2,
        max_weight=1.0,
        min_weight=0.0,
        weight_mode=weight_mode,
        transaction_cost_bps=0.5,
    )
    result = run_backtest(_long_price(close), scores, cfg)
    return result


@pytest.mark.parametrize("weight_mode", ["equal", "score"])
def test_web_and_research_and_core_metrics_identical(weight_mode: str) -> None:
    result = _pipeline(weight_mode)

    # 研究入口：pd.Series 包装器，只映射统一指标
    summary = calculate_backtest_summary(result)

    # Web 入口：_result_to_dict 序列化同一结果
    run_context = RunContext(
        strategy_type="faa" if weight_mode == "equal" else "eaa",
        params={"top_n": 2, "rebalance_freq": "5d"},
        sec_names={s: s for s in SECS},
        factor_categories=[],
        classifications={s: {} for s in SECS},
        constraints={"single_max_weight": 0.15},
    )
    payload = _result_to_dict(
        result=result,
        run_context=run_context,
        constraints=CoreOptimizationConstraints(),
        latest_weights={"A": 0.5, "B": 0.5},
        latest_data_date=str(DATES[-1].date()),
        latest_reason=None,
    )

    core_metrics = calculate_metrics(result)
    for key in (
        "total_return",
        "annual_return",
        "annual_volatility",
        "sharpe",
        "max_drawdown",
        "sortino",
        "calmar",
        "win_rate",
        "turnover_sum",
        "cost_sum",
        "rebalance_count",
    ):
        assert payload["metrics"][key] == pytest.approx(summary[key], rel=1e-12), key
        assert payload["metrics"][key] == pytest.approx(core_metrics[key], rel=1e-12), key

    # 净值/日收益逐点一致（Web 序列化不改变数值）
    for k, v in result.equity_curve.items():
        assert payload["equity_curve"][str(k.date())] == pytest.approx(float(v))
    for k, v in result.daily_returns.items():
        assert payload["daily_returns"][str(k.date())] == pytest.approx(float(v))
    assert payload["equity_curve"][str(DATES[0].date())] == pytest.approx(1.0)

    # 调度一致：计划决策日与执行日志按同一时间轴
    assert payload["rebalance_dates"] == [str(d.date()) for d in result.rebalance_dates]
    executed = [e for e in payload["execution_log"] if e["status"] == "executed"]
    assert len(executed) == int(core_metrics["rebalance_count"])
    # 每个执行日都有历史约束检查 + 一条 latest 检查
    history_checks = [c for c in payload["constraint_checks"] if c["scope"] == "history"]
    assert len(history_checks) == len(executed)
    assert any(c["scope"] == "latest" for c in payload["constraint_checks"])


def test_entrypoint_equity_identical_between_modes_of_same_inputs() -> None:
    """同一输入下，equal 模式的两次调用结果完全一致（入口不同不切换口径）。"""
    r1 = _pipeline("equal")
    r2 = _pipeline("equal")
    assert r1.equity_curve.equals(r2.equity_curve)
    assert r1.trades.equals(r2.trades)


def test_webapp_services_do_not_import_research() -> None:
    """依赖方向：webapp/services 只能依赖 core/models，禁止反向依赖 research。"""
    from pathlib import Path

    services_dir = Path(__file__).resolve().parents[1] / "webapp" / "services"
    offenders = []
    for path in services_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import research", "from research")):
                offenders.append(f"{path.name}: {stripped}")
    assert not offenders, f"webapp/services 不得导入 research: {offenders}"
