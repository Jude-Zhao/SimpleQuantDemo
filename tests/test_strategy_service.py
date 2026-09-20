"""Tests for the latest-holdings recommendation in the strategy service."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.optimization import EqualWeightOptimizer, ScoreWeightedOptimizer
from core.synthesis import CategoryBuildResult
from webapp.services import strategy_service
from webapp.services.strategy_service import _latest_recommendation


def test_latest_recommendation_equal_weight() -> None:
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    composite = pd.DataFrame(
        {"A.SH": [3.0, 3.0, 3.0], "B.SH": [2.0, 2.0, 2.0], "C.SH": [1.0, 1.0, 1.0]},
        index=dates,
    )
    weights, date, reason = _latest_recommendation(
        composite, EqualWeightOptimizer(top_n=2, max_weight=1.0)
    )
    assert date == "2026-01-03"
    assert reason is None
    assert set(weights) == {"A.SH", "B.SH"}
    assert weights["A.SH"] == pytest.approx(0.5)
    assert weights["B.SH"] == pytest.approx(0.5)


def test_latest_recommendation_score_weighted() -> None:
    dates = pd.date_range("2026-01-01", periods=2, freq="D")
    composite = pd.DataFrame(
        {"A.SH": [3.0, 3.0], "B.SH": [1.0, 1.0]},
        index=dates,
    )
    weights, date, reason = _latest_recommendation(
        composite, ScoreWeightedOptimizer(top_n=2, max_weight=1.0)
    )
    assert date == "2026-01-02"
    assert reason is None
    # score-proportional: 3/4 and 1/4
    assert weights["A.SH"] == pytest.approx(0.75)
    assert weights["B.SH"] == pytest.approx(0.25)


def test_latest_recommendation_insufficient_returns_empty_with_reason() -> None:
    """最新因子日合格证券不足：返回空持仓 + 明确理由，不回填旧推荐。"""
    dates = pd.date_range("2026-01-01", periods=2, freq="D")
    # 前一日 3 只合格，最新日仅 1 只：不得回填前一日组合
    composite = pd.DataFrame(
        {"A.SH": [3.0, 3.0], "B.SH": [2.0, float("nan")], "C.SH": [1.0, float("nan")]},
        index=dates,
    )
    weights, date, reason = _latest_recommendation(
        composite, EqualWeightOptimizer(top_n=3, max_weight=1.0)
    )
    assert date == "2026-01-02"
    assert weights == {}
    assert reason is not None
    assert "合格证券不足" in reason


# ── F19: _run_faa/_run_eaa 只把启用类别的资格明细传入决策日志 ──────────

_ISSUE_COLUMNS = ["date", "sec", "category", "factor", "instance_index", "params", "reason"]


def _flat_price_frame(dates: pd.DatetimeIndex, secs: list[str]) -> pd.DataFrame:
    records = []
    for date in dates:
        for sec in secs:
            records.append(
                {"date": date, "sec": sec, "open": 100.0, "high": 100.0,
                 "low": 100.0, "close": 100.0, "volume": 1000.0, "amount": 1e6}
            )
    return pd.DataFrame(records)


def _stub_detail(dates: pd.DatetimeIndex, secs: list[str]) -> CategoryBuildResult:
    """momentum: S2 全 NaN（启用类别问题）；volume: S3 全 inf（将被禁用）。"""
    momentum = pd.DataFrame(1.0, index=dates, columns=secs)
    momentum["S2"] = np.nan
    volume = pd.DataFrame(1.0, index=dates, columns=secs)
    volume["S3"] = np.inf
    issues = pd.DataFrame(
        [
            {"date": d, "sec": "S2", "category": "momentum", "factor": "stub",
             "instance_index": 0, "params": {}, "reason": "missing"}
            for d in dates
        ]
        + [
            {"date": d, "sec": "S3", "category": "volume", "factor": "stub",
             "instance_index": 0, "params": {}, "reason": "non_finite"}
            for d in dates
        ],
        columns=_ISSUE_COLUMNS,
    )
    return CategoryBuildResult(scores={"momentum": momentum, "volume": volume}, issues=issues)


def _stub_categories():
    from core.factors.config import FactorCategory, FactorInstance

    return (
        FactorCategory(
            key="momentum", display_name="动量",
            factors=(FactorInstance(name="stub", params={}),),
        ),
        FactorCategory(
            key="volume", display_name="量能",
            factors=(FactorInstance(name="stub", params={}),),
        ),
    )


def _patch_detail(monkeypatch):
    dates = pd.bdate_range("2024-01-02", periods=6)
    secs = ["A1", "S2", "S3"]
    detail = _stub_detail(dates, secs)
    monkeypatch.setattr(strategy_service, "list_factor_categories", _stub_categories)
    monkeypatch.setattr(
        strategy_service,
        "build_category_scores_with_details",
        lambda price_data, universe, categories: detail,
    )
    return dates, secs


def _assert_only_enabled_category_exclusions(result) -> None:
    assert result.decision_log, "应生成决策日志"
    for entry in result.decision_log:
        excl_secs = {e["sec"] for e in entry["exclusions"]}
        # S2 的动量缺失（启用类别）保留；S3 的量能缺失（禁用类别）不出现
        assert excl_secs == {"S2"}
        assert all(e["category"] == "momentum" for e in entry["exclusions"])


def test_run_faa_filters_disabled_category_issues(monkeypatch) -> None:
    dates, secs = _patch_detail(monkeypatch)
    price_data = _flat_price_frame(dates, secs)

    result, *_ = strategy_service._run_faa(
        {"top_n": 2, "class_weights": {"momentum": 1.0, "volume": 0.0}},
        price_data, price_data, secs, {}, None,
    )
    _assert_only_enabled_category_exclusions(result)


def test_run_eaa_filters_disabled_category_issues(monkeypatch) -> None:
    dates, secs = _patch_detail(monkeypatch)
    price_data = _flat_price_frame(dates, secs)

    result, *_ = strategy_service._run_eaa(
        {"top_n": 2, "exponents": {"momentum": 1.0, "volume": 0.0}, "beta": 0.5},
        price_data, price_data, secs, {}, None,
    )
    _assert_only_enabled_category_exclusions(result)
