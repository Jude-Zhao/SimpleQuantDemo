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


def _install_tencent_stub(monkeypatch, hfq_rows: list[list], raw_rows: list[list]) -> None:
    def fake_fetch(hs_code: str, start: str, end: str, fq: str, retries: int = 3):
        return list(hfq_rows) if fq == "hfq" else list(raw_rows)

    monkeypatch.setattr(AkShareDataSource, "_tencent_fetch_range", staticmethod(fake_fetch))


def test_hfq_adj_factor_computed(monkeypatch):
    """adj_factor = hfq_close / raw_close（10拆1 例：复权价 11、现价 10 → 1.1）。"""
    hfq_rows = [
        ["2024-01-02", "10.0", "11.0", "10.5", "9.9", "1000", "10000"],
        ["2024-01-03", "11.0", "12.1", "11.5", "10.9", "1000", "10000"],
    ]
    raw_rows = [
        ["2024-01-02", "9.0", "10.0", "9.5", "8.9", "1000", "10000"],
        ["2024-01-03", "9.0", "11.0", "9.5", "8.9", "1000", "10000"],
    ]
    _install_tencent_stub(monkeypatch, hfq_rows, raw_rows)

    ds = AkShareDataSource()
    df = ds._fetch_hfq_price(None, "510300.SH", "2024-01-02", "2024-01-03")

    assert not df.empty
    assert df["adj_factor"].tolist() == pytest.approx([1.1, pytest.approx(12.1 / 11.0)])
    assert set(df["source"]) == {"akshare"}


def test_hfq_raw_missing_adj_factor_is_nan(monkeypatch):
    """raw 序列缺失时 adj_factor 显式置 NaN（不默认造 1）。"""
    hfq_rows = [
        ["2024-01-02", "10.0", "11.0", "10.5", "9.9", "1000", "10000"],
    ]
    _install_tencent_stub(monkeypatch, hfq_rows, [])

    ds = AkShareDataSource()
    df = ds._fetch_hfq_price(None, "510300.SH", "2024-01-02", "2024-01-02")

    assert not df.empty
    assert pd.isna(df["adj_factor"].iloc[0])
    # close 仍是后复权价，不是现价
    assert df["close"].iloc[0] == pytest.approx(11.0)


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


def test_tencent_get_waf_page_raises_after_retries(monkeypatch):
    """WAF 拦截页触发指数退避重试，耗尽后抛 TencentSourceError。"""
    attempts = {"n": 0}
    sleeps: list[float] = []

    def fake_get(url, headers=None, timeout=None):
        attempts["n"] += 1
        return _FakeResponse(status_code=501, text="<html>waf.tencent.com/501page.html</html>")

    monkeypatch.setattr(ak_mod.requests, "get", fake_get)
    monkeypatch.setattr(ak_mod.time, "sleep", lambda s: sleeps.append(s))

    with pytest.raises(TencentSourceError, match="腾讯行情接口"):
        AkShareDataSource._tencent_get("sh510300", "2024-01-01", "2024-01-10", "hfq", retries=3)
    assert attempts["n"] == 3
    # 指数退避：第 2/3 次重试前分别等待 1s、2s（sleeps 里混有限速器
    # 的节拍等待，精确匹配退避序列取值）
    backoff = [s for s in sleeps if s in ak_mod._TENCENT_RETRY_DELAYS]
    assert backoff == [1.0, 2.0]


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


def test_throttle_enforces_min_interval(monkeypatch):
    """全局限速器：连续调用第二次必须等待约 MIN_INTERVAL。"""
    sleeps: list[float] = []
    monkeypatch.setattr(ak_mod, "_next_request_at", 0.0)
    monkeypatch.setattr(ak_mod.time, "sleep", lambda s: sleeps.append(s))

    ak_mod._throttle()
    ak_mod._throttle()

    # 第一次不等待；第二次等待 ≈ MIN_INTERVAL
    assert sleeps and sleeps[-1] == pytest.approx(ak_mod._TENCENT_MIN_INTERVAL, abs=0.05)


def test_get_etf_price_by_codes_propagates_tencent_error(monkeypatch):
    """WAF 拦截 fail-fast：TencentSourceError 从批量接口向上传播，不吞掉。"""

    def fake_fetch(self, ak, sec_code, start_date, end_date):
        raise TencentSourceError("腾讯行情接口连续 3 次请求失败：接口被拦截(HTTP 501)")

    monkeypatch.setattr(AkShareDataSource, "_fetch_hfq_price", fake_fetch)

    with pytest.raises(TencentSourceError, match="HTTP 501"):
        AkShareDataSource().get_etf_price_by_codes(
            ["510300.SH", "510500.SH"], "2024-01-01", "2024-01-10", "daily"
        )
