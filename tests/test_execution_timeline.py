"""B2：唯一逐日账本时间轴测试（决策 T 收盘 → T+1 交易日收盘执行）。

覆盖计划 B2 测试表全部场景：单日、时序、漂移（BUG-07）、缺价估值（BUG-09）、
取消整次、恢复后不重试、缺因子日保留日历、全仓换仓费用。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.backtest.engine import execute_backtest, run_backtest
from core.backtest.models import BacktestConfig, ExecutionConfig


def _close_matrix(prices: dict[str, list[float]], dates) -> pd.DataFrame:
    return pd.DataFrame(prices, index=pd.DatetimeIndex(dates, name="date"))


def _targets(rows: dict[pd.Timestamp, dict[str, float]], cols: list[str]) -> pd.DataFrame:
    idx = pd.DatetimeIndex(list(rows), name="date")
    mat = np.zeros((len(idx), len(cols)))
    for i, d in enumerate(idx):
        for j, c in enumerate(cols):
            mat[i, j] = rows[d].get(c, 0.0)
    return pd.DataFrame(mat, index=idx, columns=cols)


def _long(close: pd.DataFrame) -> pd.DataFrame:
    """宽表转长表（date/sec/open/high/low/close/volume/amount）。"""
    records = []
    for date, row in close.iterrows():
        for sec, price in row.items():
            p = price if pd.notna(price) else np.nan
            records.append(
                {
                    "date": date,
                    "sec": sec,
                    "open": p,
                    "high": p,
                    "low": p,
                    "close": p,
                    "volume": 1000.0,
                    "amount": 1000.0,
                }
            )
    return pd.DataFrame(records)


def test_single_day_unexecuted_end() -> None:
    """单日 + 首日有目标：无交易、净值 1、费用 0、unexecuted_end。"""
    dates = pd.to_datetime(["2026-01-05"])
    close = _close_matrix({"A": [100.0]}, dates)
    tw = _targets({dates[0]: {"A": 1.0}}, ["A"])
    result = execute_backtest(close, tw, ExecutionConfig())

    assert result.equity_curve.iloc[0] == pytest.approx(1.0)
    assert result.fees.iloc[0] == 0.0
    assert result.turnover.iloc[0] == 0.0
    assert result.trades.empty
    assert len(result.execution_log) == 1
    entry = result.execution_log[0]
    assert entry["status"] == "unexecuted_end"
    assert entry["execution_date"] is None
    assert entry["decision_date"] == dates[0]


def test_timeline_zero_fee() -> None:
    """时序：A 100/110/121，首日目标 A，零费 → 净值 1/1/1.1；成交价 110。"""
    dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
    close = _close_matrix({"A": [100.0, 110.0, 121.0]}, dates)
    tw = _targets({dates[0]: {"A": 1.0}}, ["A"])
    result = execute_backtest(close, tw, ExecutionConfig(transaction_cost_bps=0.0))

    assert result.equity_curve.tolist() == pytest.approx([1.0, 1.0, 1.1])
    assert result.daily_returns.tolist() == pytest.approx([0.0, 0.0, pytest.approx(0.1)])
    assert len(result.trades) == 1
    trade = result.trades.iloc[0]
    assert trade["side"] == "buy"
    assert trade["price"] == pytest.approx(110.0)
    assert trade["execution_date"] == dates[1]
    assert trade["decision_date"] == dates[0]
    # T+1 收盘执行：T+1 收盘前旧持仓（现金）承担收益，T+2 首度反映新持仓
    assert result.execution_log[0]["status"] == "executed"
    assert result.execution_log[0]["execution_date"] == dates[1]


def test_drift_keeps_shares() -> None:
    """漂移（BUG-07 直接复现）：A 100/100/200/100，B 恒 100，首日各半，零费。

    正确净值 1/1/1.5/1；份额持有不变（不每日恢复目标比例）。
    """
    dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"])
    close = _close_matrix({"A": [100.0, 100.0, 200.0, 100.0], "B": [100.0] * 4}, dates)
    tw = _targets({dates[0]: {"A": 0.5, "B": 0.5}}, ["A", "B"])
    result = execute_backtest(close, tw, ExecutionConfig(transaction_cost_bps=0.0))

    assert result.equity_curve.tolist() == pytest.approx([1.0, 1.0, 1.5, 1.0])
    # BUG-07 验收：整段收益为零（净值回到 1），份额持有不变
    assert result.equity_curve.iloc[-1] == pytest.approx(1.0)
    # 份额不变：建仓后各日 holdings 相同
    h2 = result.holdings.loc[dates[1]]
    h3 = result.holdings.loc[dates[2]]
    h4 = result.holdings.loc[dates[3]]
    assert h3.equals(h2)
    assert h4.equals(h2)
    assert h2["A"] == pytest.approx(0.005)
    # 实际权重随价格漂移（不再是目标权重）
    assert result.weights.loc[dates[2], "A"] == pytest.approx(2.0 / 3.0)


def test_missing_quote_valuation() -> None:
    """缺价估值（BUG-09）：A 100/100/NaN/120，首日买入 → 净值 1/1/1/1.2（零费）。"""
    dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"])
    close = _close_matrix({"A": [100.0, 100.0, np.nan, 120.0]}, dates)
    tw = _targets({dates[0]: {"A": 1.0}}, ["A"])
    result = execute_backtest(close, tw, ExecutionConfig(transaction_cost_bps=0.0))

    assert result.equity_curve.tolist() == pytest.approx([1.0, 1.0, 1.0, 1.2])
    # 缺价日按最后有效价估值；恢复报价后计入 20% 变化
    assert result.daily_returns.loc[dates[3]] == pytest.approx(0.2)


def test_cancel_entire_order_missing_price() -> None:
    """取消整次：第二次目标 A 换 B，执行日 B 缺价 → A 不卖、无成交记录、费用 0。"""
    dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"])
    close = _close_matrix(
        {"A": [100.0, 100.0, 100.0, 100.0], "B": [100.0, 100.0, 100.0, np.nan]},
        dates,
    )
    tw = _targets({dates[0]: {"A": 1.0}, dates[2]: {"B": 1.0}}, ["A", "B"])
    result = execute_backtest(close, tw, ExecutionConfig(transaction_cost_bps=0.0))

    # 仅首日建仓一笔成交
    assert len(result.trades) == 1
    assert result.trades.iloc[0]["sec"] == "A"
    assert result.fees.sum() == 0.0
    assert result.fees.loc[dates[3]] == 0.0
    # A 未卖出：day4 仍持有 A，净值按 A 估值
    assert result.holdings.loc[dates[3], "A"] == pytest.approx(result.holdings.loc[dates[1], "A"])
    assert result.holdings.loc[dates[3], "B"] == 0.0
    assert result.equity_curve.loc[dates[3]] == pytest.approx(1.0)
    cancel = [e for e in result.execution_log if e["status"] == "cancelled_missing_price"]
    assert len(cancel) == 1
    assert cancel[0]["missing_codes"] == ["B"]
    assert cancel[0]["execution_date"] == dates[3]


def test_no_retry_after_recovery_without_new_decision() -> None:
    """恢复后不重试：上例后 B 恢复但无新决策 → 仍持 A，无新增成交。"""
    dates = pd.to_datetime(
        ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
    )
    close = _close_matrix(
        {"A": [100.0] * 5, "B": [100.0, 100.0, 100.0, np.nan, 100.0]},
        dates,
    )
    tw = _targets({dates[0]: {"A": 1.0}, dates[2]: {"B": 1.0}}, ["A", "B"])
    result = execute_backtest(close, tw, ExecutionConfig(transaction_cost_bps=0.0))

    assert len(result.trades) == 1  # 只有首日建仓
    assert result.holdings.loc[dates[4], "A"] > 0
    assert result.holdings.loc[dates[4], "B"] == 0.0
    assert result.equity_curve.loc[dates[4]] == pytest.approx(1.0)
    statuses = [e["status"] for e in result.execution_log]
    assert statuses == ["executed", "cancelled_missing_price"]


def test_calendar_preserved_on_missing_factor_day() -> None:
    """缺因子日保留日历：close 日期齐全、scores 某决策日全 NaN → 不压缩日期。"""
    dates = pd.date_range("2026-01-05", periods=6, freq="D")
    close = _close_matrix({"A": [100.0] * 6, "B": [100.0] * 6}, dates)
    scores = pd.DataFrame(
        {"A": [1.0, 1.0, 1.0, 1.0, 1.0, np.nan], "B": [0.0, 0.0, 0.0, 0.0, 0.0, np.nan]},
        index=dates,
    )
    result = run_backtest(
        _long(close),
        scores,
        BacktestConfig(rebalance_freq="5d", top_n=1, max_weight=1.0, transaction_cost_bps=0.0),
    )

    # 5d 决策日 = d0 与 d5；d5 全 NaN 被跳过但日期保留在 decision_log
    assert len(result.decision_log) == 2
    assert result.decision_log[0]["status"] == "target_created"
    assert result.decision_log[1]["status"] == "skipped_insufficient"
    assert result.decision_log[1]["eligible_count"] == 0
    assert result.decision_log[1]["decision_date"] == dates[5]
    # 目标行只有 d0，执行在 d1；末日不再产生 unexecuted_end（未生成目标）
    assert list(result.target_weights.index) == [dates[0]]
    assert result.rebalance_dates.tolist() == [dates[0], dates[5]]
    assert all(e["status"] != "unexecuted_end" for e in result.execution_log)
    assert len(result.trades) == 1
    assert result.equity_curve.tolist() == pytest.approx([1.0] * 6)


def test_full_switch_with_fee_matches_b1_formula() -> None:
    """全仓换仓费用：A/B 平价、非零费 → 符合 B1 全仓换公式。"""
    dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"])
    close = _close_matrix({"A": [100.0] * 4, "B": [100.0] * 4}, dates)
    tw = _targets({dates[0]: {"A": 1.0}, dates[2]: {"B": 1.0}}, ["A", "B"])
    rate = 0.001
    result = execute_backtest(close, tw, ExecutionConfig(transaction_cost_bps=10000 * rate))

    c = rate
    # 建仓：after1 = 1/(1+c)
    after1 = 1.0 / (1.0 + c)
    assert result.equity_curve.loc[dates[1]] == pytest.approx(after1, rel=1e-10)
    # 换仓前净值 V = after1（价格平价）；换仓后 after2 = V(1-c)/(1+c)
    v = after1
    after2 = v * (1.0 - c) / (1.0 + c)
    assert result.equity_curve.loc[dates[3]] == pytest.approx(after2, rel=1e-10)
    # 换仓费用 = c(V + after2)
    assert result.fees.loc[dates[3]] == pytest.approx(c * (v + after2), rel=1e-10)
    # 换手 = 买卖金额合计 / 成交前净值 = (V + after2)/V（不除 2）
    assert result.turnover.loc[dates[3]] == pytest.approx((v + after2) / v, rel=1e-10)
    # costs 口径 = 费用 / 成交前净值
    assert result.costs.loc[dates[3]] == pytest.approx(c * (v + after2) / v, rel=1e-10)


def test_all_zero_row_liquidates() -> None:
    """全零行=明确清仓，与缺行（无新订单）区分。"""
    dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"])
    close = _close_matrix({"A": [100.0] * 4}, dates)
    tw = _targets({dates[0]: {"A": 1.0}, dates[2]: {}}, ["A"])
    result = execute_backtest(close, tw, ExecutionConfig(transaction_cost_bps=0.0))

    assert len(result.trades) == 2
    assert result.trades.iloc[1]["side"] == "sell"
    assert result.holdings.loc[dates[3], "A"] == 0.0
    assert result.cash.loc[dates[3]] == pytest.approx(1.0)
    assert result.weights.loc[dates[3], "A"] == 0.0


def test_missing_row_means_no_order() -> None:
    """缺行=没有新订单：持仓跨期保持，份额不变。"""
    dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
    close = _close_matrix({"A": [100.0, 100.0, 120.0]}, dates)
    tw = _targets({dates[0]: {"A": 1.0}}, ["A"])
    result = execute_backtest(close, tw, ExecutionConfig(transaction_cost_bps=0.0))

    assert len(result.execution_log) == 1  # 仅建仓一次
    assert result.holdings.loc[dates[2], "A"] == pytest.approx(result.holdings.loc[dates[1], "A"])
    assert result.equity_curve.loc[dates[2]] == pytest.approx(1.2)


def test_invalid_target_rows_rejected() -> None:
    """部分 NaN / 权重和真实超配 / 决策日不在 close 日期内 / 重复决策日 → 报错。"""
    dates = pd.to_datetime(["2026-01-05", "2026-01-06"])
    close = _close_matrix({"A": [100.0, 100.0], "B": [100.0, 100.0]}, dates)

    # 部分 NaN 非法
    tw = _targets({dates[0]: {"A": 1.0}}, ["A", "B"])
    tw.iloc[0, 1] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        execute_backtest(close, tw)

    # 真实超配（> 1 + 1e-12）
    tw2 = _targets({dates[0]: {"A": 0.6, "B": 0.5}}, ["A", "B"])
    with pytest.raises(ValueError, match="超过 1"):
        execute_backtest(close, tw2)

    # 决策日不在 close 日期内
    outside = pd.to_datetime(["2026-01-07"])
    tw3 = _targets({outside[0]: {"A": 1.0}}, ["A", "B"])
    with pytest.raises(ValueError, match="不在 close 日期内"):
        execute_backtest(close, tw3)

    # 重复决策日
    dup = pd.concat([_targets({dates[0]: {"A": 1.0}}, ["A", "B"])] * 2)
    with pytest.raises(ValueError, match="重复决策日"):
        execute_backtest(close, dup)

    # 容差内浮点超额：按原比例归一到 1，不报错
    tw4 = _targets({dates[0]: {"A": 0.5, "B": 0.5}}, ["A", "B"])
    tw4.iloc[0, 0] = 0.5 + 5e-13
    result = execute_backtest(close, tw4, ExecutionConfig(transaction_cost_bps=0.0))
    assert result.equity_curve.iloc[-1] == pytest.approx(1.0)


def test_invalid_close_rejected() -> None:
    """重复日期 / 重复证券 / 非递增索引 → 报错，不能 groupby 取平均掩盖。"""
    dates = pd.to_datetime(["2026-01-05", "2026-01-06"])
    good = _close_matrix({"A": [100.0, 100.0]}, dates)
    tw = _targets({dates[0]: {"A": 1.0}}, ["A"])

    dup_idx = pd.concat([good, good.iloc[[0]]]).sort_index()
    with pytest.raises(ValueError, match="重复日期"):
        execute_backtest(dup_idx, tw)

    dup_col = pd.DataFrame(
        [[100.0, 100.0], [100.0, 100.0]], index=dates, columns=["A", "A"]
    )
    with pytest.raises(ValueError, match="重复证券列"):
        execute_backtest(dup_col, tw)

    shuffled = good.iloc[::-1]
    with pytest.raises(ValueError, match="递增"):
        execute_backtest(shuffled, tw)


# ── 自 test_bt_engine.py 迁移的有效断言（旧 bt 引擎已删除，语义由统一账本承接）──

def _random_close(seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", "2024-06-30")
    secs = ["A", "B", "C", "D"]
    return pd.DataFrame(
        np.cumprod(1 + rng.normal(0, 0.01, (len(dates), len(secs))), axis=0),
        index=dates,
        columns=secs,
    )


def _changing_scores(close: pd.DataFrame) -> pd.DataFrame:
    """每期轮换偏好资产（1.0 对其余 0.0），产生周期性调仓。"""
    scores = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for i, date in enumerate(close.index):
        period = (i // 5) % 2
        top = close.columns[period * 2 : period * 2 + 2]
        scores.loc[date, top] = 1.0
    return scores


def test_rebalance_freq_invalid_rejected() -> None:
    """非法 rebalance_freq 必须报错（自 bt 引擎迁移）。"""
    close = _random_close()
    scores = _changing_scores(close)
    with pytest.raises(ValueError):
        run_backtest(
            _long(close),
            scores,
            BacktestConfig(rebalance_freq="daily", top_n=2, max_weight=1.0),
        )


def test_weekly_monthly_and_5d_produce_different_nav() -> None:
    """不同调度触发不同净值（自 bt 引擎迁移：weekly/monthly/5d 差异）。"""
    close = _random_close()
    scores = _changing_scores(close)

    def _run(freq: str) -> pd.Series:
        result = run_backtest(
            _long(close),
            scores,
            BacktestConfig(rebalance_freq=freq, top_n=2, max_weight=1.0, transaction_cost_bps=0.0),
        )
        return result.equity_curve

    weekly = _run("weekly")
    monthly = _run("monthly")
    five = _run("5d")
    assert not weekly.equals(monthly)
    assert not five.equals(monthly)


def test_no_lookahead_migrated_from_bt_engine() -> None:
    """T+1 执行无前视（自 bt 引擎迁移）：决策日 A 跳 1.1、次日回落 1.0，
    T 日收盘执行才会捕获 -9% 回落；T+1 执行必须为 0。"""
    dates = pd.bdate_range("2024-01-01", periods=10)
    close = pd.DataFrame(
        data={"A": [1.0] * 5 + [1.1] + [1.0] * 4, "B": [1.0] * 10},
        index=dates,
        columns=["A", "B"],
    )
    # 决策日 index0 选 B、index5 选 A，其余日期无分数（不产生目标）
    scores = pd.DataFrame(np.nan, index=dates, columns=["A", "B"])
    scores.iloc[0] = [0.0, 1.0]
    scores.iloc[5] = [1.0, 0.0]

    result = run_backtest(
        _long(close),
        scores,
        BacktestConfig(rebalance_freq="5d", top_n=1, max_weight=1.0, transaction_cost_bps=0.0),
    )
    assert result.daily_returns.loc[dates[6]] == pytest.approx(0.0)
