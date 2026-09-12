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
| 因子 RankIC 排名 | 按分类分组展示各因子实例的 RankIC 均值/IR，右侧标注该分类等权得分的 RankIC |
| 最近策略运行 | 最近 10 次策略运行记录列表 |

### 🧮 因子看板

- 左侧因子列表**按分类分组**（动量 / 波动 / 反转 / 量能 / 其他），分组与实例统一由
  `core/factors/builtin/factors.yaml` 配置，勾选一个或多个因子实例后点击「计算因子」
- 点击某个因子显示详情卡片（类别、方向、公式、描述、实例、参数）
- 每个因子展示 IC / RankIC / ICIR 概览卡片
- IC / RankIC 时序图
- 五分组年化收益柱状图
- 相关性热力图支持**按实例**（因子间）与**按类**（分类得分间）两种粒度切换

### 🚀 策略运行

支持两种策略（因子按「分类」组织，分类由 `core/factors/builtin/factors.yaml` 配置，默认 5 类：动量 / 波动 / 反转 / 量能 / 其他）：

1. **FAA 策略**（`faa`）
   - 参数：Top N 持仓数量（3/5/7/9）、调仓频率（周/月）、因子类权重
   - 流程：类内因子等权合成类得分 → 类得分 min-max 归一化 → 类间按权重线性加权 → 选 Top N 等权回测

2. **EAA 策略**（`eaa`）
   - 参数：Top N 持仓数量、调仓频率、类缩放系数 α、整体缩放系数 β
   - 流程：类内因子等权合成类得分 → 类得分归一化后各自乘方 α 再连乘、整体乘方 β → 标准化得分 → 选 Top N 按得分占比加权回测

运行结果展示：
- 绩效卡片（总收益、年化收益、年化波动、夏普、最大回撤）
- 净值曲线图
- 最新持仓权重图
- 约束校验结果提示（回测末持仓会走一轮分类约束校验）

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

### 📊 宏观数据

- 日频 / 月频切换，查看宏观指标字段
- 多选字段查看走势图，支持日期范围筛选
- 数据表格展示
- 顶部「同步数据」按钮全量同步宏观指标（删除旧数据后重新拉取）

### ⚙️ 设置

- 查看当前数据源配置（主/备数据源、缓存开关、缓存天数）
- 查看系统信息（版本、数据库连接状态、数据库地址）
- **数据同步**：
  - 「同步行情数据」：按日期范围全量同步 ETF 行情（后复权 hfq），带进度条
  - 「同步宏观数据」：选择日频/月频后全量同步宏观指标，带进度条
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
| 行情 | `GET /api/market/etf/list`、`GET /api/market/etf/{sec_code}/kline`、`POST /api/market/sync/etf`、`GET /api/market/sync/{task_id}` |
| 宏观 | `GET /api/macro/fields`、`GET /api/macro/daily`、`GET /api/macro/monthly`、`POST /api/macro/sync`、`GET /api/macro/sync/{task_id}` |
| 首页 | `GET /api/dashboard/stats`、`/api/dashboard/etf-price`、`/api/dashboard/factor-ranking`、`/api/dashboard/recent-runs` |
| 设置 | `GET /api/settings` |

---

## 常见问题

### 1. 页面加载正常但图表空白？
行情数据依赖 `akshare`（腾讯 fqkline 后复权）网络数据源。首次使用需要联网拉取数据，
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