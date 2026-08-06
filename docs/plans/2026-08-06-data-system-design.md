---

# 数据体系设计文档

**版本**: V1.0
**日期**: 2026-08-06
**状态**: 设计中

---

## 一、设计目标

1. **行情数据**：建立完整的 ETF 行情同步机制，支持手动全量同步，保证前复权数据准确性，数据覆盖范围从 `2021-01-04` 至最新交易日
2. **宏观数据**：建立标准化的宏观因子数据体系，覆盖利率、汇率、商品、估值、海外、货币、经济七大类共 20 个字段，为因子研究提供数据支撑
3. **统一同步框架**：行情 + 宏观共用一套同步架构，支持进度追踪、全量覆盖、错误重试
4. **前端交互**：提供手动同步按钮 + 实时进度展示，用户可一键刷新数据

---

## 二、行情数据设计

### 2.1 数据范围

| 项目 | 说明 |
|------|------|
| **标的范围** | 系统配置的 ETF 标的池（默认 10 只，可扩展至 28 只） |
| **时间范围** | 2021-01-04 ~ 最新交易日 |
| **数据频率** | 日线（daily）+ 分钟线（1m / 5m / 15m / 30m / 60m） |
| **复权方式** | 前复权 |

### 2.2 数据库表结构

现有表结构保持不变，沿用 `etf_daily_bar` 和 `etf_minute_bar`。

**etf_daily_bar（日线表）**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | String | 主键，格式：`{sec_code}_{trade_date}` |
| sec_code | String | 证券代码，如 `510300.SH` |
| trade_date | Date | 交易日期 |
| open | Float | 开盘价 |
| high | Float | 最高价 |
| low | Float | 最低价 |
| close | Float | 收盘价 |
| volume | Float | 成交量 |
| amount | Float | 成交额 |
| source | String | 数据来源（baostock / akshare） |

唯一索引：`(sec_code, trade_date)`

**etf_minute_bar（分钟线表）**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | String | 主键，格式：`{sec_code}_{yyyymmddHHMM}_{period}` |
| sec_code | String | 证券代码 |
| trade_datetime | DateTime | 交易时间 |
| period | String | 周期：1m / 5m / 15m / 30m / 60m |
| open / high / low / close | Float | OHLC |
| volume / amount | Float | 成交量 / 成交额 |
| source | String | 数据来源 |

唯一索引：`(sec_code, trade_datetime, period)`

### 2.3 同步策略

**采用全量覆盖式同步**：每次同步时，删除指定日期范围内的旧数据，重新拉取并写入新数据。

**为什么选全量覆盖：**
- ETF 数量少（10~28 只），全量拉取耗时可接受（预计 30~60 秒）
- 前复权数据会随分红/拆股调整而变化，增量更新无法保证历史数据正确性
- 实现简单，逻辑清晰，不易出错

**同步流程：**

```
开始同步
  │
  ├─► 确定同步参数：sec_codes, start_date, end_date, period
  │
  ├─► 1. 删除旧数据清理
  │     按 sec_code + 逐个删除指定日期范围内的记录
  │     （DELETE FROM etf_daily_bar WHERE sec_code=? AND trade_date BETWEEN ? AND ?）
  │
  ├─► 2. 从数据源拉取
  │     优先 Baostock（前复权稳定）
  │     失败则尝试 AkShare
  │
  ├─► 3. 写入新数据
  │     批量 INSERT
  │
  └─► 返回同步完成
```

**同步粒度：**
- 日线：按 ETF 逐只同步，每只完成后更新进度
- 分钟线：按需同步（默认不同步，仅在用户请求时缓存

---

## 三、宏观数据设计

### 3.1 字段清单（20 个）

#### 3.1.1 日频字段（14 个）

**利率类（4 个）**

| 标准字段名 | 中文名称 | 数据源 | 原始函数 | 原始字段映射 | 单位 | 起始日期 | 稳定性 |
|-----------|----------|--------|----------|-----------|------|----------|--------|
| shibor_3m | Shibor 3个月 | AkShare | `rate_interbank(market="上海银行同业拆借市场", symbol="Shibor人民币", indicator="3月") | 报告日 → date, 利率 → value | % | 2006-10-08 | 🟢 |
| fr007 | 银行间质押式回购利率 FR007 | AkShare | `repo_rate_hist(start_date, end_date)` | date → date, FR007 → value | % | ~2020 | 🟡 |
| cn_gov_1y | 1年期国债收益率 | AkShare | `bond_china_yield(start_date, end_date)` | 日期 → date, 1年 → value（过滤"曲线名称"="中债国债收益率曲线" | % | 2020-02-04 | 🟡 |
| cn_gov_10y | 10年期国债收益率 | AkShare | `bond_china_yield(start_date, end_date)` | 日期 → date, 10年 → value（过滤"曲线名称"="中债国债收益率曲线"） | % | 2020-02-04 | 🟡 |

> 注：`bond_china_yield 接口单次查询范围约 1 年，需分段拉取后拼接

**汇率类（1 个）**

| 标准字段名 | 中文名称 | 数据源 | 原始函数 | 原始字段映射 | 单位 | 起始日期 | 稳定性 |
|-----------|----------|--------|----------|-----------|------|----------|--------|
| usd_cny | 美元/人民币中间价 | AkShare | `currency_boc_sina(symbol="美元")` | 日期 → date, 央行中间价 → value | （百美元兑人民币 | ~2007 | 🟡 |

**大宗商品类（3 个）**

| 标准字段名 | 中文名称 | 数据源 | 原始函数 | 原始字段映射 | 单位 | 起始日期 | 稳定性 |
|-----------|----------|--------|----------|-----------|------|----------|--------|
| copper | 沪铜主力收盘价 | AkShare | `futures_main_sina(symbol="CU0") | 日期 → date, 收盘价 → value | 元/吨 | 2005-01-04 | 🟢 |
| gold | 沪金主力收盘价 | AkShare | `futures_main_sina(symbol="AU0") | 日期 → date, 收盘价 → value | 元/克 | 2008-01-09 | 🟢 |
| rebar | 螺纹钢主力收盘价 | AkShare | `futures_main_sina(symbol="RB0") | 日期 → date, 收盘价 → value | 元/吨 | 2009-03-27 | 🟢 |

**权益估值/波动率类（3 个）**

| 标准字段名 | 中文名称 | 数据源 | 原始函数 | 原始字段映射 | 单位 | 起始日期 | 稳定性 |
|-----------|----------|--------|----------|-----------|------|----------|--------|
| csi300_pe | 沪深300滚动市盈率 | AkShare | `stock_zh_index_hist_csindex(symbol="000300") | 日期 → date, 滚动市盈率 → value | 倍 | 2018-05-26 | 🟢 |
| csi1000_pe | 中证1000滚动市盈率 | AkShare | `stock_zh_index_hist_csindex(symbol="000852") | 日期 → date, 滚动市盈率 → value | 倍 | 2018-05-26 | 🟢 |
| qvix_300etf | 300ETF期权波动率指数 | AkShare | `index_option_300etf_qvix()` | date → date, close → value | 指数点 | 2015-02-09 | 🟡 |

**海外市场类（3 个）**

| 标准字段名 | 中文名称 | 数据源 | 原始函数 | 原始字段映射 | 单位 | 起始日期 | 稳定性 |
|-----------|----------|--------|----------|-----------|------|----------|--------|
| spx | 标普500指数 | AkShare | `index_us_stock_sina(symbol=".INX") | date → date, close → value | 指数点 | 2004-01-02 | 🟡 |
| ixic | 纳斯达克指数 | AkShare | `index_us_stock_sina(symbol=".IXIC") | date → date, close → value | 指数点 | 2004-01-02 | 🟡 |
| hsi | 恒生指数 | AkShare | `stock_hk_index_daily_sina(symbol="HSI") | date → date, close → value | 指数点 | 2013-08-20 | 🟡 |

#### 3.1.2 月频字段（6 个）

**货币/信用类（3 个，含 1 个派生）

| 标准字段名 | 中文名称 | 数据源 | 原始函数 | 原始字段映射 | 单位 | 起始日期 | 稳定性 |
|-----------|----------|--------|----------|-----------|------|----------|--------|
| m2_yoy | M2 同比 | Baostock | `query_money_supply_data_month | statYear+statMonth → date, m2YOY → value | % | ~1990s | 🟢 |
| m1_yoy | M1 同比 | Baostock | `query_money_supply_data_month | statYear+statMonth → date, m1YOY → value | % | ~1990s | 🟢 |
| m1_m2_scissors | M1-M2 剪刀差 | 派生 | m1_yoy - m2_yoy | - | 百分点 | - | - |

**宏观经济类（3 个）**

| 标准字段名 | 中文名称 | 数据源 | 原始函数 | 原始字段映射 | 单位 | 起始日期 | 稳定性 |
|-----------|----------|--------|----------|-----------|------|----------|--------|
| cpi_yoy | CPI 同比 | AkShare | `macro_china_cpi_yearly()` | 日期 → date, 今值 → value | % | 1986-02 | 🟢 |
| ppi_yoy | PPI 同比 | AkShare | `macro_china_ppi_yearly()` | 日期 → date, 今值 → value | % | 1995-08 | 🟢 |
| aggregate_financing | 社会融资规模增量 | AkShare | `macro_china_shrzgm()` | 月份 → date, 社会融资规模增量 → value | 亿元 | 2015-01 | 🟢 |

### 3.2 数据库表结构

新增一张宏观数据表，采用**宽表设计**，每行一个日期，每列一个字段。

**macro_daily（宏观日频宏观数据表）**

| 字段 | 类型 | 说明 |
|------|------|------|
| trade_date | Date | 日期（主键） |
| shibor_3m | Float | Shibor 3个月 |
| fr007 | Float | FR007 回购利率 |
| cn_gov_1y | Float | 1年期国债收益率 |
| cn_gov_10y | Float | 10年期国债收益率 |
| usd_cny | Float | 美元人民币汇率 |
| copper | Float | 沪铜主力收盘价 |
| gold | Float | 沪金主力收盘价 |
| rebar | Float | 螺纹钢主力收盘价 |
| csi300_pe | Float | 沪深300 PE |
| csi1000_pe | Float | 中证1000 PE |
| qvix_300etf | Float | 300ETF 波动率指数 |
| spx | Float | 标普500 |
| ixic | Float | 纳斯达克 |
| hsi | Float | 恒生指数 |

主键：`trade_date`

> 为什么用宽表：
- 宏观字段数量有限（14 个日频），不会频繁 JOIN 成本低
- 查询时一行取一行就能拿到所有字段，查询简单高效
- 字段增删字段通过 ALTER TABLE 即可，不频繁

**macro_monthly（月频宏观数据表）**

| 字段 | 类型 | 说明 |
|------|------|------|
| trade_month | String | 月份，格式 `YYYY-MM`（主键） |
| m2_yoy | Float | M2 同比 |
| m1_yoy | Float | M1 同比 |
| m1_m2_scissors | Float | M1-M2 剪刀差（派生） |
| cpi_yoy | Float | CPI 同比 |
| ppi_yoy | Float | PPI 同比 |
| aggregate_financing | Float | 社会融资规模增量 |

主键：`trade_month`

### 3.3 同步策略

**日频宏观：全量覆盖式**，每次同步删除指定日期范围，重新拉取写入。

**月频宏观：全量覆盖式**，每次同步删除指定月份范围，重新拉取写入。

**派生字段计算时机**：同步完成后，在写入数据库中计算派生字段（如 m1_m2_scissors = m1_yoy - m2_yoy。

**同步顺序：
1. 先同步基础字段（从数据源拉取）
2. 再计算派生字段

---

## 四、统一同步架构

### 4.1 同步任务模型

新增同步任务表 `sync_task`，用于追踪同步进度。

| 字段 | 类型 | 说明 |
|------|------|------|
| task_id | String | 任务 ID（UUID） |
| task_type | String | 任务类型：etf_daily / etf_minute / macro_daily / macro_monthly |
| status | String | 状态：pending / running / completed / failed |
| total | Integer | 总步骤数（如 ETF 数量、字段数量等） |
| current | Integer | 当前完成步骤数 |
| message | String | 状态消息 |
| start_time | DateTime | 开始时间 |
| end_time | DateTime | 结束时间 |
| result | Text | 结果详情（JSON，成功/失败详情） |

### 4.2 同步流程

```
用户点击同步请求
    │
    ▼
创建 sync_task 记录（status=pending）
    │
    ▼
后台线程执行同步
    │
    ├─► 更新 status=running
    │
    ├─► 遍历每个子任务（每只 ETF / 每个宏观字段）
    │     │
    │     ├─► 删除旧数据
    │     ├─► 拉取新数据
    │     ├─► 写入新数据
    │     └─► 更新 current += 1
    │
    ├─► 计算派生字段（仅宏观）
    │
    ├─► 更新 status=completed / failed
    └─► 记录 end_time
```

### 4.3 同步 API 设计

**后端同步引擎设计：
- 使用 FastAPI 接收同步请求后，立即返回 task_id，同步在后台线程执行
- 前端通过轮询 `/api/sync/{task_id} 获取进度
- 同步任务存储在内存中（不持久化，重启丢失）

> 为什么不持久化同步任务：
> - 同步是即时操作，完成即结束
> - 内存实现简单，无需额外表管理
> - 服务重启后任务丢失影响不大，重新同步即可

### 4.4 数据源优先级与现有缓存机制的关系

现有 `CachedDataSource` 是**读透缓存**（read-through），服务于日常查询。

新增的同步功能是**主动全量刷新**（主动刷新，服务于数据管理。

两者关系：
- 日常 API 查询走 `CachedDataSource`，缺数据时自动缓存
- 手动同步是主动全量刷新数据库，保证数据最新
- 同步完成后，后续查询直接命中缓存，速度快

---

## 五、API 接口设计

### 5.1 行情同步 API

#### POST /api/market/sync/etf
触发 ETF 行情同步

**请求体：**
```json
{
  "sec_codes": ["510300.SH", "510500.SH"],  // 可选，不传则同步全部
  "start_date": "2021-01-04",               // 可选，默认 2021-01-04
  "end_date": "2026-08-06",                  // 可选，默认今天
  "period": "daily"                           // 可选，默认 daily
}
```

**响应：**
```json
{
  "task_id": "uuid-xxx",
  "task_type": "etf_daily",
  "status": "running"
}
```

#### GET /api/market/sync/{task_id}
查询同步任务进度

**响应：**
```json
{
  "task_id": "uuid-xxx",
  "task_type": "etf_daily",
  "status": "running", // pending / running / completed / failed
  "total": 10,
  "current": 3,
  "message": "正在同步第 3 / 10 只 ETF",
  "start_time": "2026-08-06T10:00:00",
  "end_time": null,
  "result": null
}
```

### 5.2 宏观同步 API

#### POST /api/macro/sync
触发宏观数据同步

**请求体：**
```json
{
  "fields": ["shibor_3m", "cn_gov_10y"],  // 可选，不传则同步全部
  "start_date": "2021-01-04",            // 可选，默认 2021-01-04
  "end_date": "2026-08-06"               // 可选，默认今天
}
```

**响应：**
```json
{
  "task_id": "uuid-xxx",
  "task_type": "macro_daily",
  "status": "running"
}
```

#### GET /api/macro/sync/{task_id}
查询宏观同步进度（同行情同步进度查询接口，复用同一套）

### 5.3 宏观数据查询 API

#### GET /api/macro/daily
查询日频宏观数据

**参数：**
- `start_date`: 开始日期
- `end_date`: 结束日期
- `fields`: 字段名，逗号分隔，不传返回全部

**响应：**
```json
{
  "dates": ["2021-01-04", "2021-01-05", ...],
  "fields": {
    "shibor_3m": [2.5, 2.6, ...],
    "cn_gov_10y": [3.2, 3.3, ...]
  }
}
```

#### GET /api/macro/monthly
查询月频宏观数据

**参数：**
- `start_month`: 开始月份
- `end_month`: 结束月份
- `fields`: 字段名，逗号分隔

#### GET /api/macro/fields
获取可用宏观字段列表

**响应：**
```json
[
  {"name": "shibor_3m", "category": "利率", "unit": "%", "frequency": "daily", "stability": "high"},
  ...
]
```

---

## 六、前端交互设计

### 6.1 行情页面同步按钮

在行情数据管理页面（或行情 K 线页面顶部）添加同步区域：

```
┌─────────────────────────────────────────────────────────┐
│  数据同步                                          │
│  ┌─────────────┐  ┌─────────────┐  ┌────────────┐    │
│  │  同步行情数据 │  │ 同步宏观数据  │  数据范围: │    │
│  └─────────────┘  └─────────────┘  2021-01-04 ~ 今天 │
│                                                   │
│  同步进度：[████████░░░░] 60% (6/10 只 ETF)       │
│  状态：正在同步 510500.SH...                        │
└─────────────────────────────────────────────────────────┘
```

### 6.2 交互流程

1. 用户点击"同步行情数据"按钮
2. 弹出确认框："确定要全量同步将删除并重新拉取数据，是否继续？"
3. 用户确认后，调用同步按钮变为"同步中...，显示进度条
3. 前端每 2 秒轮询一次进度接口
4. 同步完成后提示"同步完成，共同步 X 只 ETF，Y 条数据"
5. 失败则显示错误信息

### 6.3 宏观数据页面

新增"宏观数据"页面：
- 左侧：字段分类列表（利率/汇率/商品/估值/海外/货币/经济）
- 右侧：选中字段的折线图（支持多选叠加对比）
- 顶部：同步按钮 + 日期范围选择

---

## 七、核心模块变更

### 7.1 新增文件

| 文件 | 说明 |
|------|------|
| `core/data/macro_source.py` | 宏观数据数据源适配器（AkShare + Baostock） |
| `webapp/models/macro.py` | 宏观数据 ORM 模型 |
| `webapp/services/macro_service.py` | 宏观数据服务层 |
| `webapp/services/sync_service.py` | 统一同步服务（任务管理 + 进度追踪） |
| `webapp/api/macro.py` | 宏观数据 API 路由 |
| `webapp/static/macro.html` | 宏观数据前端页面 |

### 7.2 修改文件

| 文件 | 修改内容 |
|------|----------|
| `core/data/base.py` | DataSource 基类增加宏观数据接口 |
| `core/data/akshare_source.py` | 完善 AkShare 宏观数据适配器 |
| `core/data/baostock_source.py` | 增加货币供应量等宏观接口 |
| `core/data/cached_source.py` | 增加宏观数据缓存支持 |
| `webapp/services/data_service.py` | 增加同步相关函数 |
| `webapp/api/market.py` | 增加同步 API |
| `webapp/static/market.html` | 增加同步按钮和进度条 |
| `config/webapp.yaml` | 增加同步相关配置 |

---

## 八、实施步骤

### 阶段一：行情同步功能
1. 新增 `sync_service.py 同步服务（任务管理 + 进度追踪）
2. 新增行情同步 API（POST + GET 进度）
3. 前端行情页面加同步按钮 + 进度条
4. 测试：全量同步 10 只 ETF 从 2021-01-04 到今天

### 阶段二：宏观数据体系
1. 新增宏观数据表（macro_daily + macro_monthly）
2. 完善 AkShare + Baostock 宏观适配器
3. 新增 macro_service.py 服务层
4. 新增宏观数据查询 API
5. 新增宏观数据同步 API
6. 前端宏观数据页面

### 阶段三：优化与完善
1. 错误重试机制
2. 同步日志记录
3. 数据质量校验（缺失值、异常值检测）
4. 性能优化（批量写入、并发控制

---

## 九、风险与注意事项

| 风险 | 影响 | 应对措施 |
|------|------|----------|
| AkShare 接口变更 | 宏观数据拉取失败 | 关键字段双数据源备份；接口版本锁定；失败时友好提示 |
| 同步耗时过长 | 用户等待 | 进度条 + 后台执行 + 可关闭页面不影响；分批提交后可取消 |
| 前复权数据不一致 | 回测结果不准 | 全量覆盖式同步；同步后数据校验 |
| 数据库锁 | 同步时查询慢 | 同步操作在后台线程；使用事务批量写入；查询走读未提交读 |
| 网络不稳定 | 同步中途失败 | 单只/单字段失败不影响其他；记录错误详情；支持重试 |
