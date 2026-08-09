# 策略运行接口异步化设计

日期：2026-08-09
状态：待评审

## 1. 背景与目标

### 现状问题
`POST /api/strategies/run` 当前为**同步阻塞**执行：前端点"运行策略"后，请求在服务端串行完成「拉行情 → 算因子 → 合成 → 优化 → 回测 → 落库」才返回。当 `etf_daily_bar` 缓存 miss（需实时请求腾讯/baostock 外网）时，单次可能耗时数十秒，前端全程 spinner，且占满一个 uvicorn worker。

### 目标
1. `POST /run` 立即返回，不再阻塞请求线程。
2. 后台线程执行完整 pipeline，且**同一时间只允许一个策略任务**（限并发 1）。
3. 前端提交后**轮询**任务状态，完成后从落库结果渲染。
4. 策略运行产生的 `constraint_violations` 一并落库，前端可读取。

## 2. 现状梳理

### 后端
- `webapp/api/strategies.py`：`POST /run` → `run_strategy(db, req)` → 同步返回 `StrategyRunSummary`。
- `webapp/services/strategy_service.py`：
  - `run_strategy()`：创建 `StrategyRun(status="running")` → `_run_faa/_run_eaa` → 写 `result_summary` + 置 `success/failed` → `_to_summary()` 返回。
  - `_result_to_dict()`：`result_summary` 现存 `metrics / equity_curve / daily_returns / weights / turnover / costs / rebalance_dates`，**不含 `constraint_violations`**。
  - `_to_summary()`：返回 `StrategyRunSummary`（含 metrics / nav_series / weights / constraint_violations）——仅同步响应，不落库。
  - `_prune_history_runs()`：按 `max_history_runs` 清理旧记录。
- `strategy_runs.status` 字段：`pending / running / success / failed`（已存在，可直接复用为任务状态）。
- `webapp/models/database.py`：SQLite 已设 `check_same_thread=False`，支持后台线程共享 engine；`SessionLocal` 可新建独立 session。

### 前端
- `webapp/static/js/pages/strategies.js`：`runStrategy()` 等 `POST /run` 返回 summary → `renderRunSummary` + `renderConstraintCheck` + `loadRuns()`。

### 测试
- `tests/test_webapp_api_strategies.py`：`test_run_unknown_strategy_fails_gracefully` 期望 POST 对未知策略同步返回 `status="failed"`。
- `tests/test_webapp_e2e.py`：`test_e2e_run_strategy_and_view_records` 期望 POST 返回 `status="success"` 且含 `metrics/nav_series`，并从 `GET /runs/{id}` 的 `result_summary` 读 `metrics`。

## 3. 方案设计

### 3.1 后端

#### 3.1.1 快速校验保留同步失败路径
`POST /run` 在**请求线程**内先做低成本校验，命中则同步返回 `strategy_run_summary(status="failed")`，不启动后台：
- 未知策略类型。
- 标的池为空。

> 兼容现有 `test_run_unknown_strategy_fails_gracefully`。

#### 3.1.2 任务的创建与并发控制
校验通过后：
1. 加模块级 `threading.Lock`，查询是否存在 `status in ("pending", "running")` 的 run。
   - 存在 → 返回 `status="failed", error_msg="已有策略任务运行中，请稍候"`（拒绝新提交）。
   - 不存在 → 创建 `StrategyRun(status="pending", ...)`，提交，拿到 `run_id`。
2. 启动后台线程执行 pipeline，立即返回 `StrategyRunSummary(status="pending", run_id=..., metrics=None)`。

> 并发控制基于 DB 查询 + 模块锁，单进程（uvicorn 单 worker）下严格有效。

#### 3.1.3 后台执行器
新增 `webapp/services/strategy_runner.py`（或置于 `strategy_service` 内）：
- 后台线程入参：`run_id`。
- 线程内 `SessionLocal()` 新建独立 session（不共用请求线程 session）：
  1. 取 `StrategyRun` 记录，置 `status="running"`。
  2. 从 `run.params / strategy_type / start_date / end_date / universe_snapshot` 重新读取入参，执行原有 pipeline（`_run_faa/_run_eaa`）。
  3. 写 `result_summary`（含 `constraint_violations`），置 `status="success"`。
  4. 异常 → 置 `status="failed"` + `error_msg`。
  5. 关闭 session；调用 `_prune_history_runs`。
- 复用现有 `_run_faa / _run_eaa / _result_to_dict / _build_classifications / _load_core_constraints` 等纯计算逻辑（不涉及 session 的部分）。

#### 3.1.4 落库字段扩展
`_result_to_dict()` 增加 `constraint_violations`：
```jsonc
"constraint_violations": [
  {"constraint": "...", "message": "...", "severity": "error"}
]
```
当前 `_run_faa/_run_eaa` 已返回 `violations`，需由后台执行器传入 `_result_to_dict`。

#### 3.1.5 遗留 run 清理（可选增强）
进程启动时（`create_app` 或执行器初始化）把遗留 `status="pending/running"` 的 run 置为 `failed`（`error_msg="服务重启，任务中断"`），避免孤儿任务。

### 3.2 前端
`strategies.js` 的 `runStrategy()` 改为：
1. `POST /run`：
   - 返回 `status="failed"` → 直接渲染错误（不轮询）。
   - 返回 `status="pending"` → 进入轮询。
2. 轮询 `GET /strategies/runs/{run_id}`，间隔约 1s：
   - `status` 仍在 `pending/running` → 继续轮询，展示"运行中…"。
   - `status` 为 `success/failed` → 停止轮询。
3. 完成后从 `result_summary` 渲染：
   - 指标：`result_summary.metrics`
   - 净值：`result_summary.equity_curve`
   - 最新权重：`result_summary.weights`
   - 约束面板：`result_summary.constraint_violations`
4. 复用现有 `renderRunSummary` / `renderConstraintCheck`，改为从 `result_summary` 取数。

> 注意：`GET /runs/{id}` 返回的是 `StrategyRunDetail`（含 `result_summary` dict），与现状 `StrategyRunSummary` 结构不同，前端取值需走 `result_summary.*`。

### 3.3 测试
- `test_webapp_api_strategies.py`：`test_run_unknown_strategy_fails_gracefully` 保持（未知策略仍同步失败）。
- `test_webapp_e2e.py` `test_e2e_run_strategy_and_view_records`：改为 POST 后**轮询** `GET /runs/{id}` 直到非 pending，再从 `result_summary` 断言 `metrics`，并新增断言 `constraint_violations` 存在。
- 新增并发测试：提交一个任务后，立即第二个提交被拒（`status="failed"` + 提示）。
- 新增落库测试：成功 run 的 `result_summary` 含 `constraint_violations`、`metrics`。

## 4. 影响点与风险

| 项 | 说明 | 对策 |
|----|------|------|
| 后台线程 + SQLite | 依赖 `check_same_thread=False`（已配置）。 | 线程内新建 `SessionLocal`，不共享请求 session。 |
| 进程重启打断任务 | 遗留 pending/running 变孤儿。 | 启动时清理为 failed（3.1.5）。 |
| 测试时序 | 后台线程异步执行，TestClient 需等待完成。 | 测试内轮询等待；必要时用超时保护。 |
| uvicorn 多 worker | 模块锁仅单进程有效。 | 本项目单 worker 部署，可接受；如需多 worker 再引入 DB 级锁。 |
| 前端契约变化 | `POST /run` 不再返回 metrics/nav_series。 | 前端改轮询 + 从 `result_summary` 取数。 |

## 5. 验收标准
1. `POST /run` 校验通过时**立即返回**（不计 pipeline 耗时）。
2. 后台完成策略运行，`GET /runs/{id}` 的 `result_summary` 含 `metrics` 与 `constraint_violations`。
3. 运行中再次提交被拒（限并发 1）。
4. 未知策略 / 空标的池仍同步快速失败。
5. 全量测试通过（`pytest -q`）。

## 6. 涉及文件
- `webapp/api/strategies.py`（POST /run 改为提交 + 返回 pending）
- `webapp/services/strategy_service.py`（拆分"提交"与"执行"；`_result_to_dict` 加 violations；并发控制）
- `webapp/services/strategy_runner.py`（新增，后台执行器）
- `webapp/main.py`（启动时清理遗留 run，可选）
- `webapp/static/js/pages/strategies.js`（前端轮询 + 从 result_summary 渲染）
- `tests/test_webapp_api_strategies.py`、`tests/test_webapp_e2e.py`（测试调整 + 新增）