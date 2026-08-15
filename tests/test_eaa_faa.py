"""Unit tests for the EAA / FAA strategy scoring pipeline.

Covers min-max normalization, category score synthesis, the FAA weighted-sum
and EAA power-product composites, and the ScoreWeightedOptimizer used for
EAA's score-proportional weights.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.factors.config import list_factor_categories
from core.optimization import ScoreWeightedOptimizer
from webapp.services.eaa_faa import (
    build_category_scores,
    eaa_composite,
    faa_composite,
    normalize_cross_section,
)

UNIVERSE = ["510300.SH", "510500.SH", "159915.SZ", "511010.SH", "513770.SH"]


def _price_data(n_days: int = 100, seed: int = 1) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
    rng = np.random.default_rng(seed)
    wide = pd.DataFrame(
        {s: 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, n_days)) for s in UNIVERSE},
        index=dates,
    )
    df = (
        wide.reset_index()
        .melt(id_vars="index", var_name="sec", value_name="close")
        .rename(columns={"index": "date"})
    )
    # Migrated factors need OHLCV (aroon_diff uses high/low, money_flow_20 volume).
    df["high"] = df["close"] * 1.01
    df["low"] = df["close"] * 0.99
    df["volume"] = 1000
    return df


def _non_empty_weight(frac: dict[str, float] | None = None) -> dict[str, float]:
    cats = [c for c in list_factor_categories() if not c.is_empty]
    if frac is None:
        v = 1.0 / len(cats)
        return {c.key: v for c in cats}
    out = {}
    for c in cats:
        if c.key in frac:
            out[c.key] = frac[c.key]
    return out


# ── normalize_cross_section ────────────────────────────────────────────

def test_normalize_basic_bounds():
    df = pd.DataFrame(
        {"A": [1.0, 2.0], "B": [3.0, 4.0], "C": [5.0, 6.0]},
        index=pd.date_range("2024-01-01", periods=2),
    )
    out = normalize_cross_section(df)
    assert out.min().min() >= 0.01
    assert out.max().max() <= 1.0
    # Original ordering preserved: C > B > A within each row.
    assert (out["C"] > out["B"]).all()
    assert (out["B"] > out["A"]).all()


def test_normalize_no_discrimination_row():
    df = pd.DataFrame(
        {"A": [1.0, 2.0], "B": [1.0, 4.0], "C": [1.0, 6.0]},
        index=pd.date_range("2024-01-01", periods=2),
    )
    out = normalize_cross_section(df)
    assert (out.iloc[0] == 1.0).all(), "identical row maps to 1.0"


def test_normalize_keeps_nan():
    df = pd.DataFrame(
        {"A": [1.0, 2.0, np.nan], "B": [3.0, 4.0, 5.0], "C": [5.0, 6.0, 5.0]},
        index=pd.date_range("2024-01-01", periods=3),
    )
    out = normalize_cross_section(df)
    assert pd.isna(out.iloc[2, 0]), "NaN cell stays NaN on no-discrimination row"
    assert out.iloc[2, 1] == 1.0 and out.iloc[2, 2] == 1.0


def test_normalize_all_nan_row():
    df = pd.DataFrame(
        {"A": [1.0, np.nan], "B": [2.0, np.nan], "C": [3.0, np.nan]},
        index=pd.date_range("2024-01-01", periods=2),
    )
    out = normalize_cross_section(df)
    assert out.iloc[1].isna().all()


# ── category scores ────────────────────────────────────────────────────

def test_build_category_scores_non_empty_only():
    cats = list_factor_categories()
    scores = build_category_scores(_price_data(), UNIVERSE, cats)
    assert set(scores.keys()) == {"momentum", "volatility", "reversal", "volume"}
    for key, mat in scores.items():
        assert list(mat.columns) == UNIVERSE
        assert mat.index.is_monotonic_increasing


# ── FAA composite ──────────────────────────────────────────────────────

def test_faa_composite_linear():
    cats = list_factor_categories()
    scores = build_category_scores(_price_data(), UNIVERSE, cats)
    w = _non_empty_weight({"momentum": 0.5, "volatility": 0.3, "reversal": 0.2})
    composite = faa_composite(scores, w)
    assert not composite.isna().all().all()
    last = composite.iloc[-1]
    assert last.between(0.01, 1.0).all()


def test_faa_composite_normalizes_weights():
    cats = list_factor_categories()
    scores = build_category_scores(_price_data(), UNIVERSE, cats)
    # weights not summing to 1 should still work (normalized internally)
    w = _non_empty_weight({"momentum": 0.5, "volatility": 0.5, "reversal": 0.5})
    composite = faa_composite(scores, w)
    assert not composite.isna().all().all()


def test_faa_composite_requires_positive_weight():
    cats = list_factor_categories()
    scores = build_category_scores(_price_data(), UNIVERSE, cats)
    with pytest.raises(ValueError):
        faa_composite(scores, {"momentum": 0.0, "volatility": 0.0, "reversal": 0.0})


# ── EAA composite ──────────────────────────────────────────────────────

def test_eaa_composite_positive():
    cats = list_factor_categories()
    scores = build_category_scores(_price_data(), UNIVERSE, cats)
    exponents = {c.key: 1.0 for c in cats if not c.is_empty}
    composite = eaa_composite(scores, exponents, beta=1.0)
    assert not composite.isna().all().all()
    assert (composite.iloc[-1] > 0).all(), "EAA scores must be positive"


def test_eaa_composite_raises_without_positive_exponent():
    cats = list_factor_categories()
    scores = build_category_scores(_price_data(), UNIVERSE, cats)
    with pytest.raises(ValueError):
        eaa_composite(scores, {}, beta=1.0)


# ── ScoreWeightedOptimizer ─────────────────────────────────────────────

def test_score_weighted_optimizer():
    opt = ScoreWeightedOptimizer(top_n=3, max_weight=1.0, min_weight=0.0)
    s = pd.Series({"A": 3.0, "B": 1.0, "C": 2.0, "D": 0.5})
    w = opt.optimize(s)
    # Top 3 = A, C, B; weights proportional to scores.
    assert w["A"] > w["C"] > w["B"]
    assert w["D"] == 0.0
    assert abs(w.sum() - 1.0) < 1e-9


def test_score_weighted_optimizer_insufficient():
    opt = ScoreWeightedOptimizer(top_n=5, max_weight=1.0, min_weight=0.0)
    s = pd.Series({"A": 1.0, "B": 2.0})
    with pytest.raises(Exception):
        opt.optimize(s)


def test_score_weighted_optimizer_ignores_nonnpositive():
    opt = ScoreWeightedOptimizer(top_n=2, max_weight=1.0, min_weight=0.0)
    s = pd.Series({"A": 1.0, "B": 2.0, "C": -1.0, "D": 0.0})
    w = opt.optimize(s)
    assert set(w[w > 0].index) == {"A", "B"}