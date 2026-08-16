# 消除回测前视偏差 + 最新持仓建议表格

日期：2026-08-16
状态：已确认（设计）

## 背景与问题

### 前视偏差：两个回测引擎信号价 = 成交价

用户指出 core 与 bt 两个回测引擎存在**相同的前视偏差**，实测确认：

- **core/backtest/engine.py**：调仓日 T 用 `close[T]` 算因子→出权重，`weights.shift(1)` 后乘 `daily_asset_returns`。`gross_returns[T+1] = Σ weights[T] × (close[T+1]/close[T] - 1)`，等价于**以 T 日收盘价 `close[T]` 成交**。因子用 `close[T]`、成交价也是 `close[T]`，信号价 = 成交价。
  - 实测证据（V 形价格：A 在调仓日跳 +10%、次日回落）：day6 收益 **-9.09%**，吃到了决策日到次日的 gap → 前视。
- **research/bt_engine.py**：`WeighTarget` + `Rebalance` 在触发日 T 以 `close[T]` 成交，同样信号价 = 成交价。

> 关键辨析：`shift(1)` 只处理了「收益归属滞后」（避免同日自算），**没有消除前视**——实盘在 T 日收盘后才能算信号，无法再以 T 日收盘价成交，回测系统性虚高（多吃一拍隔夜收益）。

### 最新持仓展示缺口

策略运行页"最新持仓权重"目前：
- 取回测持仓矩阵最后一个日期（其实 = 数据库最新行情日）渲染柱状图；
- 只显示权重，无代码、无中文名称；
- 无标题说明生成日期。

## 设计

### A. 回测引擎：T 日算持仓，T+1 收盘价成交（消除前视）

**core/backtest/engine.py**
- `weights.shift(1)` → `weights.shift(2)`。
- 决策日 T 的权重以 **T+1 收盘价**为持有起点（收益从 T+2 起算），不捕获 T→T+1 的价格 gap。
- 实测：同一 V 形场景下 `shift(2)` 的 day6 收益 = 0（无前视）。

**research/bt_engine.py**
- 决策日 T 算权重，用 `bt.algos.RunOnDate(D+1交易日列表)` 触发成交，成交价 = T+1 收盘价。
- 决策日序列仍由 `rebalance_freq` 决定（复用 `generate_rebalance_dates`）；成交日 = 决策日的下一个交易日（在 close.index 中取）。
- `target_weights` 内部 `ffill().fillna(0.0)` 后喂给 `WeighTarget`，保证成交日能读到决策日的权重。
- 已用 bt 实测验证：决策日跳变次日回落不被捕获，无前视。

### B. RankIC 前瞻收益起点：T → T+1（口径一致）

**core/analysis/ic.py `calculate_forward_returns`**
- 现值：`forward_returns = close.shift(-horizon) / close - 1`，从 T 日收盘起算。
- 改为从 **T+1 起算**：`close.shift(1 - horizon) / close.shift(1) - 1`（即 `close[T+1+horizon]/close[T+1] - 1`），落点与回测 T+1 成交口径一致。
- 若不改，RankIC 会把策略实际吃不到的 T→T+1 gap 计入因子评价，与回测结果错位。
- 影响面：dashboard（`RANK_IC_HORIZON=5`）、factor_service（`horizon=5`）、research（`forward_return_horizon=5`）均复用此函数，一处改动全局生效。

### C. 最新持仓建议：独立基于数据库最新日 T 重算

**webapp/services/strategy_service.py**（`_run_faa`/`_run_eaa`）
- 复用已算好的 `composite`（因子合成得分矩阵），取**最后一行**（= 数据库最新交易日 T，`composite.index[-1]`）的截面因子得分。
- 用对应优化器算推荐持仓：
  - FAA：`EqualWeightOptimizer(top_n, max_weight, min_weight).optimize(score_row)`
  - EAA：`ScoreWeightedOptimizer(top_n, max_weight, min_weight).optimize(score_row)`
- 返回新增字段：
  - `latest_weights`: `{sec_code: weight}`（仅保留持仓 > 0 的标的）
  - `latest_data_date`: `composite.index[-1]` 的日期字符串（T 日）
- 语义：T 日最新因子算的持仓，假设 **T+1 收盘价调仓**。

### D. 前端表格渲染 + 标题

**webapp/static/js/pages/strategies.js**（`renderRunSummary`）
- 用 `latest_weights` 渲染**表格**，替换原"最新持仓权重"柱状图。
- 列：**代码**（sec_code）、**中文名称**（sec_name）、**持仓比例**（weight）。
- 中文名称来源：`API.getUniverse()` 建立 `sec_code → sec_name` 映射（运行前/渲染时拉取一次）。
- 按权重**降序**排列；权重格式化为百分比。
- 表格标题："**持仓建议生成日期：截至 {latest_data_date}**"。

**webapp/static/js/api.js**
- 无需新增接口（复用 `getUniverse`）。

## 测试

- **core 引擎无前视**：V 形价格场景（调仓日跳变、次日回落），断言收益不捕获决策日到次日的 gap。
- **bt 引擎无前视**：同上场景，断言无前视收益。
- **RankIC 起点**：`calculate_forward_returns` 从 T+1 起算的断言（更新 `test_analysis_ic.py`）。
- **最新推荐计算**：`_run_faa`/`_run_eaa` 返回 `latest_weights`/`latest_data_date`，且等于 composite 最后一行优化结果。
- **前端**：用 Node DOM stub 验证表格渲染字段（代码/名称/比例）与标题日期。

## 影响面

- `core/backtest/engine.py`、`core/analysis/ic.py`、`research/bt_engine.py`、`webapp/services/strategy_service.py`、`webapp/static/js/pages/strategies.js`
- 既有回测/IC 测试需同步更新断言（`test_backtest.py`、`test_analysis_ic.py`、`test_bt_engine.py`、`test_synthesis.py` 等）。
- 回测结果数值会因消除前视而变化（更保守、更接近实盘）。