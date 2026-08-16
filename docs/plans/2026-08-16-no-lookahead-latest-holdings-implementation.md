# 消除回测前视偏差 + 最新持仓建议表格 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 消除两个回测引擎的前视偏差（T+1 成交）、同步调整 RankIC 前瞻起点，并把策略运行页"最新持仓权重"改为基于数据库最新日 T 的持仓建议表格。

**Architecture:** ① core 引擎 `shift(1)→shift(2)` 与 bt 引擎 `RunOnDate(决策日+1)` 让成交价 = 决策日次日收盘价；② `calculate_forward_returns` 前瞻起点 T→T+1 与回测口径对齐；③ 后端在 `_run_faa`/`_run_eaa` 复用 `composite` 最后一行重算 `latest_weights`/`latest_data_date`；④ 前端用 `latest_weights` 渲染表格（代码/中文名/比例）+ 标题日期。

**Tech Stack:** Python 3.12 / pandas / SQLAlchemy / bt 1.2.0 / FastAPI / 原生 JS（无前端测试框架，用 Node DOM stub 临时验证）。

**设计文档:** `docs/plans/2026-08-16-no-lookahead-latest-holdings-design.md`

---

### Task 1: core 引擎消除前视（shift(1)→shift(2)）

**Files:**
- Modify: `core/backtest/engine.py`（`shifted_weights = weights.shift(1)` 行）
- Test: `tests/test_backtest.py`

**Step 1: 新增无前视测试**（V 形：调仓日跳变、次日回落，断言 Gap 不被捕获）

```python
def test_run_backtest_no_lookahead_on_rebalance_day() -> None:
    """V-shape: asset jumps on rebalance date, pulls back next day.
    T+1 execution must NOT capture the rebalance-day-to-next-day gap."""
    dates = pd.date_range("2026-01-05", periods=5, freq="D")
    # A: 1,1,1,1.1,1.0  — jumps to 1.1 on 01-08 (rebalance day), pulls back 01-09
    price_data = pd.DataFrame(
        {
            "date": list(dates) * 2,
            "sec": ["A.SH"] * 5 + ["B.SH"] * 5,
            "open": [1, 1, 1, 1.1, 1.0, 0.5, 0.5, 0.5, 0.5, 0.5],
            "high": [1, 1, 1, 1.1, 1.0, 0.5, 0.5, 0.5, 0.5, 0.5],
            "low": [1, 1, 1, 1.1, 1.0, 0.5, 0.5, 0.5, 0.5, 0.5],
            "close": [1, 1, 1, 1.1, 1.0, 0.5, 0.5, 0.5, 0.5, 0.5],
            "volume": [100] * 10,
            "amount": [100] * 10,
        }
    )
    # Factor prefers B until 01-08, then prefers A (so A is picked on the rebalance day).
    factor_scores = pd.DataFrame(
        [[0.0, 1.0]] * 3 + [[1.0, 0.0]] * 2,
        index=pd.DatetimeIndex(dates, name="date"),
        columns=["A.SH", "B.SH"],
    )
    result = run_backtest(
        price_data,
        factor_scores,
        BacktestConfig(top_n=1, max_weight=1.0, transaction_cost_bps=0),
    )
    # 01-09 A pulls back 1.1 -> 1.0 (-9.09%). If executed at decision-day close (lookahead),
    # this would be captured. T+1 execution must give 0 on 01-09.
    assert result.daily_returns.loc[pd.Timestamp("2026-01-09")] == pytest.approx(0.0)
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_backtest.py::test_run_backtest_no_lookahead_on_rebalance_day -v`
Expected: FAIL（当前 `shift(1)` 会捕获 gap，01-09 收益 ≈ -0.0909）

**Step 3: Implement**

`core/backtest/engine.py`:
```python
shifted_weights = weights.shift(2).fillna(0.0)
```

**Step 4: Update existing assertions**

`tests/test_backtest.py::test_run_backtest_minimal_deterministic_case`（A close 1..6，weekly 调仓日仅 01-05）：
决策 01-05、成交 01-06、01-07 起持有 A。新断言：
```python
assert result.weights.loc[pd.Timestamp("2026-01-05"), "A.SH"] == pytest.approx(1.0)
assert result.daily_returns.loc[pd.Timestamp("2026-01-06")] == pytest.approx(0.0)  # 成交日无损益
assert result.equity_curve.iloc[-1] == pytest.approx(3.0)  # 只有 01-07 起持有 A
```
`test_run_backtest_charges_turnover_cost`：调仓成本仍在决策日 01-05 计提，`turnover[01-05]==1.0`、`costs[01-05]==0.001` 不变；`equity_curve.iloc[0]` 断言改为基于新收益序列重算（实现时运行后确认）。
`test_run_backtest_clears_sold_positions_on_rebalance`：`weights` 断言不变（target_weights 未变）；如需校准 `daily_returns` 相关断言，运行后更新。

**Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_backtest.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add core/backtest/engine.py tests/test_backtest.py
git commit -m "fix(backtest): core 引擎 T+1 成交消除前视（shift 1->2）"
```

---

### Task 2: bt 引擎消除前视（RunOnDate 成交日触发）

**Files:**
- Modify: `research/bt_engine.py`
- Modify: `research/backtest.py`（如需重导出）
- Test: `tests/test_bt_engine.py`

**Step 1: 新增无前视测试**

```python
def test_no_lookahead_on_rebalance_day() -> None:
    dates = pd.bdate_range("2024-01-01", periods=6)
    secs = ["A", "B"]
    close = pd.DataFrame(
        data={"A": [1.0, 1.0, 1.0, 1.1, 1.0, 1.0], "B": [1.0] * 6},
        index=dates, columns=secs,
    )
    # A jumped to 1.1 on date index 3 (rebalance date), pulled back on index 4.
    tw = pd.DataFrame(float("nan"), index=dates, columns=secs)
    tw.loc[dates[0], "B"] = 1.0
    tw.loc[dates[3], "A"] = 1.0
    tw = tw.ffill().fillna(0.0)

    res = run_bt_backtest(close, tw, rebalance_freq="5d")
    # index 4 (A 1.1 -> 1.0) must NOT be captured -> daily return 0.
    assert res.daily_returns.loc[dates[4]] == pytest.approx(0.0)
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_bt_engine.py::test_no_lookahead_on_rebalance_day -v`
Expected: FAIL（当前每天 `RunEveryNPeriods` 决策日收盘成交，会捕获）

**Step 3: Implement**

`research/bt_engine.py`：用决策日序列计算成交日，改用 `RunOnDate` 触发：
```python
from core.calendar import generate_rebalance_dates

def _execution_dates(close_index, rebalance_freq: str) -> list:
    """Decision dates shifted forward one trading day = execution dates."""
    decision = generate_rebalance_dates(
        trading_dates=pd.DatetimeIndex(close_index),
        rebalance_freq=rebalance_freq,
        rebalance_day=0,
    )
    pos = close_index.get_indexer(decision)
    exec_idx = [p + 1 for p in pos if p + 1 < len(close_index)]
    return [close_index[p] for p in exec_idx]
```
在 `run_bt_backtest` 中：
```python
filled = target_weights.ffill().fillna(0.0).astype(float)
trigger = bt.algos.RunOnDate(*_execution_dates(close.index, rebalance_freq))
strategy = bt.Strategy(
    name,
    algos=[
        trigger,
        bt.algos.SelectAll(),
        bt.algos.WeighTarget(filled),
        bt.algos.Rebalance(),
    ],
)
```
保留 `rebalance_freq` 校验（`not in {"weekly","monthly","5d"}`）。
`target_weights` 需为逐日矩阵（调用方已 ffill）；若传入只在决策日有值，内部 `ffill` 兜底。

**Step 4: 更新既有 bt 测试**

`test_bt_engine.py` 的 `_changing_weights` 已逐日填值，`test_run_bt_backtest_returns_expected` 的 `result.weights.shape == close.shape` 等断言不变；`test_*_produce_different_nav` 不依赖成交时点，仍应通过。运行后如个别断言受影响再校准。

**Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_bt_engine.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add research/bt_engine.py tests/test_bt_engine.py
git commit -m "fix(backtest): bt 引擎 T+1 成交消除前视（RunOnDate 次日触发）"
```

---

### Task 3: RankIC 前瞻收益起点 T→T+1

**Files:**
- Modify: `core/analysis/ic.py:13-27`
- Test: `tests/test_analysis_ic.py`

**Step 1: 更新测试断言**

`tests/test_analysis_ic.py::test_calculate_forward_returns`（sample: A close 10,11,12,13；B 20,19,18,17；C 30,30,30,30；horizon=2）：
起点 T+1 → `close[T+1+2]/close[T+1]-1`：
```python
A_ret = returns.loc[pd.Timestamp("2026-01-01"), "A.SH"]
B_ret = returns.loc[pd.Timestamp("2026-01-01"), "B.SH"]
C_ret = returns.loc[pd.Timestamp("2026-01-01"), "C.SH"]
assert A_ret == pytest.approx(13 / 11 - 1)   # 0.1818
assert B_ret == pytest.approx(17 / 19 - 1)   # -0.1053
assert C_ret == pytest.approx(0.0)
assert np.isnan(returns.loc[pd.Timestamp("2026-01-03"), "A.SH"])
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_analysis_ic.py::test_calculate_forward_returns -v`
Expected: FAIL（当前从 T 起算，A=0.2、B=-0.1）

**Step 3: Implement**

`core/analysis/ic.py`:
```python
close = pivot_price_field(price_data, field=price_field, universe=universe)
# Forward return from execution day (T+1) to T+1+horizon, aligned with T+1 backtest.
forward_returns = close.shift(1 - horizon) / close.shift(1) - 1.0
```

**Step 4: Run full IC test suite to find other affected assertions**

Run: `python -m pytest tests/test_analysis_ic.py tests/test_synthesis.py tests/test_backtest.py -v`
Expected: 更新后 PASS；若其他测试断言了绝对前瞻值，按 T+1 口径校准。

**Step 5: Commit**

```bash
git add core/analysis/ic.py tests/test_analysis_ic.py
git commit -m "fix(analysis): RankIC 前瞻收益起点对齐 T+1 成交口径"
```

---

### Task 4: 最新持仓建议（后端独立重算）

**Files:**
- Modify: `webapp/services/strategy_service.py`（`_run_faa`/`_run_eaa`、`_execute_run`、`_result_to_dict`）
- Modify: `webapp/schemas/strategy.py`（如需，`StrategyRunSummary` 可不改，result 是 dict）
- Test: `tests/test_strategy_service.py`（新建或扩充）

**Step 1: 写失败测试**

```python
def test_run_faa_returns_latest_holdings(faa_args):
    result, violations, latest_weights, latest_date = _run_faa(*faa_args)
    assert latest_date is not None
    assert isinstance(latest_weights, dict)
    assert all(w > 0 for w in latest_weights.values())
    # weights sum to 1 for fully invested top-N
    assert sum(latest_weights.values()) == pytest.approx(1.0, abs=1e-6)
```
（`faa_args` fixture 用最小 price_data + 简单 scores 构造，确保 `composite` 最后一行有足够因子得分。）

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_strategy_service.py -v`
Expected: FAIL（当前返回 2 元组，解包报错）

**Step 3: Implement**

`_run_faa`：
```python
def _run_faa(...) -> tuple[BacktestResult, list[ConstraintViolationItem], dict[str, float], str]:
    ...
    composite = faa_composite(category_scores, class_weights)
    latest_weights, latest_date = _latest_recommendation(
        composite, EqualWeightOptimizer(top_n=top_n, max_weight=1.0, min_weight=0.0)
    )
    run_result = run_backtest(...)
    ...
    return run_result, violations, latest_weights, latest_date
```
`_run_eaa` 同理，用 `ScoreWeightedOptimizer`。

新增辅助函数：
```python
def _latest_recommendation(
    composite: pd.DataFrame, optimizer
) -> tuple[dict[str, float], str]:
    """Top-N holdings from the most recent factor date (T)."""
    latest = composite.index[-1]
    score_row = composite.loc[latest].dropna()
    weights = optimizer.optimize(score_row)
    holdings = {k: float(v) for k, v in weights.items() if v > 0}
    return holdings, str(pd.Timestamp(latest).date())
```
`_execute_run` 调用处：
```python
result, violations, latest_weights, latest_date = _run_faa(...)
run.result_summary = _result_to_dict(result, violations, latest_weights, latest_date)
```
`_result_to_dict` 增加参数并写入：
```python
def _result_to_dict(result, violations, latest_weights=None, latest_data_date=None):
    ...
    "latest_weights": latest_weights or {},
    "latest_data_date": latest_data_date,
```

**Step 4: Run tests**

Run: `python -m pytest tests/test_strategy_service.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add webapp/services/strategy_service.py tests/test_strategy_service.py
git commit -m "feat(strategy): 独立基于数据库最新日 T 重算最新持仓建议"
```

---

### Task 5: 前端持仓建议表格渲染

**Files:**
- Modify: `webapp/static/js/pages/strategies.js`（`runStrategy`、`renderRunSummary`、`renderWeightsChart`）

**说明：** 项目无 JS 测试框架，采用 Node DOM stub 临时脚本验证（不入库），与之前策略页验证一致。

**Step 1: 修改 `runStrategy`**，把 `latest_weights`/`latest_data_date` 透传给 summary：
```js
const rs = detail.result_summary || {};
const summary = {
    status: detail.status,
    error_msg: detail.error_msg,
    metrics: rs.metrics || {},
    nav_series: rs.equity_curve || {},
    latest_weights: rs.latest_weights || {},
    latest_data_date: rs.latest_data_date || null,
    constraint_violations: rs.constraint_violations || [],
};
```

**Step 2: 修改 `renderRunSummary`**，把"最新持仓权重"col-6 区块改为表格，标题带生成日期：
```js
const latestDate = summary.latest_data_date
    ? `持仓建议生成日期：截至 ${summary.latest_data_date}`
    : "最新持仓建议";
// 右侧 col-6 区块：
<div class="col-6" style="grid-column: span 6;">
    <div class="card-title card-title-sm">${Utils.escapeHtml(latestDate)}</div>
    <div id="latest-holdings" class="holdings-table-wrap"></div>
</div>
```
删除 `renderWeightsChart(summary.weights)` 调用，改为 `renderLatestHoldings(summary.latest_weights)`。

**Step 3: 新增 `renderLatestHoldings`**（表格 + 中文名映射）：
```js
let universeNameMap = null;
async function ensureUniverseNameMap() {
    if (universeNameMap) return universeNameMap;
    const list = await API.getUniverse().catch(() => []);
    universeNameMap = {};
    list.forEach((u) => { universeNameMap[u.sec_code] = u.sec_name; });
    return universeNameMap;
}
async function renderLatestHoldings(weights) {
    const el = document.getElementById("latest-holdings");
    if (!el) return;
    const nameMap = await ensureUniverseNameMap();
    const rows = Object.entries(weights)
        .map(([code, w]) => ({ code, name: nameMap[code] || code, w }))
        .sort((a, b) => b.w - a.w);
    if (!rows.length) {
        el.innerHTML = `<div class="empty-state"><div class="empty-title">暂无持仓</div></div>`;
        return;
    }
    el.innerHTML = `
        <div class="table-wrap">
            <table class="table">
                <thead><tr><th>代码</th><th>名称</th><th>持仓比例</th></tr></thead>
                <tbody>${rows.map((r) => `
                    <tr>
                        <td class="mono">${Utils.escapeHtml(r.code)}</td>
                        <td>${Utils.escapeHtml(r.name)}</td>
                        <td>${Utils.formatPct(r.w)}</td>
                    </tr>`).join("")}
                </tbody>
            </table>
        </div>`;
    el.innerHTML = `<div class="holdings-table-wrap">${el.innerHTML}</div>`;
}
```
`renderRunSummary` 改为 `async` 并在末尾 `await renderLatestHoldings(...)`。

**Step 4: Node DOM stub 验证**（临时脚本，校验表格含代码/名称/比例、标题含日期，逻辑同策略页修复时的验证方式）。

**Step 5: 语法检查**

Run Windows: `node --check webapp/static/js/pages/strategies.js`
Expected: 无输出（退出码 0）

**Step 6: Commit**

```bash
git add webapp/static/js/pages/strategies.js
git commit -m "feat(webapp): 最新持仓改为表格并展示代码/中文名/比例及生成日期"
```

---

### Task 6: 全量回归

**Step 1: 运行全量测试**

Run: `python -m pytest -q`
Expected: 全部通过（受影响的 `test_backtest.py`、`test_analysis_ic.py`、`test_bt_engine.py`、`test_synthesis.py` 等已在前序 Task 更新）。

**Step 2: 后端 e2e 复核**

Run: `python -m pytest tests/test_webapp_e2e.py -v`
Expected: PASS（若 `test_e2e_run_strategy_and_view_records` 断言了 `result_summary` 结构，确认 `latest_weights`/`latest_data_date` 存在）。

**Step 3: 提交回归修正（如有）**

```bash
git add -A && git commit -m "test: 同步回测/IC 断言至 T+1 口径"   # 若 Summary 有剩余改动
```

---

## 验收清单

- [ ] core 引擎决策日跳变次日回落不被捕获（无前视）
- [ ] bt 引擎同上
- [ ] `calculate_forward_returns` 从 T+1 起算，RankIC 与回测口径一致
- [ ] 策略运行返回 `latest_weights` + `latest_data_date`
- [ ] 前端表格展示 代码/中文名称/持仓比例，标题"截至 {latest_data_date}"
- [ ] `python -m pytest -q` 全绿