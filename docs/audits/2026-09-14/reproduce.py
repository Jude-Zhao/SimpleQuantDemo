"""Offline evidence for the 2026-09-14 audit, NOT regression acceptance tests.

Run from the repo: .venv/Scripts/python.exe docs/audits/2026-09-14/reproduce.py
Assertions describe the buggy current behavior. Uses only a temporary database.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import runpy
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory(prefix="sq_audit_")
os.environ["DATABASE_URL"] = "sqlite:///" + (Path(TEMP.name) / "audit.db").as_posix()
os.environ["MPLBACKEND"] = "Agg"

import numpy as np
import pandas as pd
from core.backtest import BacktestConfig, ExecutionConfig, execute_backtest
from core.backtest.targets import build_target_weights
from core.data.cached_source import CachedDataSource
from core.factors.config import FactorCategory, FactorInstance
from webapp.models.database import Base, SessionLocal, engine, init_db
from webapp.models.market_data import EtfDailyBar
from webapp.services import data_service, factor_service, strategy_service, sync_service
from webapp.services import classification_service, macro_service


def prices(values, codes=None):
    values = np.asarray(values, dtype=float)
    if values.ndim == 1:
        values = values[:, None]
    codes = codes or [f"{i:06d}.SH" for i in range(values.shape[1])]
    dates = pd.bdate_range("2024-01-02", periods=len(values))
    return pd.DataFrame([
        {"date": date, "sec": code, "open": values[i, j], "high": values[i, j],
         "low": values[i, j], "close": values[i, j], "volume": 100.0, "amount": 1000.0}
        for i, date in enumerate(dates) for j, code in enumerate(codes)
    ])


def error_of(call):
    try:
        call()
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def cache_start():
    frame = prices([10, 11])
    frame["date"] = pd.to_datetime(["2024-06-03", "2024-06-04"])
    calls = []
    class Source:
        def get_etf_price_by_codes(self, **kwargs):
            calls.append(kwargs)
            return frame
    src = CachedDataSource(Source(), cache_reader=lambda *args: frame)
    result = src.get_etf_price_by_codes(["000000.SH"], "2021-01-04", "2024-06-04")
    assert not calls and len(result) == 2
    return {"requested_start": "2021-01-04", "actual_start": str(result.date.min().date()), "source_calls": len(calls)}


def partial_replace():
    frame = prices([10, 11, 12])
    with SessionLocal() as db:
        sync_service._write_etf_data(db, frame)
        sync_service._replace_etf_range(db, frame.iloc[[1]], "000000.SH", "2024-01-02", "2024-01-04")
        remaining = db.query(EtfDailyBar).all()
        assert len(remaining) == 1
        db.query(EtfDailyBar).delete()
        db.commit()
        return {"rows_before": 3, "source_rows": 1, "rows_after": len(remaining)}


def cache_revision():
    old = prices([10, 11])
    fresh = prices([20, 21, 22])
    with SessionLocal() as db:
        data_service._write_daily_cache(db, old)
        data_service._write_daily_cache(db, fresh)
        read = data_service._read_daily_cache(db, ["000000.SH"], None, None)
        actual = read.sort_values("date").close.tolist()
        assert actual == [10.0, 11.0, 22.0]
        db.query(EtfDailyBar).delete()
        db.commit()
    return {"fresh": fresh.close.tolist(), "persisted": actual}


def sync_boundaries():
    sync_service._active_requests.clear()
    with patch("threading.Thread.start", return_value=None):
        with SessionLocal() as db:
            a = sync_service.start_etf_sync(db, ["000000.SH"], "2024-01-02", "2024-01-03")
            b = sync_service.start_etf_sync(db, ["000000.SH"], "2024-01-03", "2024-01-04")
    assert len(sync_service._active_requests) == 2
    sync_service._active_requests.clear()
    with patch("threading.Thread.start", return_value=None):
        with SessionLocal() as db:
            macro_service.start_macro_sync(db, "monthly", "2024-01-02", "2024-01-03")
            macro_service.start_macro_sync(db, "monthly", "2024-01-20", "2024-01-21")
    assert len(sync_service._active_requests) == 2
    sync_service._active_requests.clear()
    return {"etf_shared_end_day": "both accepted", "macro_same_month": "both accepted"}


def hfq_fallback():
    from core.data.akshare_source import AkShareDataSource
    class Response:
        status_code = 200
        text = "{}"
        def json(self):
            return {"data": {"sh000000": {"day": [["2024-01-02", "10", "10", "10", "10", "1"]]}}}
    with patch("core.data.akshare_source.requests.get", return_value=Response()), patch("core.data.akshare_source._throttle"):
        rows = AkShareDataSource._tencent_get("sh000000", "2024-01-02", "2024-01-02", "hfq")
    assert len(rows) == 1
    return {"requested": "hfq", "only_response_key": "day", "accepted_rows": len(rows)}


def missing_factor_observations():
    from core.factors.builtin.mfi.mfi import MFIFactor
    from core.factors.builtin.drawdown_120.drawdown_120 import Drawdown120Factor
    frame = prices(np.arange(100, 125))
    frame.loc[frame.index[-1], ["open", "high", "low", "close", "volume"]] = np.nan
    mfi = MFIFactor().build(frame, pd.DataFrame(), ["000000.SH"]).iloc[-1, 0]
    dd = Drawdown120Factor().build(frame, pd.DataFrame(), ["000000.SH"]).iloc[-1, 0]
    assert np.isfinite(mfi) and np.isfinite(dd)
    return {"latest_raw_price": None, "mfi": float(mfi), "drawdown": float(dd)}


def drawdown_window():
    from core.factors.builtin.drawdown_120.drawdown_120 import Drawdown120Factor
    frame = prices([200] + [100] * 130)
    got = Drawdown120Factor().build(frame, pd.DataFrame(), ["000000.SH"]).iloc[-1, 0]
    expected = 0.0  # all last 120 prices equal 100
    assert got == -0.5
    return {"last_120_prices": "all 100", "older_peak": 200, "reported": float(got), "rolling_window_drawdown": expected}


def overlapping_returns():
    idx = pd.bdate_range("2024-01-02", periods=10)
    factors = pd.DataFrame(1.0, index=idx, columns=["A"])
    forward = pd.DataFrame(1.01**5 - 1, index=idx, columns=["A"])
    got = factor_service._calculate_group_returns(factors, forward, n_groups=1, horizon=5)[0]
    assert abs(got.cumulative_return - (1.01**50 - 1)) < 1e-12
    return {"ten_overlapping_5d_windows": got.cumulative_return, "underlying_14_daily_intervals": 1.01**14 - 1,
            "two_nonoverlapping_5d_windows": 1.01**10 - 1}


def nonfinite_api_stats():
    from starlette.responses import JSONResponse
    idx = pd.bdate_range("2024-01-02", periods=2)
    constant = pd.DataFrame(1.0, index=idx, columns=["A", "B", "C"])
    _, corr = factor_service._correlation_of_panel({"constant": constant})
    err = error_of(lambda: JSONResponse({"matrix": corr}))
    assert err and "JSON" in err
    # First MACD row is constant; 8 prices with horizon=5 leave one usable IC.
    frame = prices([[10+i, 12+i*i, 15+2*i+i*i/2] for i in range(8)])
    response = factor_service.compute_factor("macd_hist", {}, frame, pd.DataFrame(), sorted(frame.sec.unique()), horizon=5)
    assert np.isnan(response.ic_result.ic_std)
    return {"constant_correlation_json_error": err, "one_ic_observation_std": "NaN",
            "one_ic_observation_icir": str(response.ic_result.icir)}


def default_strategy_params():
    frame = prices(np.arange(1, 151)[:, None] * np.array([[1, 2, 3, 4, 5]]) + 100)
    codes = sorted(frame.sec.unique())
    err = error_of(lambda: strategy_service._run_faa({}, frame, frame, codes, {}, None))
    assert err and "positive weight" in err
    return {"omitted_params": {}, "error": err, "metadata_has_defaults": True}


def disabled_category_issues():
    idx = pd.bdate_range("2024-01-02", periods=2)
    scores = pd.DataFrame({"A": [1.0, 1.0]}, index=idx)
    issues = pd.DataFrame([{"date": idx[0], "sec": "A", "category": "disabled", "factor": "unused",
                            "instance_index": 0, "params": {}, "reason": "missing"}])
    plan = build_target_weights(scores, idx, BacktestConfig(top_n=1, max_weight=1), issues)
    entry = plan.decision_log[0]
    assert entry["eligible_count"] == 1 and entry["exclusions"][0]["sec"] == "A"
    return {"selected_weight": float(plan.target_weights.iloc[0, 0]), "eligible_count": 1,
            "same_security_exclusion": entry["exclusions"][0]}


def beta_zero():
    from core.synthesis import eaa_composite
    from webapp.schemas.strategy import StrategyRunRequest
    req = StrategyRunRequest(strategy_type="eaa", params={"exponents": {"x": 1}, "beta": 0})
    mat = pd.DataFrame([[np.nan, 1.0]], index=pd.to_datetime(["2024-01-02"]), columns=["A", "B"])
    got = eaa_composite({"x": mat}, req.params["exponents"], req.params["beta"])
    assert got.iloc[0, 0] == 1
    return {"beta_accepted": 0, "input_missing_score": None, "output_score": float(got.iloc[0, 0])}


def thread_start_failure():
    from webapp.schemas.strategy import StrategyRunRequest
    from webapp.models.strategy_run import StrategyRun
    with SessionLocal() as db:
        with patch("threading.Thread.start", side_effect=RuntimeError("simulated cannot start thread")):
            err = error_of(lambda: strategy_service.submit_strategy(db, StrategyRunRequest(strategy_type="faa")))
        stranded = db.query(StrategyRun).filter_by(status="pending").count()
        again = strategy_service.submit_strategy(db, StrategyRunRequest(strategy_type="faa"))
        assert stranded == 1 and again.status == "failed"
        db.query(StrategyRun).delete()
        db.commit()
    return {"launch_error": err, "stranded_pending": stranded, "next_request": again.error_msg}


def invalid_rule_persists():
    from webapp.schemas.classification import ClassificationRuleCreate, ClassificationRuleUpdate
    from webapp.models.classification import ClassificationRule
    with SessionLocal() as db:
        rule = classification_service.create_rule(db, ClassificationRuleCreate(rule_name="bad", category_key="asset_type", rule_type="manual", config={"sec_codes": []}))
        classification_service.update_rule(db, rule.id, ClassificationRuleUpdate(config=None))
        err = error_of(lambda: classification_service.classify_universe(db))
        assert err and "get" in err
        db.query(ClassificationRule).delete()
        db.commit()
    return {"accepted_update": {"config": None}, "later_classification_error": err}


def macro_alignment():
    from core.data.utils import align_macro_to_trading_dates
    raw = pd.DataFrame({"value": [1.0, 2.0]}, index=pd.to_datetime(["2024-01-05", "2024-01-07"]))
    aligned = align_macro_to_trading_dates(raw, pd.to_datetime(["2024-01-05", "2024-01-08"]))
    assert aligned.iloc[-1, 0] == 1
    return {"friday": 1, "sunday_published": 2, "monday_aligned": float(aligned.iloc[-1, 0])}


def icir_lookahead():
    from core.analysis.ic import calculate_forward_returns, calculate_factor_ic, calculate_icir
    from core.synthesis.icir_weight import ICIRWeightedSynthesizer
    rng = np.random.default_rng(123)
    values = 100 * np.cumprod(1 + rng.normal(0, .03, (20, 5)), axis=0)
    dates = pd.bdate_range("2024-01-02", periods=20)
    codes = [f"{i:06d}.SH" for i in range(5)]
    panel = {f"F{i}": pd.DataFrame(rng.normal(size=(20, 5)), index=dates, columns=codes) for i in range(2)}
    def synth(vals):
        forward = calculate_forward_returns(prices(vals), horizon=2, universe=codes)
        icir = {k: calculate_icir(calculate_factor_ic(m, forward), window=3) for k, m in panel.items()}
        return ICIRWeightedSynthesizer().synthesize(panel, icir).loc[dates[6]]
    original = synth(values)
    changed_values = values.copy()
    changed_values[9] *= [2, 1, .7, 1.1, 1.3]  # ONLY change a future close after signal T=6
    changed = synth(changed_values)
    delta = float((original-changed).abs().max())
    assert delta > 1e-8
    return {"signal_date": str(dates[6].date()), "changed_future_date": str(dates[9].date()), "max_signal_change": delta}


def research_missing_dates():
    import research.main as research_main
    from research.config import ResearchConfig
    frame = prices([10, 10, 11, 12, 13, 14, 15])
    dates = pd.DatetimeIndex(frame.date)
    composite = pd.DataFrame(1.0, index=dates.delete(1), columns=["000000.SH"])
    class Source:
        def __init__(self, **kwargs): pass
        def load_all(self, **kwargs): return frame, pd.DataFrame(), ["000000.SH"]
    with patch.object(research_main, "SqliteDataSource", Source), patch.object(research_main, "load_research_categories", return_value=()), \
         patch.object(research_main, "_build_factor_panel", return_value={"F": composite}), \
         patch.object(research_main, "_evaluate_factors", return_value=({}, {}, {}, [])), \
         patch.object(research_main, "_build_composite", return_value=(composite, pd.DataFrame())), \
         patch.object(research_main, "write_research_outputs", return_value={}):
        got = research_main.run_research(ResearchConfig(start_date="2024-01-02", top_n=1))
    executed = got.backtest.execution_log[0]["execution_date"]
    assert executed == dates[2] and dates[1] not in got.backtest.equity_curve.index
    return {"expected_execution": str(dates[1].date()), "actual_execution": str(executed.date()), "missing_close_rows": 1}


def infinite_initial_cash():
    close = pd.DataFrame({"A": [10, 10]}, index=pd.bdate_range("2024-01-02", periods=2))
    target = pd.DataFrame(columns=["A"], index=pd.DatetimeIndex([]), dtype=float)
    got = execute_backtest(close, target, ExecutionConfig(initial_cash=float("inf")))
    assert got.equity_curve.isna().all() and (got.daily_returns == 0).all()
    return {"accepted_initial_cash": "Infinity", "equity": ["NaN", "NaN"], "daily_returns": got.daily_returns.tolist()}


def dashboard_return_window():
    from webapp.api import dashboard
    frame = prices([100, 110, 121])
    with patch.object(dashboard, "get_etf_list", return_value=[{"sec_code": "000000.SH", "sec_name": "sample"}]), \
         patch.object(dashboard, "get_etf_price", return_value=frame):
        one = dashboard.get_returns_ranking(days=1, db=None)
        two = dashboard.get_returns_ranking(days=2, db=None)
    assert one.momentum == [] and abs(two.momentum[0].return_pct - .1) < 1e-12
    return {"days_1_result": [], "days_2_reported": two.momentum[0].return_pct, "days_2_expected": .21}


def dashboard_stale_cache():
    from webapp.api import dashboard
    frame = prices([10, 11, 12])
    key = (("000000.SH",), str(frame.date.max()))
    sentinel = [dashboard.FactorRankingItem(key="old", display_name="old", is_empty=True)]
    dashboard._ranking_cache[key] = sentinel
    changed = frame.copy()
    changed.loc[0, "close"] = 100
    with patch.object(dashboard, "get_etf_list", return_value=[{"sec_code": "000000.SH", "sec_name": "sample"}]), \
         patch.object(dashboard, "get_etf_price", return_value=changed), \
         patch.object(dashboard, "calculate_forward_returns") as compute:
        got = dashboard.get_factor_ranking(None)
    assert got is sentinel and compute.call_count == 0
    dashboard._ranking_cache.clear()
    return {"historical_price_changed": True, "date_unchanged": True, "recomputed": False}


def research_ic_schedule():
    from core.calendar import generate_rebalance_dates
    from research.main import _evaluate_factors
    from research.config import ResearchConfig
    frame = prices(np.arange(1, 16)[:, None] * np.array([[1, 2, 3]]) + 100)
    dates = pd.DatetimeIndex(frame.date.unique())
    eval_start = dates[2]
    factor = pd.DataFrame([[1, 2, 3]] * len(dates), index=dates, columns=sorted(frame.sec.unique()))
    cfg = ResearchConfig(start_date=str(eval_start.date()), ic_min_observations=3, icir_window=3, icir_min_periods=2)
    ic, _, _, _ = _evaluate_factors(frame, {"sample": factor}, cfg, eval_start)
    backtest_dates = generate_rebalance_dates(dates[dates >= eval_start], "5d")
    assert ic["sample"].index[0] != backtest_dates[0]
    return {"first_ic_date": str(ic["sample"].index[0].date()), "first_backtest_decision": str(backtest_dates[0].date())}


def category_scoring_disagreement():
    from core.synthesis.faa_eaa import category_score_from_matrices
    from core.synthesis.eligibility import build_category_scores_with_details
    idx = pd.to_datetime(["2024-01-02"])
    mats = [pd.DataFrame([[1., 2., 3.]], index=idx, columns=["A", "B", "C"]),
            pd.DataFrame([[np.nan, 2., 3.]], index=idx, columns=["A", "B", "C"])]
    cats = (FactorCategory("test", "test", (FactorInstance("f1"), FactorInstance("f2"))),)
    with patch("core.synthesis.eligibility.build_category_factors", return_value=mats):
        strict = build_category_scores_with_details(pd.DataFrame(), ["A", "B", "C"], cats)
    dashboard = category_score_from_matrices(mats)
    assert np.isnan(strict.scores["test"].iloc[0, 0]) and np.isfinite(dashboard.iloc[0, 0])
    return {"strategy_category_score": None, "dashboard_category_score": float(dashboard.iloc[0, 0])}


def joinquant_cash_target():
    results = {}
    for strategy in ("faa", "eaa"):
        with patch.dict(sys.modules, {"jqdata": ModuleType("jqdata")}):
            module = runpy.run_path(str(ROOT / "research" / "joinquant" / f"{strategy}_strategy.py"))
        orders = []
        context = SimpleNamespace(portfolio=SimpleNamespace(total_value=2000, available_cash=200,
                    positions={s: SimpleNamespace(total_amount=900, value=900) for s in ("A", "B")}))
        module["_rebalance"].__globals__["order_target_value"] = lambda sec, val: orders.append([sec, val])
        module["_rebalance"](context, {"A": .5, "B": .5})
        assert orders[0] == ["A", 199.0]
        # A real execution may update cash before B; the first target is already wrong.
        results[strategy] = {"current_A_value": 900, "desired_A_value": 1000, "submitted_A_target": orders[0][1]}
    return results


CASES = [cache_start, partial_replace, cache_revision, sync_boundaries, hfq_fallback,
         missing_factor_observations, drawdown_window, overlapping_returns, nonfinite_api_stats,
         default_strategy_params, disabled_category_issues, beta_zero, thread_start_failure,
         invalid_rule_persists, macro_alignment, icir_lookahead, research_missing_dates, infinite_initial_cash,
         dashboard_return_window, dashboard_stale_cache, research_ic_schedule, category_scoring_disagreement,
         joinquant_cash_target]


if __name__ == "__main__":
    init_db()
    outputs = {}
    # No external requests permitted by these deterministic examples.
    with patch.object(socket.socket, "connect", side_effect=AssertionError("audit forbids network")):
        for case in CASES:
            try:
                outputs[case.__name__] = {"reproduced": True, "evidence": case()}
            except Exception as exc:
                outputs[case.__name__] = {"reproduced": False, "error": f"{type(exc).__name__}: {exc}"}
    engine.dispose()
    TEMP.cleanup()
    output = Path(__file__).with_name("evidence.json")
    output.write_text(json.dumps(outputs, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(outputs, ensure_ascii=True, indent=2, allow_nan=False))
    sys.exit(0 if all(r["reproduced"] for r in outputs.values()) else 1)
