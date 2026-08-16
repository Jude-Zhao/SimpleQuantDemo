"""Grid-search EAA/FAA strategy parameters and pick robust defaults.

Design doc: docs/plans/2026-08-16-strategy-param-tuning.md

Run:
    python research/tune_strategy_params.py [--faa] [--eaa]

Key optimisation: factor category scores are built ONCE from price data and
reused by every candidate combination (weights only affect the composite
combination, not the factor matrices).
"""
from __future__ import annotations

import argparse
import itertools
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.calendar import generate_rebalance_dates
from core.data import SqliteDataSource
from core.factors.config import list_factor_categories
from core.factors.utils import pivot_price_field
from core.optimization import EqualWeightOptimizer, ScoreWeightedOptimizer
from core.synthesis import build_category_scores, eaa_composite, faa_composite

DB_PATH = PROJECT_ROOT / "data" / "simple_quant.db"
START = "2021-01-04"
END = "2026-08-14"
DATA_START = "2019-11-01"
TOP_N = 5
COST_BPS = 1.0
REBALANCE_FREQ = "5d"

# Search spaces
FAA_W_GRID = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
EAA_ALPHA_GRID = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]
EAA_BETA_GRID = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]

# Scoring weights
SCORE_W_RETURN = 0.5   # risk-adjusted return (annual/vol)
SCORE_W_STABILITY = 0.3  # year-stability
SCORE_W_DRAWDOWN = 0.2   # (1 - drawdown penalty)
MDD_REF = -0.25  # reference for drawdown penalty


@dataclass
class ScenarioResult:
    label: str
    params: dict
    total: float
    annual: float
    vol: float
    sharpe: float
    mdd: float
    yearly: dict
    score: float
    n_reb: int


def _load_data() -> tuple[pd.DataFrame, pd.DataFrame, tuple, list]:
    source = SqliteDataSource(db_path=str(DB_PATH))
    price_data, _macro, universe = source.load_all(
        start_date=DATA_START, end_date=END
    )
    categories = list_factor_categories()
    close = pivot_price_field(price_data, field="close", universe=universe)
    start = pd.Timestamp(START)
    close = close.loc[close.index >= start].sort_index()
    return price_data, close, categories, universe


def _build_category_scores(price_data, categories, universe) -> dict:
    return build_category_scores(price_data, universe, categories)


def _target_weights(close, scores, weight_mode) -> pd.DataFrame:
    reb = generate_rebalance_dates(
        trading_dates=close.index, rebalance_freq=REBALANCE_FREQ, rebalance_day=0
    )
    optimizer = (
        ScoreWeightedOptimizer(top_n=TOP_N, max_weight=1.0, min_weight=0.0)
        if weight_mode == "score"
        else EqualWeightOptimizer(top_n=TOP_N, max_weight=1.0, min_weight=0.0)
    )
    target = pd.DataFrame(pd.NA, index=close.index, columns=close.columns, dtype="Float64")
    for date in reb:
        if date not in scores.index:
            continue
        row = scores.loc[date].dropna()
        if len(row) < TOP_N:
            continue
        target.loc[date] = optimizer.optimize(row)
    return target.ffill().fillna(0.0).astype(float)


def _backtest(close, composite, weight_mode) -> tuple[pd.Series, pd.Series, int]:
    scores = composite.loc[close.index, close.columns].sort_index()
    weights = _target_weights(close, scores, weight_mode)
    reb = generate_rebalance_dates(
        trading_dates=close.index, rebalance_freq=REBALANCE_FREQ, rebalance_day=0
    )
    reb = reb[reb.isin(close.index)]
    turnover = weights.diff().abs().sum(axis=1)
    costs = turnover * COST_BPS / 10000
    close_ret = close.pct_change(fill_method=None).fillna(0.0)
    rets = (weights.shift(2).fillna(0.0) * close_ret).sum(axis=1) - costs
    equity = (1.0 + rets).cumprod()
    return equity, rets, len(reb)


def _metrics(equity: pd.Series, rets: pd.Series, n_reb: int) -> dict:
    total = float(equity.iloc[-1] / equity.iloc[0] - 1)
    n = len(equity)
    annual = float((1 + total) ** (252 / n) - 1) if total > -1 else -1.0
    vol = float(rets.std() * np.sqrt(252))
    sharpe = float((annual - 0.02) / vol) if vol > 0 else 0.0
    mdd = float((equity / equity.cummax() - 1).min())
    yearly = {year: float((1 + grp).prod() - 1.0)
              for year, grp in rets.groupby(rets.index.year)}
    return {"total": total, "annual": annual, "vol": vol, "sharpe": sharpe,
            "mdd": mdd, "yearly": yearly, "n_reb": n_reb}


def _year_stability(yearly: dict) -> float:
    if not yearly:
        return 0.0
    vals = list(yearly.values())
    win_rate = sum(1 for v in vals if v > 0) / len(vals)
    worst = min(vals)
    best = max(vals)
    if best == worst:
        worst_norm = 0.0
    else:
        worst_norm = worst / (worst - best)  # in (-inf, 0], closer to 0 = more robust
    return 0.5 * win_rate + 0.5 * worst_norm


def _score(m: dict) -> float:
    ra = m["annual"] / m["vol"] if m["vol"] > 0 else 0.0
    stab = _year_stability(m["yearly"])
    mdd_pen = m["mdd"] / MDD_REF  # mdd negative -> positive penalty
    return SCORE_W_RETURN * ra + SCORE_W_STABILITY * stab + SCORE_W_DRAWDOWN * (1.0 - mdd_pen)


def _faa_combos() -> list[dict]:
    combos = []
    for w in itertools.product(FAA_W_GRID, repeat=4):
        if abs(sum(w) - 1.0) > 1e-9:
            continue
        if w[0] + w[1] < 0.5:  # momentum + reversal >= 0.5
            continue
        combos.append({"class_weights": {
            "momentum": w[0], "reversal": w[1],
            "volatility": w[2], "volume": w[3],
        }})
    return combos


def _eaa_combos(include_beta: bool = True) -> list[dict]:
    alpha_non1 = [a for a in EAA_ALPHA_GRID if abs(a - 1.0) > 1e-9]
    cats = ["momentum", "reversal", "volatility", "volume"]
    betas = EAA_BETA_GRID if include_beta else [1.0]
    combos = []
    # 1-category perturbation
    for cat in cats:
        for a in alpha_non1:
            exp = {c: 1.0 for c in cats}
            exp[cat] = a
            for b in betas:
                combos.append({"exponents": dict(exp), "beta": b})
    # 2-category perturbation
    for c1, c2 in itertools.combinations(cats, 2):
        for a1 in alpha_non1:
            for a2 in alpha_non1:
                exp = {c: 1.0 for c in cats}
                exp[c1] = a1
                exp[c2] = a2
                for b in betas:
                    combos.append({"exponents": dict(exp), "beta": b})
    # baseline (all 1) with beta sweep
    for b in betas:
        combos.append({"exponents": {c: 1.0 for c in cats}, "beta": b})
    return combos


def _run_faa(close, category_scores) -> list[ScenarioResult]:
    results = []
    for params in _faa_combos():
        cw = params["class_weights"]
        composite = faa_composite(category_scores, cw)
        equity, rets, n_reb = _backtest(close, composite, "equal")
        m = _metrics(equity, rets, n_reb)
        results.append(ScenarioResult(
            label="FAA " + ",".join(f"{k}={v:g}" for k, v in cw.items()),
            params=params, score=_score(m), **m,
        ))
    return results


def _run_eaa(close, category_scores) -> list[ScenarioResult]:
    results = []
    # Stage 1: alpha search with beta=1 (beta does not affect Top-N selection).
    for params in _eaa_combos(include_beta=False):
        exp = params["exponents"]
        composite = eaa_composite(category_scores, exp, 1.0)
        equity, rets, n_reb = _backtest(close, composite, "score")
        m = _metrics(equity, rets, n_reb)
        results.append(ScenarioResult(
            label="EAA " + ",".join(f"{k}={v:g}" for k, v in exp.items()) + ",beta=1",
            params=params, score=_score(m), **m,
        ))
    # Stage 2: beta sweep on the alpha Top candidates (beta only rescales the
    # score-proportional weights, not the Top-N ranking).
    top_alpha = sorted(results, key=lambda r: r.score, reverse=True)[:15]
    for r in top_alpha:
        exp = r.params["exponents"]
        for b in EAA_BETA_GRID:
            if abs(b - 1.0) < 1e-9:
                continue
            composite = eaa_composite(category_scores, exp, b)
            equity, rets, n_reb = _backtest(close, composite, "score")
            m = _metrics(equity, rets, n_reb)
            results.append(ScenarioResult(
                label="EAA " + ",".join(f"{k}={v:g}" for k, v in exp.items()) +
                      f",beta={b:g}",
                params={"exponents": dict(exp), "beta": b},
                score=_score(m), **m,
            ))
    return results


def _print_table(results: list[ScenarioResult], top_k: int = 15) -> None:
    results = sorted(results, key=lambda r: r.score, reverse=True)[:top_k]
    years = sorted({y for r in results for y in r.yearly})
    header = ["参数", "Score", "总收益", "年化", "Sharpe", "最大回撤", "年胜率"]
    print(f"{header[0]:<52}{header[1]:>7}{header[2]:>9}{header[3]:>8}"
          f"{header[4]:>8}{header[5]:>10}{header[6]:>8}")
    for r in results:
        win_rate = sum(1 for v in r.yearly.values() if v > 0) / len(r.yearly)
        print(f"{r.label:<52}{r.score:>7.3f}{r.total:>9.2%}{r.annual:>8.2%}"
              f"{r.sharpe:>8.2f}{r.mdd:>10.2%}{win_rate:>8.2f}")
    print("-" * 100)
    print("每年收益（Top 候选）：")
    print(f"{'参数':<52}" + "".join(f"{y:>9}" for y in years))
    for r in results:
        cells = "".join(f"{r.yearly.get(y, float('nan')):>8.2%}" for y in years)
        print(f"{r.label:<52}{cells}")


def _run_one(strategy: str, close, category_scores) -> list[ScenarioResult]:
    if strategy == "faa":
        return _run_faa(close, category_scores)
    return _run_eaa(close, category_scores)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--faa", action="store_true")
    parser.add_argument("--eaa", action="store_true")
    args = parser.parse_args()
    do_faa = args.faa or not (args.eaa or args.faa)
    do_eaa = args.eaa or not (args.eaa or args.faa)

    # Build factor category scores once.
    price_data, close, categories, universe = _load_data()
    print(f"标的数={len(universe)} 区间={close.index[0].date()}~{close.index[-1].date()}")
    category_scores = _build_category_scores(price_data, categories, universe)

    if do_faa:
        print("=" * 100)
        print("FAA 搜索")
        print("=" * 100)
        res = _run_one("faa", close, category_scores)
        print(f"候选组合数: {len(res)}")
        _print_table(res)

    if do_eaa:
        print("=" * 100)
        print("EAA 搜索")
        print("=" * 100)
        res = _run_one("eaa", close, category_scores)
        print(f"候选组合数: {len(res)}")
        _print_table(res)


if __name__ == "__main__":
    main()