"""B3：build_target_weights 稀疏目标与决策日志测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.backtest.models import BacktestConfig
from core.backtest.targets import ISSUE_COLUMNS, build_target_weights
from core.optimization import OptimizationError

DATES = pd.date_range("2026-01-05", periods=10, freq="D")


def _scores(rows: dict[int, dict[str, float]], cols=("A", "B", "C")) -> pd.DataFrame:
    scores = pd.DataFrame(np.nan, index=DATES, columns=list(cols))
    for i, values in rows.items():
        for sec, v in values.items():
            scores.loc[DATES[i], sec] = v
    return scores


def _cfg(top_n: int = 2, **kw) -> BacktestConfig:
    return BacktestConfig(rebalance_freq="5d", top_n=top_n, max_weight=1.0, min_weight=0.0, **kw)


def test_sparse_rows_only_on_created_dates() -> None:
    """目标行只出现在 target_created 日期；缺行=无新订单。"""
    scores = _scores({0: {"A": 3.0, "B": 2.0, "C": 1.0}})
    plan = build_target_weights(scores, DATES, _cfg())
    assert list(plan.target_weights.index) == [DATES[0]]
    # 全列都有值（未选中证券显式为 0 → 明确清仓语义）
    assert plan.target_weights.loc[DATES[0]].notna().all()
    assert plan.target_weights.loc[DATES[0], "C"] == 0.0


def test_decision_log_covers_all_plan_dates() -> None:
    """每个计划决策日都有日志；跳过日无目标行。"""
    scores = _scores({0: {"A": 3.0, "B": 2.0, "C": 1.0}, 5: {"A": 1.0}})
    plan = build_target_weights(scores, DATES, _cfg(top_n=2))
    assert plan.rebalance_dates.tolist() == [DATES[0], DATES[5]]
    assert [e["status"] for e in plan.decision_log] == [
        "target_created",
        "skipped_insufficient",
    ]
    assert plan.decision_log[1]["eligible_count"] == 1


def test_equal_weight_targets() -> None:
    scores = _scores({0: {"A": 3.0, "B": 2.0, "C": 1.0}})
    plan = build_target_weights(scores, DATES, _cfg(top_n=2))
    row = plan.target_weights.loc[DATES[0]]
    assert row["A"] == pytest.approx(0.5)
    assert row["B"] == pytest.approx(0.5)
    assert row.sum() == pytest.approx(1.0)


def test_score_weighted_targets() -> None:
    scores = _scores({0: {"A": 3.0, "B": 1.0, "C": float("nan")}})
    plan = build_target_weights(
        scores, DATES, _cfg(top_n=2, weight_mode="score")
    )
    row = plan.target_weights.loc[DATES[0]]
    assert row["A"] == pytest.approx(0.75)
    assert row["B"] == pytest.approx(0.25)
    assert row["C"] == 0.0


def test_exclusions_from_issues_and_score_missing_fallback() -> None:
    """exclusions 包含 issues 全部无效实例；无 issue 的 NaN 分数补 score_missing。"""
    scores = _scores({0: {"A": 3.0, "C": 1.0}})  # B 缺分数且无 issue 记录
    issues = pd.DataFrame(
        [
            {
                "date": DATES[0],
                "sec": "C",
                "category": "volume",
                "factor": "mfi",
                "instance_index": 0,
                "params": {"window": 20},
                "reason": "non_finite",
            }
        ],
        columns=ISSUE_COLUMNS,
    )
    plan = build_target_weights(scores, DATES, _cfg(top_n=2), decision_issues=issues)
    entry = plan.decision_log[0]
    by_sec = {e["sec"]: e for e in entry["exclusions"]}
    assert by_sec["C"]["reason"] == "non_finite"
    assert by_sec["C"]["factor"] == "mfi"
    assert by_sec["C"]["params"] == {"window": 20}
    assert by_sec["B"]["reason"] == "score_missing"


def test_eligible_counts_with_negative_scores_equal_mode() -> None:
    """equal 模式：负分数仍为有限合格分数，可被选中。"""
    scores = _scores({0: {"A": -1.0, "B": -2.0, "C": float("nan")}})
    plan = build_target_weights(scores, DATES, _cfg(top_n=2))
    row = plan.target_weights.loc[DATES[0]]
    assert row["A"] == pytest.approx(0.5)
    assert row["B"] == pytest.approx(0.5)


def test_score_mode_nonpositive_finite_scores_fail_fast() -> None:
    """score 模式：有限但非正分数不足以构成正权重组合 → 显式报错（fail-fast）。"""
    scores = _scores({0: {"A": -1.0, "B": -2.0, "C": 0.5}})
    with pytest.raises(OptimizationError):
        build_target_weights(scores, DATES, _cfg(top_n=2, weight_mode="score"))


def test_max_weight_feasibility_from_config() -> None:
    """显式配置的 max_weight 传入优化器并校验可行性（不静默改成 1/0）。"""
    scores = _scores({0: {"A": 3.0, "B": 2.0, "C": 1.0}})
    cfg = BacktestConfig(rebalance_freq="5d", top_n=5, max_weight=0.15, min_weight=0.0)
    with pytest.raises(OptimizationError):
        build_target_weights(scores, DATES, cfg)  # 1/5=0.2 > 0.15 不可行

    # 4 只合格证券、max_weight=0.3：1/4=0.25 可行，权重按配置执行
    scores4 = pd.DataFrame(
        {"A": 3.0, "B": 2.0, "C": 1.0, "D": 0.5}, index=[DATES[0]]
    )
    plan = build_target_weights(
        scores4, DATES, BacktestConfig(rebalance_freq="5d", top_n=4, max_weight=0.3, min_weight=0.0)
    )
    assert plan.target_weights.loc[DATES[0]].max() == pytest.approx(0.25)


def test_trading_dates_not_intersected_with_scores() -> None:
    """决策日来自 trading_dates 全集：scores 缺日期不压缩日历。"""
    scores = _scores({5: {"A": 3.0, "B": 2.0, "C": 1.0}})
    plan = build_target_weights(scores, DATES, _cfg())
    assert plan.rebalance_dates.tolist() == [DATES[0], DATES[5]]
    assert plan.decision_log[0]["decision_date"] == DATES[0]
    assert plan.decision_log[0]["eligible_count"] == 0


def test_empty_target_plan_shape() -> None:
    """全部跳过时目标矩阵为空但列齐全。"""
    scores = _scores({})
    plan = build_target_weights(scores, DATES, _cfg())
    assert plan.target_weights.empty
    assert list(plan.target_weights.columns) == ["A", "B", "C"]
    assert len(plan.decision_log) == 2
