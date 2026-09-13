"""Tests for the AkShare data source (offline, BUG-05)."""

from __future__ import annotations

import pandas as pd
import pytest

from core.data.akshare_source import AkShareDataSource


def test_no_unadjusted_fallback(monkeypatch):
    """BUG-05: hfq 为空时不得切换未复权来源，直接返回空（上层保留旧数据）。"""
    monkeypatch.setattr(
        AkShareDataSource,
        "_fetch_hfq_price",
        lambda self, ak, sec_code, start_date, end_date: pd.DataFrame(),
    )
    # fallback 已移除：_fetch_sina_price 不应再存在
    assert not hasattr(AkShareDataSource, "_fetch_sina_price")

    ds = AkShareDataSource()
    out = ds.get_etf_price_by_codes(
        ["510300.SH"], start_date="2024-01-02", end_date="2024-01-03"
    )
    assert out.empty


def _install_segment_stub(monkeypatch) -> list[tuple[str, str]]:
    """记录 _tencent_get 的分段调用（返回空行），供分段边界断言。"""
    calls: list[tuple[str, str]] = []

    def fake_get(hs_code: str, start: str, end: str, fq: str, retries: int = 3):
        calls.append((start, end))
        return []

    monkeypatch.setattr(AkShareDataSource, "_tencent_get", staticmethod(fake_get))
    return calls


def test_tencent_fetch_range_two_year_segments(monkeypatch):
    """2 年分段：2021-01-04~2026-09-11 → 恰 3 段，段边界如开发计划 2.1(a)。"""
    calls = _install_segment_stub(monkeypatch)

    AkShareDataSource._tencent_fetch_range("sh510300", "2021-01-04", "2026-09-11", "hfq")

    assert calls == [
        ("2021-01-01", "2022-12-31"),
        ("2023-01-01", "2024-12-31"),
        ("2025-01-01", "2026-09-11"),
    ]


# ── 腾讯源加固：UA / 限速 / 指数退避 / WAF 识别 ────────────────────────


from core.data import akshare_source as ak_mod  # noqa: E402
from core.data.akshare_source import TencentSourceError  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not a json payload")
        return self._payload


def test_tencent_get_sends_browser_headers(monkeypatch):
    """请求必须带浏览器 UA + Referer（requests 默认 UA 是爬虫特征）。"""
    calls = {}

    def fake_get(url, headers=None, timeout=None):
        calls["url"] = url
        calls["headers"] = headers
        return _FakeResponse(payload={"data": {"sh510300": {"hfqday": []}}})

    monkeypatch.setattr(ak_mod.requests, "get", fake_get)
    AkShareDataSource._tencent_get("sh510300", "2024-01-01", "2024-01-10", "hfq", retries=1)

    assert "Mozilla/5.0" in calls["headers"]["User-Agent"]
    assert calls["headers"]["Referer"].startswith("https://gu.qq.com")
    assert "param=sh510300,day," in calls["url"]


@pytest.mark.parametrize(
    "status_code, text",
    [
        (501, ""),
        (200, "<html>waf.tencent.com/501page.html</html>"),
    ],
)
def test_tencent_get_waf_fails_immediately(monkeypatch, status_code, text):
    """WAF 拦截（HTTP 501/拦截页）不重试：恰 1 次请求即抛，无退避 sleep。"""
    attempts = {"n": 0}
    sleeps: list[float] = []

    def fake_get(url, headers=None, timeout=None):
        attempts["n"] += 1
        return _FakeResponse(status_code=status_code, text=text)

    monkeypatch.setattr(ak_mod, "_next_request_at", 0.0)
    monkeypatch.setattr(ak_mod.requests, "get", fake_get)
    monkeypatch.setattr(ak_mod.time, "sleep", lambda s: sleeps.append(s))

    with pytest.raises(TencentSourceError, match="WAF"):
        AkShareDataSource._tencent_get("sh510300", "2024-01-01", "2024-01-10", "hfq", retries=3)
    assert attempts["n"] == 1
    assert sleeps == []


def test_tencent_get_bad_json_retries_then_raises(monkeypatch):
    """非 JSON 响应（疑似拦截页）视为可重试错误。"""
    attempts = {"n": 0}

    def fake_get(url, headers=None, timeout=None):
        attempts["n"] += 1
        return _FakeResponse(status_code=200, text="<html>not json</html>", payload=None)

    monkeypatch.setattr(ak_mod.requests, "get", fake_get)
    monkeypatch.setattr(ak_mod.time, "sleep", lambda s: None)

    with pytest.raises(TencentSourceError):
        AkShareDataSource._tencent_get("sh510300", "2024-01-01", "2024-01-10", "hfq", retries=2)
    assert attempts["n"] == 2


def test_tencent_get_network_error_raises(monkeypatch):
    """网络异常重试耗尽后抛错，异常信息携带最后一次失败原因。"""
    def fake_get(url, headers=None, timeout=None):
        raise ConnectionError("unreachable")

    monkeypatch.setattr(ak_mod.requests, "get", fake_get)
    monkeypatch.setattr(ak_mod.time, "sleep", lambda s: None)

    with pytest.raises(TencentSourceError, match="网络请求失败"):
        AkShareDataSource._tencent_get("sh510300", "2024-01-01", "2024-01-10", "hfq", retries=1)


def test_tencent_get_network_error_still_retries(monkeypatch):
    """其他非 200（502/503 等瞬时错误）保留退避重试，耗尽后抛错。"""
    attempts = {"n": 0}
    sleeps: list[float] = []

    def fake_get(url, headers=None, timeout=None):
        attempts["n"] += 1
        return _FakeResponse(status_code=502, text="bad gateway")

    monkeypatch.setattr(ak_mod, "_next_request_at", 0.0)
    monkeypatch.setattr(ak_mod.requests, "get", fake_get)
    monkeypatch.setattr(ak_mod.time, "sleep", lambda s: sleeps.append(s))

    with pytest.raises(TencentSourceError, match="HTTP 502"):
        AkShareDataSource._tencent_get("sh510300", "2024-01-01", "2024-01-10", "hfq", retries=2)
    assert attempts["n"] == 2
    assert 1.0 in sleeps


def test_tencent_get_parses_hfq_rows(monkeypatch):
    """正常 JSON 应答解析为行数据。"""
    payload = {
        "data": {
            "sh510300": {
                "hfqday": [["2024-01-02", "1.0", "2.0", "3.0", "0.9", "100", "1000"]]
            }
        }
    }

    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse(payload=payload)

    monkeypatch.setattr(ak_mod.requests, "get", fake_get)
    rows = AkShareDataSource._tencent_get("sh510300", "2024-01-01", "2024-01-10", "hfq", retries=1)
    assert rows and rows[0][0] == "2024-01-02"


def test_tencent_get_empty_data_is_not_error(monkeypatch):
    """接口正常应答但区间无数据（未上市等）不触发重试，返回空列表。"""
    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse(payload={"data": {}})

    monkeypatch.setattr(ak_mod.requests, "get", fake_get)
    rows = AkShareDataSource._tencent_get("sh510300", "2024-01-01", "2024-01-10", "hfq", retries=3)
    assert rows == []


def test_throttle_applies_interval_range(monkeypatch):
    """全局限速器：第二次等待落在抖动区间 [min, max] 内。"""
    sleeps: list[float] = []
    monkeypatch.setattr(ak_mod, "_next_request_at", 0.0)
    monkeypatch.setattr(ak_mod.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(ak_mod, "_TENCENT_INTERVAL_RANGE", (0.75, 1.25))

    ak_mod._throttle()
    ak_mod._throttle()

    # 第一次不等待；第二次等待为 random.uniform(0.75, 1.25) 的取值
    assert sleeps and 0.75 <= sleeps[-1] <= 1.25


def test_set_tencent_interval_range(monkeypatch):
    """setter 覆盖全局节流区间；非法区间（low>high 或 low<=0）拒绝。"""
    monkeypatch.setattr(ak_mod, "_TENCENT_INTERVAL_RANGE", (0.75, 1.25))

    ak_mod.set_tencent_interval_range(0.5, 0.9)
    assert ak_mod._TENCENT_INTERVAL_RANGE == (0.5, 0.9)

    with pytest.raises(ValueError):
        ak_mod.set_tencent_interval_range(1.5, 1.0)
    with pytest.raises(ValueError):
        ak_mod.set_tencent_interval_range(0.0, 1.0)


def test_get_etf_price_by_codes_propagates_tencent_error(monkeypatch):
    """WAF 拦截 fail-fast：TencentSourceError 从批量接口向上传播，不吞掉。"""

    def fake_fetch(self, ak, sec_code, start_date, end_date):
        raise TencentSourceError("腾讯行情接口连续 3 次请求失败：接口被拦截(HTTP 501)")

    monkeypatch.setattr(AkShareDataSource, "_fetch_hfq_price", fake_fetch)

    with pytest.raises(TencentSourceError, match="HTTP 501"):
        AkShareDataSource().get_etf_price_by_codes(
            ["510300.SH", "510500.SH"], "2024-01-01", "2024-01-10", "daily"
        )
