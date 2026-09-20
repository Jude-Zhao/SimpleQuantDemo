"""Tests for dashboard API endpoints."""

from __future__ import annotations

import pandas as pd
import pytest

from fastapi.testclient import TestClient

from webapp.api import dashboard as dashboard_module
from webapp.main import app

pytestmark = pytest.mark.usefixtures("webapp_clean_state")

client = TestClient(app)


def test_dashboard_stats():
    response = client.get("/api/dashboard/stats")
    assert response.status_code == 200
    data = response.json()
    assert "universe_count" in data
    assert "factor_count" in data
    assert "run_count_today" in data
    assert "system_status" in data
    assert data["system_status"] == "ok"
    # factor_count counts factor instances from factors.yaml (>= 3 non-empty classes)
    assert data["factor_count"] >= 3


def test_dashboard_recent_runs():
    response = client.get("/api/dashboard/recent-runs?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_dashboard_factor_ranking():
    response = client.get("/api/dashboard/factor-ranking")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    keys = set()
    for item in data:
        assert "key" in item
        assert "display_name" in item
        assert "is_empty" in item
        assert "class_rank_ic_mean" in item
        assert "class_rank_icir" in item
        assert "factors" in item
        assert isinstance(item["factors"], list)
        keys.add(item["key"])
        for f in item["factors"]:
            assert "name" in f
            assert "params" in f
            assert "rank_ic_mean" in f
            assert "rank_icir" in f
    # factor categories come from factors.yaml, so keys must be unique.
    assert len(keys) == len(data)


def test_dashboard_returns_ranking():
    response = client.get("/api/dashboard/returns-ranking?days=20")
    assert response.status_code == 200
    data = response.json()
    assert "days" in data
    assert "as_of" in data
    assert "momentum" in data
    assert "reversal" in data
    assert data["days"] == 20


# ── F16: N 日涨跌幅端点数（N 个收益期需要 N+1 个价格端点）──────────────


def _patch_returns_data(monkeypatch, price_data: pd.DataFrame, codes: list[str]):
    """让涨跌幅榜跑在微型确定性数据上，不读共享种子库。"""
    etfs = [{"sec_code": c, "sec_name": c, "category": ""} for c in codes]
    monkeypatch.setattr(dashboard_module, "get_etf_list", lambda db: etfs)
    monkeypatch.setattr(
        dashboard_module, "get_etf_price", lambda db, universe, start, end: price_data
    )


def _three_day_prices(closes_by_sec: dict[str, list[float]]) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=3)
    frames = []
    for sec, closes in closes_by_sec.items():
        frames.append(pd.DataFrame({"date": dates, "sec": sec, "close": closes}))
    return pd.concat(frames, ignore_index=True)


def test_returns_ranking_uses_days_plus_1_endpoints(monkeypatch):
    # 审计证据：[100, 110, 121]，days=2 应为 21%（旧实现取 2 个端点得 10%）
    price_data = _three_day_prices({"A1": [100.0, 110.0, 121.0], "B1": [200.0, 200.0, 264.0]})
    _patch_returns_data(monkeypatch, price_data, ["A1", "B1"])

    response = client.get("/api/dashboard/returns-ranking?days=2")
    assert response.status_code == 200
    data = response.json()
    assert data["days"] == 2
    ret = {i["sec_code"]: i["return_pct"] for i in data["momentum"]}
    assert ret["A1"] == pytest.approx(0.21)
    assert ret["B1"] == pytest.approx(0.32)

    # 旧实现 days=1 返回空榜；现在用 2 个端点得 1 期收益
    response = client.get("/api/dashboard/returns-ranking?days=1")
    assert response.status_code == 200
    data = response.json()
    assert data["days"] == 1
    ret = {i["sec_code"]: i["return_pct"] for i in data["momentum"]}
    assert ret["A1"] == pytest.approx(0.10)
    assert ret["B1"] == pytest.approx(0.32)


def test_returns_ranking_missing_endpoint_not_substituted(monkeypatch):
    # B1 首端点缺失：不得回填旧价把 2 期窗口偷换成 1 期（旧实现会报 10%）
    price_data = _three_day_prices({"A1": [100.0, 110.0, 121.0], "B1": [float("nan"), 110.0, 121.0]})
    _patch_returns_data(monkeypatch, price_data, ["A1", "B1"])

    response = client.get("/api/dashboard/returns-ranking?days=2")
    assert response.status_code == 200
    data = response.json()
    ret = {i["sec_code"]: i["return_pct"] for i in data["momentum"]}
    assert ret["A1"] == pytest.approx(0.21)
    assert "B1" not in ret
    assert "B1" not in {i["sec_code"] for i in data["reversal"]}


def test_returns_ranking_short_history_reports_actual_days(monkeypatch):
    # 仅 3 行历史请求 days=20：按实际 2 个观察期报告，不冒充完整窗口
    price_data = _three_day_prices({"A1": [100.0, 110.0, 121.0]})
    _patch_returns_data(monkeypatch, price_data, ["A1"])

    response = client.get("/api/dashboard/returns-ranking?days=20")
    assert response.status_code == 200
    data = response.json()
    assert data["days"] == 2
    assert data["momentum"][0]["return_pct"] == pytest.approx(0.21)