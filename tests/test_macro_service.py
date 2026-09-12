"""Tests for macro data service layer (no network, uses in-memory DB)."""

from __future__ import annotations

import pandas as pd
import pytest

from webapp.models.database import Base, SessionLocal
from webapp.services.macro_service import (
    DAILY_FIELDS,
    MONTHLY_FIELDS,
    get_daily_macro,
    get_monthly_macro,
    list_fields,
)


@pytest.fixture()
def db():
    """In-memory SQLite session with schema."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def test_list_fields_daily():
    fields = list_fields("daily")
    assert len(fields) == len(DAILY_FIELDS)
    names = {f["name"] for f in fields}
    assert {"shibor_3m", "cn_gov_10y", "spx", "hsi"}.issubset(names)


def test_list_fields_monthly():
    fields = list_fields("monthly")
    assert len(fields) == len(MONTHLY_FIELDS)
    names = {f["name"] for f in fields}
    assert {"cpi_yoy", "ppi_yoy", "aggregate_financing"}.issubset(names)
    assert not {"m1_yoy", "m2_yoy"} & names  # 货币供应字段已随 baostock 移除


def test_list_fields_all():
    fields = list_fields()
    assert len(fields) == len(DAILY_FIELDS) + len(MONTHLY_FIELDS)


def test_get_daily_macro_empty(db):
    df = get_daily_macro(db)
    assert df.empty


def test_get_monthly_macro_empty(db):
    df = get_monthly_macro(db)
    assert df.empty


def test_get_daily_macro_returns_data(db):
    from webapp.models.macro import MacroDaily

    db.add(MacroDaily(trade_date="2024-01-02", shibor_3m=2.45, cn_gov_10y=2.56))
    db.add(MacroDaily(trade_date="2024-01-03", shibor_3m=2.42, cn_gov_10y=2.55))
    db.commit()

    df = get_daily_macro(db)
    assert len(df) == 2
    assert "shibor_3m" in df.columns
    assert df.loc["2024-01-02", "shibor_3m"] == pytest.approx(2.45)

    # Date filter
    df2 = get_daily_macro(db, start_date="2024-01-03")
    assert len(df2) == 1

    # Field filter
    df3 = get_daily_macro(db, fields=["shibor_3m"])
    assert list(df3.columns) == ["shibor_3m"]


def test_get_monthly_macro_returns_data(db):
    from webapp.models.macro import MacroMonthly

    db.add(MacroMonthly(trade_month="2024-01", cpi_yoy=-0.8, ppi_yoy=-2.5))
    db.add(MacroMonthly(trade_month="2024-02", cpi_yoy=0.7, ppi_yoy=-2.3))
    db.commit()

    df = get_monthly_macro(db)
    assert len(df) == 2
    assert "cpi_yoy" in df.columns
    assert df.loc["2024-01", "cpi_yoy"] == pytest.approx(-0.8)

    df2 = get_monthly_macro(db, start_month="2024-02")
    assert len(df2) == 1


# ── BUG-03: 宏观区间同步保留区间外历史 ─────────────────────────────────


def _make_task():
    from webapp.services.macro_service import MacroSyncTask

    return MacroSyncTask(task_id="t", frequency="daily")


def _daily_frame(rows: dict[str, dict[str, float | None]]) -> pd.DataFrame:
    """rows: {date_str: {field: value}} → DataFrame indexed by Timestamp."""
    data = {date: values for date, values in rows.items()}
    df = pd.DataFrame.from_dict(data, orient="index")
    df.index = pd.to_datetime(df.index)
    return df


def test_sync_daily_only_updates_requested_range(db, monkeypatch):
    """只同步二月：一月和三月不变；超范围返回不写入；缺字段保留旧值。"""
    from core.data.akshare_source import AkShareDataSource

    from webapp.models.macro import MacroDaily
    from webapp.services.macro_service import _sync_daily

    db.add(MacroDaily(trade_date="2024-01-31", shibor_3m=1.0, cn_gov_10y=1.1))
    db.add(MacroDaily(trade_date="2024-02-15", shibor_3m=2.0, cn_gov_10y=2.2))
    db.add(MacroDaily(trade_date="2024-03-15", shibor_3m=3.0, cn_gov_10y=3.3))
    db.commit()

    # 源返回包含越界的一月/三月，且二月行缺 cn_gov_10y
    df = _daily_frame(
        {
            "2024-01-31": {"shibor_3m": 9.9, "cn_gov_10y": 9.9},
            "2024-02-15": {"shibor_3m": 2.5, "cn_gov_10y": None},
            "2024-03-15": {"shibor_3m": 9.9, "cn_gov_10y": 9.9},
        }
    )
    monkeypatch.setattr(
        AkShareDataSource,
        "get_macro_factors",
        lambda self, start_date=None, end_date=None, trading_dates=None: df,
    )

    _sync_daily(db, _make_task(), "2024-02-01", "2024-02-29")

    rows = {r.trade_date: r for r in db.query(MacroDaily).all()}
    assert rows["2024-01-31"].shibor_3m == pytest.approx(1.0)  # 越界不写入
    assert rows["2024-03-15"].shibor_3m == pytest.approx(3.0)  # 越界不写入
    assert rows["2024-02-15"].shibor_3m == pytest.approx(2.5)  # 范围内覆盖
    assert rows["2024-02-15"].cn_gov_10y == pytest.approx(2.2)  # 缺字段保留旧值


def test_sync_daily_no_bounds_upserts_without_clearing(db, monkeypatch):
    """无日期边界：按返回键更新，不隐式清表。"""
    from core.data.akshare_source import AkShareDataSource

    from webapp.models.macro import MacroDaily
    from webapp.services.macro_service import _sync_daily

    db.add(MacroDaily(trade_date="2024-01-31", shibor_3m=1.0, cn_gov_10y=1.1))
    db.commit()

    df = _daily_frame(
        {
            "2024-01-31": {"shibor_3m": 1.5, "cn_gov_10y": 1.6},
            "2024-02-01": {"shibor_3m": 2.5, "cn_gov_10y": 2.6},
        }
    )
    monkeypatch.setattr(
        AkShareDataSource,
        "get_macro_factors",
        lambda self, start_date=None, end_date=None, trading_dates=None: df,
    )

    _sync_daily(db, _make_task(), None, None)

    rows = {r.trade_date: r for r in db.query(MacroDaily).all()}
    assert set(rows) == {"2024-01-31", "2024-02-01"}
    assert rows["2024-01-31"].shibor_3m == pytest.approx(1.5)


def test_sync_daily_write_failure_rolls_back(db, monkeypatch):
    """写入失败回滚：范围内旧行不变、无部分写入。"""
    from core.data.akshare_source import AkShareDataSource

    from webapp.models.macro import MacroDaily
    from webapp.services.macro_service import _sync_daily

    db.add(MacroDaily(trade_date="2024-01-31", shibor_3m=1.0, cn_gov_10y=1.1))
    db.commit()

    df = _daily_frame({"2024-02-15": {"shibor_3m": 2.5, "cn_gov_10y": 2.6}})
    monkeypatch.setattr(
        AkShareDataSource,
        "get_macro_factors",
        lambda self, start_date=None, end_date=None, trading_dates=None: df,
    )

    def _bad_commit():
        raise RuntimeError("commit boom")

    monkeypatch.setattr(db, "commit", _bad_commit)
    with pytest.raises(RuntimeError):
        _sync_daily(db, _make_task(), "2024-02-01", "2024-02-29")

    monkeypatch.undo()
    rows = {r.trade_date: r for r in db.query(MacroDaily).all()}
    assert set(rows) == {"2024-01-31"}
    assert rows["2024-01-31"].shibor_3m == pytest.approx(1.0)


class _FakeAk:
    """akshare module test double returning canned indicator frames."""

    def __init__(
        self,
        cpi: dict[str, float],
        ppi: dict[str, float],
        shrzgm: dict[str, float],
    ):
        self.cpi = cpi
        self.ppi = ppi
        self.shrzgm = shrzgm

    def _indicator_frame(self, values: dict[str, float]) -> pd.DataFrame:
        months = sorted(values)
        return pd.DataFrame(
            {
                "商品": ["ind"] * len(months),
                "日期": [f"{m}-15" for m in months],
                "今值": [values[m] for m in months],
            }
        )

    def macro_china_cpi_yearly(self):
        return self._indicator_frame(self.cpi)

    def macro_china_ppi_yearly(self):
        return self._indicator_frame(self.ppi)

    def macro_china_shrzgm(self):
        months = sorted(self.shrzgm)
        return pd.DataFrame(
            {
                "月份": [m.replace("-", "") for m in months],
                "社会融资规模增量": [self.shrzgm[m] for m in months],
            }
        )


def test_sync_monthly_cross_year_boundary_and_clipping(db, monkeypatch):
    """跨年月份边界 2025-12~2026-02 含三个月；区间外不写入；缺字段保留旧值。"""
    from webapp.models.macro import MacroMonthly
    from webapp.services.macro_service import _sync_monthly

    db.add(MacroMonthly(trade_month="2025-11", cpi_yoy=-9.0, ppi_yoy=-11.0))
    db.add(MacroMonthly(trade_month="2025-12", cpi_yoy=-1.0, ppi_yoy=-12.0))
    db.add(MacroMonthly(trade_month="2026-02", ppi_yoy=-13.0))
    db.commit()

    months = ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03"]
    fake_ak = _FakeAk(
        cpi={m: -1.0 + i * 0.1 for i, m in enumerate(months) if m != "2026-02"},
        ppi={m: -2.0 + i * 0.1 for i, m in enumerate(months)},
        shrzgm={m: 10000.0 + i for i, m in enumerate(months)},
    )
    monkeypatch.setattr("core.data.akshare_source.ensure_akshare_available", lambda: fake_ak)

    task = _make_task()
    task.frequency = "monthly"
    _sync_monthly(db, task, "2025-12-01", "2026-02-28")

    rows = {r.trade_month: r for r in db.query(MacroMonthly).all()}
    assert "2026-03" not in rows  # 越界不写入
    assert rows["2025-11"].cpi_yoy == pytest.approx(-9.0)  # 越界不变
    assert rows["2025-11"].ppi_yoy == pytest.approx(-11.0)
    # 2025-12：既有行，非空字段更新
    assert rows["2025-12"].cpi_yoy == pytest.approx(-0.9)
    assert rows["2025-12"].ppi_yoy == pytest.approx(-1.9)
    assert rows["2025-12"].aggregate_financing == pytest.approx(10001.0)
    # 2026-01：新插入（跨年区间内第三个月）
    assert rows["2026-01"].cpi_yoy == pytest.approx(-0.8)
    assert rows["2026-01"].aggregate_financing == pytest.approx(10002.0)
    # 2026-02：cpi 缺失保留旧值（本就为空）；ppi/社融正常更新
    assert rows["2026-02"].cpi_yoy is None
    assert rows["2026-02"].ppi_yoy == pytest.approx(-1.7)
    assert rows["2026-02"].aggregate_financing == pytest.approx(10003.0)
    assert task.result["total_months"] == 3


def test_sync_monthly_write_failure_rolls_back(db, monkeypatch):
    """月频写入失败回滚：旧行不变。"""
    from webapp.models.macro import MacroMonthly
    from webapp.services.macro_service import _sync_monthly

    db.add(MacroMonthly(trade_month="2025-12", cpi_yoy=-1.0))
    db.commit()

    months = ["2025-12", "2026-01"]
    fake_ak = _FakeAk(
        cpi={m: -0.5 for m in months},
        ppi={m: -2.0 for m in months},
        shrzgm={m: 20000.0 for m in months},
    )
    monkeypatch.setattr("core.data.akshare_source.ensure_akshare_available", lambda: fake_ak)

    def _bad_commit():
        raise RuntimeError("commit boom")

    monkeypatch.setattr(db, "commit", _bad_commit)
    with pytest.raises(RuntimeError):
        _sync_monthly(db, _make_task(), "2025-12-01", "2026-01-31")

    monkeypatch.undo()
    rows = {r.trade_month: r for r in db.query(MacroMonthly).all()}
    assert set(rows) == {"2025-12"}
    assert rows["2025-12"].cpi_yoy == pytest.approx(-1.0)
    assert rows["2025-12"].aggregate_financing is None
