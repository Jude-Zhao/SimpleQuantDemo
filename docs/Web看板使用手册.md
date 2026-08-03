# SimpleQuant Web 看板使用手册

## 启动方式

在项目根目录运行：

```bash
python run_webapp.py
```

启动后访问 <http://localhost:8000> 即可打开 Web 看板。

首次运行时系统会自动初始化 SQLite 数据库（`data/simple_quant.db`）并预置
默认的 ETF 标的池。

---

## 页面功能说明

### 🏠 首页（Dashboard）

| 区域 | 说明 |
|------|------|
| 统计卡片 | 展示标的池数量、因子数量、今日策略运行次数、系统状态 |
| ETF 走势 | 下拉选择 ETF 查看收盘价走势折线图 |
| 因子 IC 排名 | 横向条形图展示各因子最近 IC 均值 |
| 最近策略运行 | 最近 10 次策略运行记录列表 |

### 🧮 因子看板

- 勾选一个或多个因子，选择调仓窗口（5/10/20 个交易日）后点击「计算因子」
- 每个因子展示 IC / RankIC / ICIR 概览卡片
- IC / RankIC 时序图
- 五分组年化收益柱状图

### 🚀 策略运行

支持三种策略：

1. **线性因子策略**（`linear_factor`）
   - 参数：因子多选、Top N 持仓数量、调仓频率（周/月）、单票最大/最小权重、调仓窗口
   - 流程：因子合成（ICIR 加权）→ Top N 等权选择 → 回测

2. **均值方差优化**（`mvo`）
   - 参数：优化目标（最小方差/最大夏普/目标收益）、单票权重上下限、协方差回看窗口
   - 流程：历史收益与协方差估计 → MVO 求解 → 回测

3. **Black-Litterman**（`bl`）
   - 参数：单票权重上下限、先验不确定性 tau、优化目标、观点列表（JSON）
   - 流程：市场均衡先验 + 观点 → 后验收益 → MVO → 回测

运行结果展示：
- 绩效卡片（总收益、年化收益、年化波动、夏普、最大回撤）
- 净值曲线图
- 最新持仓权重图
- 约束校验结果提示

历史运行记录列表支持查看详情与导出 NAV CSV。

### 📦 标的池

- 查看当前活跃标的列表（代码、名称、分类、状态）
- 从「可添加 ETF」多选框中批量添加标的
- 一键移除标的（软删除）
- 刷新标的池和可用列表

### 🏷️ 分类约束

- **分类规则管理**：列表展示现有规则，支持新增/编辑/删除
  - 规则类型：
    - `manual`：手动指定证券代码列表
    - `by_field`：按 meta 字段匹配
    - `by_range`：按数值字段区间匹配
  - 优先级数字越小越优先
- **应用分类**：执行全部启用规则，预览分类结果表
- **约束配置**：设置单票最小/最大权重、分类权重上下限（JSON 形式）

### ⚙️ 设置

- 查看当前数据源配置（主/备数据源、缓存开关、缓存天数）
- 查看系统信息（版本、数据库连接状态、数据库地址）
- 清空本地缓存

---

## API 文档

启动服务后访问 <http://localhost:8000/docs> 查看交互式 API 文档（Swagger UI）。

主要 API 接口：

| 模块 | 接口 |
|------|------|
| 健康检查 | `GET /api/health` |
| 因子 | `GET /api/factors`、`POST /api/factors/compute`、`POST /api/factors/correlation` |
| 标的池 | `GET/POST /api/universe`、`DELETE /api/universe/{sec_code}`、`GET /api/universe/available` |
| 分类 | `GET/POST/PUT/DELETE /api/classifications/rules`、`POST /api/classifications/apply` |
| 约束 | `GET/PUT /api/constraints` |
| 策略 | `GET /api/strategies`、`POST /api/strategies/run`、`GET /api/strategies/runs`、`GET /api/strategies/runs/{id}`、`GET /api/strategies/runs/{id}/export` |
| 行情 | `GET /api/market/etf/list`、`GET /api/market/etf/{sec_code}/kline` |
| 首页 | `GET /api/dashboard/stats`、`/api/dashboard/etf-price`、`/api/dashboard/factor-ranking`、`/api/dashboard/recent-runs` |
| 设置 | `GET /api/settings` |

---

## 常见问题

### 1. 页面加载正常但图表空白？
行情数据依赖 `akshare` / `baostock` 网络数据源。首次使用需要联网拉取数据，
数据会缓存到本地 SQLite。若数据源不可用，图表会显示「加载失败」提示。

### 2. 策略运行失败？
- 检查标的池是否有数据（首页统计卡片可查看数量）
- 检查数据源是否可访问（设置页可查看数据库连接状态）
- 检查因子是否存在（因子看板「计算因子」可验证）
- BL 策略需要提供有效的观点 JSON（资产代码必须在标的池内）

### 3. 如何修改数据库？
默认数据库文件为 `data/simple_quant.db`。可通过环境变量 `DATABASE_URL`
覆盖（见 `.env.example`）。

### 4. 运行全部测试？
```bash
pytest -v