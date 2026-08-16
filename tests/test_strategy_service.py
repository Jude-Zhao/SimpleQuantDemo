"""Tests for the latest-holdings recommendation in the strategy service."""

from __future__ import annotations

import pandas as pd
import pytest

from core.optimization import EqualWeightOptimizer, ScoreWeightedOptimizer
from webapp.services.strategy_service import _latest_recommendation


def test_latest_recommendation_equal_weight() -> None:
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    composite = pd.DataFrame(
        {"A.SH": [3.0, 3.0, 3.0], "B.SH": [2.0, 2.0, 2.0], "C.SH": [1.0, 1.0, 1.0]},
        index=dates,
    )
    weights, date = _latest_recommendation(
        composite, EqualWeightOptimizer(top_n=2, max_weight=1.0)
    )
    assert date == "2026-01-03"
    assert set(weights) == {"A.SH", "B.SH"}
    assert weights["A.SH"] == pytest.approx(0.5)
    assert weights["B.SH"] == pytest.approx(0.5)


def test_latest_recommendation_score_weighted() -> None:
    dates = pd.date_range("2026-01-01", periods=2, freq="D")
    composite = pd.DataFrame(
        {"A.SH": [3.0, 3.0], "B.SH": [1.0, 1.0]},
        index=dates,
    )
    weights, date = _latest_recommendation(
        composite, ScoreWeightedOptimizer(top_n=2, max_weight=1.0)
    )
    assert date == "2026-01-02"
    # score-proportional: 3/4 and 1/4
    assert weights["A.SH"] == pytest.approx(0.75)
    assert weights["B.SH"] == pytest.approx(0.25)