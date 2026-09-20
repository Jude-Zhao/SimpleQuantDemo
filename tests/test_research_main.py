from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.backtest import BacktestConfig
from core.backtest.targets import build_target_weights
from core.calendar import generate_rebalance_dates
from research.config import ResearchConfig, default_research_config
from research.main import (
    _build_composite,
    _evaluate_factors,
    _slice_backtest_window,
    calculate_backtest_summary,
    run_research,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)


def _research_config(tmp_path: Path, test_db_path: Path):
    return replace(
        default_research_config(output_dir=tmp_path),
        db_path=test_db_path,
        start_date="2024-06-03",
        end_date="2025-12-31",
    )


def test_run_research_writes_outputs(tmp_path: Path, test_db_path: Path) -> None:
    config = _research_config(tmp_path, test_db_path)

    result = run_research(config)

    assert result.selected_factors
    assert {"total_return", "annual_return", "max_drawdown"}.issubset(result.summary.index)
    assert result.backtest.equity_curve.dropna().iloc[-1] > 0
    for path in result.output_paths.values():
        assert path.exists()
    summary = pd.read_csv(result.output_paths["summary"], index_col=0)
    factor_stats = pd.read_csv(result.output_paths["factor_stats"], index_col=0, header=[0, 1])
    assert "value" in summary.columns
    assert len(factor_stats) < len(result.backtest.equity_curve)


def test_run_research_eaa(tmp_path: Path, test_db_path: Path) -> None:
    config = replace(
        _research_config(tmp_path, test_db_path),
        strategy_type="eaa",
        weight_mode="score",
    )

    result = run_research(config)

    assert result.selected_factors
    assert result.backtest.equity_curve.dropna().iloc[-1] > 0
    assert (tmp_path / "summary.csv").exists()
    assert (tmp_path / "synthesized_scores.csv").exists()


def test_calculate_backtest_summary_has_expected_fields(tmp_path: Path, test_db_path: Path) -> None:
    config = _research_config(tmp_path, test_db_path)
    result = run_research(config)

    summary = calculate_backtest_summary(result.backtest)

    assert summary["rebalance_count"] > 0
    assert summary["cost_sum"] >= 0
    assert -1 <= summary["max_drawdown"] <= 0
    # Performance metrics come from the unified framework (core.backtest.metrics).
    assert "sortino" in summary.index
    assert "calmar" in summary.index
    assert summary["sharpe"] == summary["sharpe"]  # not NaN on a valid run


def test_research_cli_runs(tmp_path: Path, test_db_path: Path) -> None:
    completed = subprocess.run(
        [
            str(PYTHON),
            "-m",
            "research.main",
            "--db-path",
            str(test_db_path),
            "--start-date",
            "2024-06-03",
            "--end-date",
            "2025-12-31",
            "--output-dir",
            str(tmp_path),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Research pipeline completed." in completed.stdout
    assert (tmp_path / "summary.csv").exists()


# ── F07: 回测窗口保留全部行情日期（分数 reindex，而非 intersection 删日）──


def _close_frame(dates: list[str], secs: list[str]) -> pd.DataFrame:
    idx = pd.DatetimeIndex(dates)
    values = 10.0 + np.arange(len(idx), dtype=float)[:, None] * 0.1
    return pd.DataFrame(
        np.repeat(values, len(secs), axis=1),
        index=idx,
        columns=list(secs),
    )


def test_missing_signal_day_keeps_price_date():
    """F07: 合成矩阵缺行时不得删除真实价格日——close 全保留，缺日分数为 NaN 行。

    旧实现 close.index.intersection(composite.index) 会丢掉 01-03 行情，
    把 T+1 成交从 01-03 推迟到 01-04，净值序列少一日。
    """
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    close = _close_frame(dates, ["A", "B"])
    composite = pd.DataFrame(
        {"A": [1.0, 2.0], "B": [0.5, 0.1]},
        index=pd.DatetimeIndex(["2024-01-02", "2024-01-04"]),  # 插件因子缺 01-03 行
    )

    clipped, scores = _slice_backtest_window(close, composite, pd.Timestamp("2024-01-02"))

    assert list(clipped.index) == list(pd.DatetimeIndex(dates))
    assert clipped.loc["2024-01-03", "A"] == close.loc["2024-01-03", "A"]
    assert np.isnan(scores.loc["2024-01-03"]).all()
    assert scores.loc["2024-01-02", "A"] == 1.0
    assert scores.loc["2024-01-04", "B"] == 0.1
    assert list(clipped.columns) == ["A", "B"]
    assert list(scores.columns) == ["A", "B"]


def test_eval_start_clips_window():
    """eval_start 仍生效：更早的行情日期不进入回测窗口。"""
    close = _close_frame(["2023-12-29", "2024-01-02", "2024-01-03"], ["A"])
    composite = pd.DataFrame({"A": [9.0, 1.0, 2.0]}, index=close.index)

    clipped, scores = _slice_backtest_window(close, composite, pd.Timestamp("2024-01-02"))

    assert list(clipped.index) == list(pd.DatetimeIndex(["2024-01-02", "2024-01-03"]))
    assert list(scores.index) == list(clipped.index)


def test_factor_dates_do_not_change_execution_days():
    """F07 验收：因子缺行不改变同一价格输入的调仓决策日（T+1 执行日）；
    缺信号决策仅跳过新目标，其余决策日目标逐值不变。"""
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"]
    close = _close_frame(dates, ["A", "B", "C"])
    full = pd.DataFrame(
        {
            "A": [3.0, 2.0, 1.0, 3.0, 2.0, 1.0],
            "B": [2.0, 3.0, 2.0, 1.0, 3.0, 2.0],
            "C": [1.0, 1.0, 3.0, 2.0, 1.0, 3.0],
        },
        index=close.index,
    )
    partial = full.drop(index=pd.Timestamp("2024-01-02"))  # 插件因子缺首个决策日

    cfg = BacktestConfig(
        rebalance_freq="5d",
        top_n=2,
        max_weight=1.0,
        min_weight=0.0,
        weight_mode="equal",
    )
    plan_full = build_target_weights(full, close.index, cfg)
    plan_partial = build_target_weights(partial, close.index, cfg)

    # 决策日只由行情日期决定（6 日按 5 日一块 → 首日与第 6 日）
    assert len(plan_full.rebalance_dates) == 2
    assert list(plan_partial.rebalance_dates) == list(plan_full.rebalance_dates)

    first = plan_full.rebalance_dates[0]
    entry = next(e for e in plan_partial.decision_log if e["decision_date"] == first)
    assert entry["status"] == "skipped_insufficient"
    assert entry["eligible_count"] == 0
    assert first not in plan_partial.target_weights.index
    for d in plan_full.rebalance_dates[1:]:
        assert plan_partial.target_weights.loc[d].equals(plan_full.target_weights.loc[d])


# ── F19: 研究入口同样只把启用类别的资格明细传给决策日志 ────────────────


def test_build_composite_filters_disabled_category_issues(monkeypatch):
    """FAA/EAA 两分支：detail.issues 过滤后仅剩启用（正权重/正指数）类别。"""
    import research.main as research_main
    from core.synthesis import CategoryBuildResult

    dates = pd.bdate_range("2024-01-02", periods=6)
    secs = ["A1", "S2", "S3"]
    momentum = pd.DataFrame(1.0, index=dates, columns=secs)
    momentum["S2"] = np.nan
    volume = pd.DataFrame(1.0, index=dates, columns=secs)
    volume["S3"] = np.inf
    issue_rows = [
        {"date": d, "sec": "S2", "category": "momentum", "factor": "stub",
         "instance_index": 0, "params": {}, "reason": "missing"}
        for d in dates
    ] + [
        {"date": d, "sec": "S3", "category": "volume", "factor": "stub",
         "instance_index": 0, "params": {}, "reason": "non_finite"}
        for d in dates
    ]
    detail = CategoryBuildResult(
        scores={"momentum": momentum, "volume": volume},
        issues=pd.DataFrame(
            issue_rows,
            columns=["date", "sec", "category", "factor", "instance_index", "params", "reason"],
        ),
    )
    monkeypatch.setattr(
        research_main, "build_category_scores_with_details", lambda *args, **kwargs: detail
    )
    price_data = pd.DataFrame(
        [
            {"date": d, "sec": s, "open": 100.0, "high": 100.0, "low": 100.0,
             "close": 100.0, "volume": 1000.0, "amount": 1e6}
            for d in dates
            for s in secs
        ]
    )

    config = ResearchConfig(
        strategy_type="faa", class_weights={"momentum": 1.0, "volume": 0.0}
    )
    composite, issues = _build_composite(price_data, secs, (), config)
    assert (issues["category"] == "momentum").all()

    config = ResearchConfig(
        strategy_type="eaa", exponents={"momentum": 1.0, "volume": 0.0}, beta=0.5
    )
    composite, issues = _build_composite(price_data, secs, (), config)
    assert (issues["category"] == "momentum").all()


# ── F20: IC 采样网格锚定评估区间（预热长度不影响相位）───────────────────


def _ic_schedule_frame(
    master: pd.DatetimeIndex, start: int, end: int, secs: list[str]
) -> pd.DataFrame:
    """构造长表价格数据：价格由主日历位置决定，预热前缀不改变评估日价格。

    各证券斜率不同（1.0/1.5/2.0），保证前瞻收益有截面区分度、IC 可定义。
    """
    rows = []
    for pos in range(start, end):
        for k, s in enumerate(secs):
            close = 100.0 + pos * (1.0 + 0.5 * k)
            rows.append(
                {"date": master[pos], "sec": s, "open": close, "high": close,
                 "low": close, "close": close, "volume": 1000.0, "amount": 1e6}
            )
    return pd.DataFrame(rows)


def _ic_schedule_panel(price_data: pd.DataFrame, secs: list[str]) -> dict[str, pd.DataFrame]:
    dates = pd.DatetimeIndex(price_data["date"].unique()).sort_values()
    factor = pd.DataFrame({s: float(k + 1) for k, s in enumerate(secs)}, index=dates)
    return {"sample": factor}


def test_ic_dates_anchor_at_eval_start_with_warmup():
    """F20 审计证据：预热 2 个交易日时首次 IC 采样必须是评估区间首日。

    旧行为：网格在全量（含预热）索引上生成后过滤 ≥ eval_start，
    首次 IC 落在 master[5]；回测首决策日是 master[4]（master 从 0 起算）。
    """
    master = pd.bdate_range("2024-01-02", periods=15)
    secs = ["A", "B", "C"]
    price_data = _ic_schedule_frame(master, 2, 15, secs)
    config = ResearchConfig(ic_min_observations=3, icir_window=3, icir_min_periods=2)
    eval_start = master[2]

    ic, _, _, _ = _evaluate_factors(
        price_data, _ic_schedule_panel(price_data, secs), config, eval_start=eval_start
    )

    ic_idx = ic["sample"].index
    assert len(ic_idx) >= 2
    assert ic_idx[0] == eval_start
    # IC 采样日是回测网格的前缀（尾部不足前瞻期的网格点无 IC 属预期）
    dates = pd.DatetimeIndex(price_data["date"].unique()).sort_values()
    backtest_dates = generate_rebalance_dates(dates[dates >= eval_start], "5d")
    assert list(ic_idx) == list(backtest_dates[: len(ic_idx)])


@pytest.mark.parametrize("freq", ["5d", "weekly", "monthly"])
def test_ic_schedule_invariant_to_warmup_length(freq):
    """F20 验收：预热增加 0–4 个交易日，同一评估区间的 IC/RankIC 采样序列
    逐值不变，且首个采样日=评估区间首日；周/月频首周期边界同样锚定。"""
    master = pd.bdate_range("2024-01-02", periods=25)
    secs = ["A", "B", "C"]
    eval_start = master[5]  # 周二——周/月频首周期均为残期，边界敏感

    baseline = None
    for warmup in range(5):
        price_data = _ic_schedule_frame(master, 5 - warmup, 25, secs)
        config = ResearchConfig(
            rebalance_freq=freq, ic_min_observations=3, icir_window=3, icir_min_periods=2
        )
        ic, rank_ic, _, _ = _evaluate_factors(
            price_data, _ic_schedule_panel(price_data, secs), config, eval_start=eval_start
        )

        assert ic["sample"].index[0] == eval_start, f"warmup={warmup}"
        if baseline is None:
            baseline = (ic["sample"], rank_ic["sample"])
        else:
            pd.testing.assert_series_equal(ic["sample"], baseline[0])
            pd.testing.assert_series_equal(rank_ic["sample"], baseline[1])