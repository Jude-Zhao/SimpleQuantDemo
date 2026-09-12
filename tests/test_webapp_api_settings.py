"""Tests for settings API endpoint."""

from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from webapp.api import settings as settings_module
from webapp.config import WebAppConfig
from webapp.main import app

pytestmark = pytest.mark.usefixtures("webapp_clean_state")

client = TestClient(app)

# 测试专用凭据值：足够独特，避免与响应其余字段意外撞串
_PW = "S3cretPw-DO-NOT-LEAK"
_QUERY_PW = "QueryPw-Leak01"
_QUERY_TOKEN = "TokenLeak99"
_CRED_URL = (
    f"postgresql://alice:{_PW}@db.internal:5432/quantapp"
    f"?password={_QUERY_PW}&token={_QUERY_TOKEN}&sslmode=require"
)


def _fake_config(database_url: str) -> WebAppConfig:
    """构造带指定 database.url 的完整配置。

    get_config 是 lru_cache：端点测试用 monkeypatch 替换 settings 模块内的
    get_config 引用注入配置，不触碰全局缓存（最小侵入）。
    """
    return WebAppConfig(
        server={"host": "127.0.0.1", "port": 8000},
        database={"url": database_url},
        datasource={
            "primary": "akshare",
            "cache_enabled": True,
            "cache_days_daily": 365,
        },
        sync={"default_start_date": "2021-01-04"},
        factors={"auto_discover": True, "scan_package": "core.factors"},
        strategy={"max_history_runs": 100},
    )


def test_get_settings():
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert "datasource" in data
    assert "system" in data
    assert "primary" in data["datasource"]
    assert "cache_enabled" in data["datasource"]
    assert "cache_days_daily" in data["datasource"]
    assert "version" in data["system"]
    assert "database_connected" in data["system"]


# ── 纯函数 _display_database_url（BUG-20）──────────────────────────────


def test_display_database_url_masks_password_and_sensitive_query():
    shown = settings_module._display_database_url(_CRED_URL)
    assert _PW not in shown
    assert _QUERY_PW not in shown
    assert _QUERY_TOKEN not in shown
    assert "alice:***@" in shown  # username 保留
    assert "db.internal:5432/quantapp" in shown  # 定位信息保留
    assert "password=***" in shown
    assert "token=***" in shown
    assert "sslmode=require" in shown  # 非敏感 query 保留


def test_display_database_url_sensitive_query_case_insensitive():
    shown = settings_module._display_database_url(
        "mysql://u:p@h/db?Password=abc&SECRET=x&keep=1"
    )
    assert "abc" not in shown
    assert "Password=***" in shown
    assert "SECRET=***" in shown
    assert "keep=1" in shown


def test_display_database_url_sqlite_unchanged():
    url = "sqlite:///./data/simple_quant.db"
    assert settings_module._display_database_url(url) == url


def test_display_database_url_invalid_returns_placeholder():
    assert settings_module._display_database_url("not-a-url") == "[invalid database URL]"
    assert settings_module._display_database_url("") == "[invalid database URL]"


# ── 端点响应（注入配置；连接探测走真实测试库 engine，无外部连接）─────────


def test_get_settings_masks_database_credentials(monkeypatch):
    monkeypatch.setattr(settings_module, "get_config", lambda: _fake_config(_CRED_URL))
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["system"]["database_connected"] is True  # 探测行为不变
    shown = data["system"]["database_url"]
    assert _PW not in shown
    assert _QUERY_PW not in shown
    assert _QUERY_TOKEN not in shown
    assert "alice:***@db.internal:5432/quantapp" in shown
    assert "password=***" in shown
    assert "token=***" in shown
    assert "sslmode=require" in shown
    # 整个响应载荷也不含原始凭据
    assert _PW not in response.text
    assert _QUERY_PW not in response.text
    assert _QUERY_TOKEN not in response.text


def test_get_settings_sqlite_url_shown_as_is(monkeypatch):
    url = "sqlite:///./data/simple_quant.db"
    monkeypatch.setattr(settings_module, "get_config", lambda: _fake_config(url))
    response = client.get("/api/settings")
    assert response.status_code == 200
    assert response.json()["system"]["database_url"] == url


def test_get_settings_invalid_database_url_not_echoed(monkeypatch):
    monkeypatch.setattr(settings_module, "get_config", lambda: _fake_config("not-a-url"))
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["system"]["database_url"] == "[invalid database URL]"
    assert "not-a-url" not in data["system"]["database_url"]


def test_get_settings_connection_failure_hides_credentials(monkeypatch):
    """patch 连接失败：database_connected 为 False，异常/响应不泄露连接串。"""
    monkeypatch.setattr(settings_module, "get_config", lambda: _fake_config(_CRED_URL))

    class _BrokenEngine:
        def connect(self):
            raise RuntimeError("connect refused by test stub")

    monkeypatch.setattr(settings_module, "engine", _BrokenEngine())
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["system"]["database_connected"] is False
    assert data["system"]["database_url"] == (
        "postgresql://alice:***@db.internal:5432/quantapp?password=***&sslmode=require&token=***"
    )
    assert _PW not in response.text
    assert _QUERY_PW not in response.text
    assert _QUERY_TOKEN not in response.text