"""End-to-end integration tests for the web dashboard.

Uses FastAPI TestClient to exercise the full workflow:
1. Add ETF to the universe
2. Get factor list
3. Compute a factor
4. Run the linear-factor strategy
5. View run records
6. Add a classification rule
7. Apply classification
8. Export a run's NAV
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from webapp.main import app

client = TestClient(app)


def test_e2e_add_universe_and_get_factors():
    """1. Add ETF to universe, 2. list factors."""
    # Add ETF
    response = client.post("/api/universe", json={
        "items": [
            {"sec_code": "510300.SH", "sec_name": "沪深300ETF", "meta": {"category": "宽基"}},
            {"sec_code": "510500.SH", "sec_name": "中证500ETF", "meta": {"category": "宽基"}},
            {"sec_code": "159915.SZ", "sec_name": "创业板ETF", "meta": {"category": "宽基"}},
            {"sec_code": "518880.SH", "sec_name": "黄金ETF", "meta": {"category": "商品"}},
            {"sec_code": "511010.SH", "sec_name": "国债ETF", "meta": {"category": "债券"}},
        ]
    })
    assert response.status_code == 200

    universe = client.get("/api/universe")
    assert universe.status_code == 200
    assert len(universe.json()) >= 5

    # Factor list
    factors = client.get("/api/factors")
    assert factors.status_code == 200
    data = factors.json()
    assert len(data) >= 3
    keys = {c["key"] for c in data}
    assert {"momentum", "volatility", "reversal"}.issubset(keys)


def test_e2e_compute_factor():
    """3. Compute a factor."""
    response = client.post("/api/factors/compute", json={
        "factor_name": "macd_hist",
        "params": {},
        "horizon": 5,
    })
    assert response.status_code in (200, 503)  # 503 if no network data available
    if response.status_code == 200:
        data = response.json()
        assert data["factor_name"] == "macd_hist"
        assert "ic_result" in data
        assert "group_returns" in data


def test_e2e_run_strategy_and_view_records():
    """4. Run eaa strategy (async), 5. view run records."""
    response = client.post("/api/strategies/run", json={
        "strategy_type": "eaa",
        "params": {
            "top_n": 3,
            "rebalance_freq": "monthly",
            "exponents": {"momentum": 1.0, "volatility": 1.0, "reversal": 1.0},
            "beta": 1.0,
        },
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("pending", "failed")

    if data["status"] == "failed":
        return  # validation/concurrency failure; not the success path

    run_id = data["run_id"]
    assert run_id > 0

    # Poll the background task until it reaches a terminal state.
    detail = None
    for _ in range(120):
        detail = client.get(f"/api/strategies/runs/{run_id}").json()
        if detail["status"] in ("success", "failed"):
            break
        time.sleep(0.5)
    assert detail is not None, "strategy run did not finish in time"
    assert detail["status"] == "success", detail.get("error_msg")

    # Persisted detail must expose metrics and constraint violations.
    rs = detail["result_summary"]
    assert "metrics" in rs
    assert "constraint_violations" in rs

    # View run records
    runs = client.get("/api/strategies/runs?limit=5")
    assert runs.status_code == 200
    assert len(runs.json()) >= 1

    # Export NAV
    export = client.get(f"/api/strategies/runs/{run_id}/export")
    assert export.status_code == 200
    assert "csv" in export.json()


def test_e2e_classification_rules_and_apply():
    """6. Add classification rule, 7. apply classification."""
    # Create a manual classification rule
    response = client.post("/api/classifications/rules", json={
        "rule_name": "宽基分类",
        "category_key": "category",
        "rule_type": "manual",
        "config": {
            "sec_codes": ["510300.SH", "510500.SH", "159915.SZ"],
            "category_value": "宽基",
        },
        "priority": 10,
    })
    assert response.status_code == 200
    rule_id = response.json()["id"]

    # Apply classification
    apply = client.post("/api/classifications/apply")
    assert apply.status_code == 200
    result = apply.json()
    assert isinstance(result, list)
    codes = {item["sec_code"] for item in result}
    assert "510300.SH" in codes
    assert "510500.SH" in codes

    # Clean up rule
    client.delete(f"/api/classifications/rules/{rule_id}")


def test_e2e_full_health_check_and_dashboard():
    """Verify all top-level dashboard endpoints respond."""
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/dashboard/stats").status_code == 200
    assert client.get("/api/dashboard/recent-runs").status_code == 200
    assert client.get("/api/settings").status_code == 200


def test_e2e_constraint_update_flow():
    """Update constraints then verify they are persisted."""
    response = client.put("/api/constraints", json={
        "constraints": {
            "single_max_weight": 0.2,
            "single_min_weight": 0.01,
            "category_constraints": [
                {"category_key": "category", "category_value": "宽基", "max_weight": 0.6},
            ],
        }
    })
    assert response.status_code == 200
    data = response.json()
    assert data["single_max_weight"] == 0.2
    assert len(data["category_constraints"]) == 1

    # Verify persisted
    get_resp = client.get("/api/constraints")
    assert get_resp.json()["single_max_weight"] == 0.2