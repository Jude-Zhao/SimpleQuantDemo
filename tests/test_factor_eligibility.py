"""B3 / OPT-03：严格因子资格测试（携带因子身份的类别构建 + 合成 AND 语义）。

固定样例：A 完整、B 动量 NaN、C 量能 inf：
- top_n=1 应选 A 且记录 B/C 的全部无效原因；
- top_n=2 跳过且日志 eligible_count=1。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.backtest.models import BacktestConfig
from core.backtest.targets import build_target_weights
from core.factors.base import FactorBuilder
from core.factors.config import FactorCategory, FactorInstance
from core.synthesis import (
    build_category_scores_with_details,
    eaa_composite,
    enabled_category_keys,
    faa_composite,
    filter_issues_by_categories,
)

DATES = pd.date_range("2026-01-05", periods=6, freq="D")
SECS = ["A", "B", "C"]


def _price_data() -> pd.DataFrame:
    records = []
    for date in DATES:
        for sec in SECS:
            records.append(
                {
                    "date": date,
                    "sec": sec,
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 1000.0,
                    "amount": 1000.0,
                }
            )
    return pd.DataFrame(records)


class StubFactor(FactorBuilder):
    """测试替身：params.nan_secs 全 NaN；params.inf_secs 全 +inf。"""

    registry_name = "stub"
    display_name = "stub"

    def __init__(self, nan_secs=None, inf_secs=None, **params):
        self.nan_secs = set(nan_secs or [])
        self.inf_secs = set(inf_secs or [])

    @property
    def name(self) -> str:
        return "stub"

    def build(self, price_data, macro_data, universe) -> pd.DataFrame:
        dates = pd.DatetimeIndex(sorted(price_data["date"].unique()))
        mat = pd.DataFrame(1.0, index=dates, columns=list(universe))
        for sec in self.nan_secs:
            mat[sec] = np.nan
        for sec in self.inf_secs:
            mat[sec] = np.inf
        return mat


def _stub_resolver(name: str):
    return StubFactor if name == "stub" else None


def _momentum(*instances) -> FactorCategory:
    return FactorCategory(
        key="momentum",
        display_name="动量",
        factors=tuple(instances) if instances else (FactorInstance(name="stub", params={}),),
    )


def _volume(*instances) -> FactorCategory:
    return FactorCategory(
        key="volume",
        display_name="量能",
        factors=tuple(instances) if instances else (FactorInstance(name="stub", params={}),),
    )


def _detail(momentum: FactorCategory, volume: FactorCategory):
    """构建类别得分 + 资格明细。"""
    return build_category_scores_with_details(
        _price_data(), SECS, (momentum, volume), resolver=_stub_resolver
    )


def _bc_invalid_detail():
    """固定样例：A 完整、B 动量 NaN（missing）、C 量能 inf（non_finite）。"""
    return _detail(
        _momentum(FactorInstance(name="stub", params={"nan_secs": ["B"]})),
        _volume(FactorInstance(name="stub", params={"inf_secs": ["C"]})),
    )


def test_nan_and_inf_recorded_with_full_reasons() -> None:
    """B 动量 NaN(missing)、C 量能 inf(non_finite)：类别得分 NaN，issues 全记录。"""
    detail = _bc_invalid_detail()
    issues = detail.issues
    assert list(issues.columns) == [
        "date", "sec", "category", "factor", "instance_index", "params", "reason"
    ]
    b_rows = issues[(issues["sec"] == "B") & (issues["category"] == "momentum")]
    assert len(b_rows) == len(DATES)
    assert (b_rows["reason"] == "missing").all()
    c_rows = issues[(issues["sec"] == "C") & (issues["category"] == "volume")]
    assert len(c_rows) == len(DATES)
    assert (c_rows["reason"] == "non_finite").all()
    # 类别得分：B 在 momentum 为 NaN，C 在 volume 为 NaN，A 全有效
    assert detail.scores["momentum"]["B"].isna().all()
    assert detail.scores["volume"]["C"].isna().all()
    assert detail.scores["momentum"]["A"].notna().all()


def test_top_n_one_selects_a_and_records_exclusions() -> None:
    """top_n=1：每个决策日选 A，记录 B/C 的无效原因。"""
    detail = _bc_invalid_detail()
    composite = eaa_composite(detail.scores, {"momentum": 1.0, "volume": 1.0}, beta=1.0)
    plan = build_target_weights(
        composite,
        DATES,
        BacktestConfig(rebalance_freq="5d", top_n=1, max_weight=1.0, min_weight=0.0),
        decision_issues=detail.issues,
    )

    created = plan.target_weights
    # 6 天 5d 分块 → 决策日 d0/d5，合格数都只有 1 → 两天都生成目标
    assert list(created.index) == [DATES[0], DATES[5]]
    row = created.loc[DATES[0]]
    assert row["A"] == pytest.approx(1.0)
    assert row["B"] == 0.0
    assert row["C"] == 0.0

    entry = plan.decision_log[0]
    assert entry["status"] == "target_created"
    assert entry["eligible_count"] == 1
    assert entry["top_n"] == 1
    excl_secs = {e["sec"] for e in entry["exclusions"]}
    assert excl_secs == {"B", "C"}
    by_sec = {e["sec"]: e for e in entry["exclusions"]}
    assert by_sec["B"]["reason"] == "missing"
    assert by_sec["C"]["reason"] == "non_finite"


def test_top_n_two_skips_with_eligible_count_one() -> None:
    """top_n=2：合格数 1 不足 → 跳过，不生成目标行，不偷偷改 N。"""
    detail = _bc_invalid_detail()
    composite = eaa_composite(detail.scores, {"momentum": 1.0, "volume": 1.0}, beta=1.0)
    plan = build_target_weights(
        composite,
        DATES,
        BacktestConfig(rebalance_freq="5d", top_n=2, max_weight=1.0, min_weight=0.0),
        decision_issues=detail.issues,
    )
    assert plan.target_weights.empty
    for entry in plan.decision_log:
        assert entry["status"] == "skipped_insufficient"
        assert entry["eligible_count"] == 1
        assert entry["top_n"] == 2


def test_same_name_different_params_all_recorded() -> None:
    """同名不同参数两实例：issues 携带 instance_index/params，逐条保留。"""
    detail = _detail(
        _momentum(
            FactorInstance(name="stub", params={"nan_secs": ["B"]}),
            FactorInstance(name="stub", params={"nan_secs": ["B", "C"]}),
        ),
        _volume(),
    )
    issues = detail.issues
    mom = issues[(issues["category"] == "momentum") & (issues["sec"] == "B")]
    by_idx = {int(r.instance_index): r for r in mom.itertuples()}
    assert set(by_idx) == {0, 1}
    assert by_idx[0].params == {"nan_secs": ["B"]}
    assert by_idx[1].params == {"nan_secs": ["B", "C"]}
    # 类别得分：B 需两实例都有效 → NaN；C 仅第二实例无效 → NaN
    assert detail.scores["momentum"]["B"].isna().all()
    assert detail.scores["momentum"]["C"].isna().all()


def test_category_mean_requires_all_instances_finite() -> None:
    """类内均值要求所有实例有限：任一无效 → 类别资格 NaN；完整单元沿用等权均值。"""
    detail = _detail(
        _momentum(
            FactorInstance(name="stub", params={}),
            FactorInstance(name="stub", params={"nan_secs": ["B"]}),
        ),
        _volume(),
    )
    momentum = detail.scores["momentum"]
    # B 无效；A/C 完整 → min-max 归一化后等权均值（单值行 → 1.0）
    assert momentum["B"].isna().all()
    assert momentum["A"].notna().all()
    assert momentum["A"].iloc[0] == pytest.approx(1.0, abs=1e-9)
    assert momentum["C"].iloc[0] == pytest.approx(1.0, abs=1e-9)


def test_zero_weight_category_na_does_not_affect() -> None:
    """零权重类别：FAA 只遍历正权重类别，其 NaN 不影响资格。"""
    # C 在 volume 为 inf，但 volume 权重 0 → 不参与；momentum 全有效
    detail = _detail(
        _momentum(),
        _volume(FactorInstance(name="stub", params={"inf_secs": ["C"]})),
    )
    composite = faa_composite(detail.scores, {"momentum": 1.0, "volume": 0.0})
    assert composite.notna().all().all()


def test_enabled_missing_category_raises() -> None:
    """启用（正权重/正指数）但缺失的类别 → 配置错误。"""
    detail = _bc_invalid_detail()
    with pytest.raises(ValueError, match="空或缺失"):
        faa_composite(detail.scores, {"momentum": 0.5, "reversal": 0.5})
    with pytest.raises(ValueError, match="空或缺失"):
        eaa_composite(detail.scores, {"momentum": 1.0, "reversal": 1.0}, beta=1.0)


def test_cross_category_and_semantics() -> None:
    """跨启用类别取 AND：任一类别无效 → 合成 NaN（B 动量 NaN → 合成 NaN）。"""
    detail = _bc_invalid_detail()
    composite = eaa_composite(detail.scores, {"momentum": 1.0, "volume": 1.0}, beta=1.0)
    assert composite["B"].isna().all()
    assert composite["C"].isna().all()
    assert composite["A"].notna().all()


def test_normal_rebalance_still_records_exclusions() -> None:
    """正常生成组合的调仓日也保存被排除标的（合格数足够仍记录）。"""
    detail = _bc_invalid_detail()
    composite = eaa_composite(detail.scores, {"momentum": 1.0, "volume": 1.0}, beta=1.0)
    plan = build_target_weights(
        composite,
        DATES,
        BacktestConfig(rebalance_freq="5d", top_n=1, max_weight=1.0, min_weight=0.0),
        decision_issues=detail.issues,
    )
    entry = plan.decision_log[0]
    assert entry["status"] == "target_created"
    assert len(entry["exclusions"]) >= 2  # B 与 C 都被记录


def test_decision_log_entry_per_plan_date_even_without_exclusions() -> None:
    """每个计划决策日一条日志，即使没有任何排除。"""
    detail = _detail(_momentum(), _volume())
    composite = eaa_composite(detail.scores, {"momentum": 1.0, "volume": 1.0}, beta=1.0)
    plan = build_target_weights(
        composite,
        DATES,
        BacktestConfig(rebalance_freq="5d", top_n=2, max_weight=1.0, min_weight=0.0),
        decision_issues=detail.issues,
    )
    assert len(plan.decision_log) == 2  # 6 天 5d 分块 → d0/d5
    for entry in plan.decision_log:
        assert entry["status"] == "target_created"
        assert entry["eligible_count"] == 3
        assert entry["exclusions"] == []


def test_warmup_does_not_shift_5d_start() -> None:
    """预热范围不改变 5d 分块起点：决策日由 trading_dates 决定，不与因子日期交。"""
    trading_dates = pd.date_range("2026-01-05", periods=12, freq="D")
    scores_dates = trading_dates[6:]  # 因子只有后 6 天有效（预热 6 天）
    scores = pd.DataFrame(
        {"A": [1.0] * 6, "B": [0.8] * 6, "C": [0.5] * 6}, index=scores_dates
    )
    plan = build_target_weights(
        scores,
        trading_dates,
        BacktestConfig(rebalance_freq="5d", top_n=2, max_weight=1.0, min_weight=0.0),
    )
    # 决策日 = d0/d5/d10；d0、d5 在预热内 → 无分数跳过；d10 正常生成
    assert plan.rebalance_dates.tolist() == list(trading_dates[[0, 5, 10]])
    assert [e["status"] for e in plan.decision_log] == [
        "skipped_insufficient",
        "skipped_insufficient",
        "target_created",
    ]
    assert list(plan.target_weights.index) == [trading_dates[10]]


def test_full_data_formula_unchanged() -> None:
    """完整数据时：合成公式与既有 min-max + 等权/乘法公式一致。"""
    detail = _detail(_momentum(), _volume())
    scores = detail.scores

    composite = faa_composite(scores, {"momentum": 0.6, "volume": 0.4})
    # 手算：归一化单值行全为 1.0 → 合成 = 0.6*1 + 0.4*1 = 1.0
    assert composite.notna().all().all()
    assert np.allclose(composite.to_numpy(), 1.0)

    composite_eaa = eaa_composite(scores, {"momentum": 0.5, "volume": 1.25}, beta=0.5)
    # 手算：(1^0.5 * 1^1.25)^0.5 = 1.0
    assert np.allclose(composite_eaa.to_numpy(), 1.0)


# ── F19：禁用类别的资格缺失不进决策日志排除项 ─────────────────────────


def test_disabled_category_issues_not_in_decision_log() -> None:
    """F19 验收：选中证券不因禁用类别进入 exclusions。

    _bc_invalid_detail：B 动量 NaN（启用类别 missing）、C 量能 inf（禁用
    类别 non_finite）。volume 权重 0 → C 合成有效且被选中，其量能问题不得
    出现在排除项；B 的动量问题完整保留。旧实现会同时记录 C 的 volume
    non_finite，与"目标权重 > 0"自相矛盾。
    """
    detail = _bc_invalid_detail()
    weights = {"momentum": 1.0, "volume": 0.0}
    composite = faa_composite(detail.scores, weights)
    enabled = enabled_category_keys(detail.scores, weights)
    assert enabled == {"momentum"}
    issues = filter_issues_by_categories(detail.issues, enabled)

    plan = build_target_weights(
        composite,
        DATES,
        BacktestConfig(rebalance_freq="5d", top_n=2, max_weight=1.0, min_weight=0.0),
        decision_issues=issues,
    )
    entry = plan.decision_log[0]
    assert entry["status"] == "target_created"
    row = plan.target_weights.loc[entry["decision_date"]]
    assert row["A"] > 0 and row["C"] > 0  # C 被选中
    # 排除项只有 B 的动量 missing，无任何 volume 类别行
    assert {e["sec"] for e in entry["exclusions"]} == {"B"}
    assert all(e["category"] == "momentum" for e in entry["exclusions"])


def test_filter_issues_by_categories_preserves_enabled_rows() -> None:
    """启用类别内同名不同参数实例的问题逐条保留；EAA 口径共用同一 helper。"""
    detail = _detail(
        _momentum(
            FactorInstance(name="stub", params={"nan_secs": ["B"]}),
            FactorInstance(name="stub", params={"nan_secs": ["B", "C"]}),
        ),
        _volume(FactorInstance(name="stub", params={"inf_secs": ["C"]})),
    )
    issues = filter_issues_by_categories(detail.issues, {"momentum"})
    assert (issues["category"] == "momentum").all()
    # B 两实例逐条保留；C 仅第二实例无效
    b_mom = issues[(issues["category"] == "momentum") & (issues["sec"] == "B")]
    assert len(b_mom) == 2 * len(DATES)
    c_mom = issues[(issues["category"] == "momentum") & (issues["sec"] == "C")]
    assert len(c_mom) == len(DATES)

    # EAA 正指数口径：全部启用 → 与原始明细一致
    all_enabled = filter_issues_by_categories(
        detail.issues,
        enabled_category_keys(detail.scores, {"momentum": 1.0, "volume": 0.5}),
    )
    assert len(all_enabled) == len(detail.issues)

    # 空明细原样返回
    assert filter_issues_by_categories(detail.issues.iloc[:0], {"momentum"}).empty
