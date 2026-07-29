# Web 因子看板设计文档

**版本**: V1.0
**日期**: 2026-07-29
**状态**: 已确认

---

## 一、项目定位

面向 ETF 的量化策略 Web 看板。在浏览器中展示因子表现、运行多种策略并调参、管理标的池，并对组合做分类约束优化与检查。

**技术栈**: Python + FastAPI + SQLite + 原生 JS + ECharts

**架构原则**:
- 量化引擎（core/）是独立 Python 模块，不依赖 Web 层
- Web 服务层通过 service 层调用 core 能力
- 与 research/、trading/ 共享 core 层，三入口互不耦合

---

## 二、整体架构与目录结构

### 2.1 架构模式

模块化单体：公共核心层 + 三个独立入口（research / trading / webapp）

```
┌─────────────────────────────────────────────────┐
│  前端：静态 HTML + 原生 JS + ECharts             │
│  （左侧边栏导航 + 多 Tab 内容区）                 │
├─────────────────────────────────────────────────┤
│  Web 层：FastAPI（REST API + 静态文件托管）       │
├─────────────────────────────────────────────────┤
│  服务层：webapp/services/                        │
│  （factor / strategy / universe / classification │
│   / data_service）                               │
├─────────────────────────────────────────────────┤
│  核心层：core/（纯计算、无状态、不依赖 Web）       │
│  + factors/ 插件式注册                            │
│  + optimization/ 扩展 MVO + BL + 约束            │
│  + data/ 扩展 AkShare + baostock + 缓存          │
├─────────────────────────────────────────────────┤
│  数据层：SQLite（标的池/分类规则/策略记录/行情缓存）│
└─────────────────────────────────────────────────┘
```

### 2.2 目录结构

```
SimpleQuantDemo/
├── core/                     # 公共核心计算层
│   ├── data/
│   │   ├── base.py           # DataSource 抽象基类
│   │   ├── csv_source.py     # CSV 数据源
│   │   ├── akshare_source.py # AkShare 数据源
│   │   ├── baostock_source.py# baostock 数据源
│   │   ├── cached_source.py  # 缓存装饰器/混合数据源
│   │   ├── macro_catalog.py
│   │   ├── validators.py
│   │   └── utils.py
│   ├── factors/
│   │   ├── base.py           # FactorBuilder 基类
│   │   ├── registry.py       # 自动发现 + 装饰器注册
│   │   ├── momentum.py
│   │   ├── volatility.py
│   │   ├── value.py          # 新增示例因子
│   │   └── utils.py
│   ├── analysis/             # IC/共线性分析
│   ├── synthesis/            # 因子合成
│   ├── optimization/
│   │   ├── base.py
│   │   ├── equal_weight.py   # 等权优化
│   │   ├── mvo.py            # 均值方差优化
│   │   ├── bl.py             # Black-Litterman
│   │   └── constraints.py    # 分类约束体系
│   └── calendar.py
│
├── research/                 # 投研入口（不动）
├── trading/                  # 实盘入口（不动）
│
├── webapp/                   # Web 看板模块（新增）
│   ├── main.py               # FastAPI 应用入口
│   ├── config.py             # 配置（Pydantic + YAML）
│   ├── api/                  # API 路由层
│   │   ├── factors.py
│   │   ├── strategies.py
│   │   ├── universe.py
│   │   ├── classifications.py
│   │   └── health.py
│   ├── services/             # 业务逻辑层
│   │   ├── factor_service.py
│   │   ├── strategy_service.py
│   │   ├── universe_service.py
│   │   ├── classification_service.py
│   │   └── data_service.py
│   ├── models/               # SQLAlchemy ORM 模型
│   │   ├── database.py       # 连接 + 基类
│   │   ├── universe.py
│   │   ├── classification.py
│   │   └── strategy_run.py
│   ├── schemas/              # Pydantic 请求/响应模型
│   │   ├── factor.py
│   │   ├── strategy.py
│   │   ├── universe.py
│   │   └── classification.py
│   └── static/               # 前端静态文件
│       ├── index.html
│       ├── css/
│       ├── js/
│       │   ├── app.js        # 主逻辑 + 路由
│       │   ├── dashboard.js  # 首页
│       │   ├── factors.js    # 因子看板
│       │   ├── strategies.js # 策略运行
│       │   ├── universe.js   # 标的池管理
│       │   ├── classification.js # 分类约束
│       │   ├── settings.js   # 设置页
│       │   └── api.js        # API 调用封装
│       └── lib/              # ECharts 等第三方库
│
├── tests/                    # 补充 webapp 相关测试
├── docs/
│   └── plans/
│       └── 2026-07-29-web-dashboard-design.md
├── data_example/
├── config/
│   └── webapp.yaml           # Web 服务配置
├── pyproject.toml
└── .env.example
```

---

## 三、因子插件式注册机制

### 3.1 注册方式

装饰器 + 自动扫描。新增因子只需在 `core/factors/` 下加一个 `.py` 文件，用 `@register_factor` 装饰。

```python
# core/factors/registry.py

_factor_registry: dict[str, type[FactorBuilder]] = {}

def register_factor(name: str | None = None):
    """装饰器：注册因子类到全局注册表"""
    def decorator(cls: type[FactorBuilder]):
        factor_name = name or cls.__name__
        _factor_registry[factor_name] = cls
        return cls
    return decorator

def discover_factors(package: str = "core.factors"):
    """自动扫描包内因子模块并导入，触发装饰器注册"""

def get_factor_registry() -> dict[str, type[FactorBuilder]]:
    """获取因子注册表（确保已扫描）"""

def get_factor_meta(name: str) -> FactorMeta:
    """获取单个因子的元信息"""
```

### 3.2 因子自描述

每个因子类通过类属性暴露元数据，供 Web 端动态渲染参数表单：

```python
@register_factor("momentum")
class MomentumFactor(FactorBuilder):
    name = "momentum"
    display_name = "动量因子"
    description = "N日收盘价收益率"
    params_schema = {
        "window": {
            "type": "int",
            "default": 5,
            "min": 1,
            "max": 252,
            "label": "窗口天数"
        }
    }

    def __init__(self, window: int = 5):
        self.window = window

    def build(self, price_data, macro_data, universe) -> pd.DataFrame:
        ...
```

---

## 四、标的池管理与分类约束体系

### 4.1 标的池管理

**数据库表：universe_items**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| sec_code | TEXT UNIQUE | ETF代码，如 510300.SH |
| sec_name | TEXT | ETF名称 |
| is_active | BOOLEAN | 是否在标的池中 |
| added_at | DATETIME | 加入时间 |
| removed_at | DATETIME NULL | 移出时间 |
| meta | JSON | 扩展字段：基金公司、规模、跟踪指数等 |

**核心 API**：
- `GET /api/universe` — 当前标的池列表
- `POST /api/universe` — 批量添加标的
- `DELETE /api/universe/{sec_code}` — 移出标的
- `GET /api/universe/available` — 可添加的 ETF 列表

### 4.2 分类规则体系

**数据库表：classification_rules**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| rule_name | TEXT | 规则名，如"规模分类" |
| category_key | TEXT | 分类维度，如"size" |
| rule_type | TEXT | manual / by_field / by_range |
| config | JSON | 规则配置 |
| is_active | BOOLEAN | 是否启用 |
| priority | INTEGER | 优先级，高优先级先打标 |

**规则类型**：

| 类型 | config 示例 | 说明 |
|------|-------------|------|
| manual | `{"sec_codes": ["510300.SH"], "category_value": "大盘"}` | 手动指定 |
| by_field | `{"field": "track_index", "value": "沪深300", "category_value": "大盘"}` | 按字段值 |
| by_range | `{"field": "fund_size", "min": 100, "category_value": "大规模"}` | 按数值范围 |

**分类服务**：按优先级依次执行所有 active 规则，同一 category_key 下高优先级覆盖低优先级。

### 4.3 约束体系

约束作用于两个环节：
1. **优化前约束**：作为优化器的输入条件
2. **优化后校验**：优化完成后兜底检查

**约束配置结构**：
```python
class CategoryConstraint:
    category_key: str          # 分类维度
    category_value: str        # 分类值
    min_weight: float | None   # 该类别最小权重
    max_weight: float | None   # 该类别最大权重
    min_count: int | None      # 该类别最少持仓数
    max_count: int | None      # 该类别最多持仓数

class OptimizationConstraints:
    single_min_weight: float | None   # 单标的最小权重
    single_max_weight: float | None   # 单标的最大权重
    category_constraints: list[CategoryConstraint]
    turnover_limit: float | None      # 换手率约束
```

**优化后校验**：
```python
def validate_constraints(weights, classifications, constraints) -> list[ValidationResult]:
    """校验权重是否满足所有约束，返回违规项列表"""
```

---

## 五、策略运行与三种优化器

### 5.1 三种策略

| 策略 | 标识 | 核心逻辑 | 主要参数 |
|------|------|----------|----------|
| 线性因子组合 | linear_factor | 多因子加权打分 → Top N 等权 | 因子权重列表、调仓频率、Top N、约束 |
| 均值方差优化 | mvo | 收益+协方差 → Markowitz 优化 | 目标收益/风险厌恶、调仓频率、约束 |
| Black-Litterman | bl | 先验收益 + 主观观点 → BL 后验 → 优化 | 观点矩阵P/Q、置信度、风险厌恶、约束 |

### 5.2 策略运行流程

```
POST /api/strategies/run
    → strategy_service.run_strategy(strategy_type, params)
        ├─ 从 DB 读取标的池
        ├─ data_service 获取行情/宏观数据
        ├─ 按策略类型实例化组件并计算
        ├─ 应用约束（优化器内约束）
        ├─ 回测引擎计算净值
        ├─ 约束兜底校验
        └─ 保存运行记录到 DB
    → 返回结果
```

### 5.3 优化器实现

**MVO**：scipy.optimize.minimize 求解，目标函数最小化组合方差（或最大化夏普），约束作为不等式约束传入。

**Black-Litterman**：
- 先验收益 Π：市场均衡收益
- 观点：P（观点×标的矩阵）、Q（观点收益向量）、Ω（观点置信度对角阵）
- 后验收益：E(R) = (Σ⁻¹ + P'Ω⁻¹P)⁻¹ · (Σ⁻¹Π + P'Ω⁻¹Q)
- 后验协方差：M = [Σ⁻¹ + P'Ω⁻¹P]⁻¹
- 得到后验估计后，送入与 MVO 相同的优化器求解

### 5.4 策略运行记录

**数据库表：strategy_runs**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| strategy_type | TEXT | linear_factor / mvo / bl |
| params | JSON | 运行时参数快照 |
| universe_snapshot | JSON | 当时标的池快照 |
| start_date | DATE | 回测起始日 |
| end_date | DATE | 回测结束日 |
| result_summary | JSON | 绩效指标摘要 |
| status | TEXT | success / failed |
| error_msg | TEXT NULL | 失败原因 |
| created_at | DATETIME | 创建时间 |

详细数据（净值、权重矩阵等）存文件：`data/runs/{run_id}/`

---

## 六、前端设计

### 6.1 布局

左侧边栏导航 + 右侧内容区。

**侧边栏菜单**：
- 首页（Dashboard）
- 因子看板
- 策略运行
- 标的池
- 分类约束
- 设置

### 6.2 首页（Dashboard）

```
┌─────────────────────────────────────────────────┐
│  统计卡片：标的池数量 | 因子数量 | 今日运行 | 状态 │
├─────────────────────────────────────────────────┤
│  ┌─ ETF走势（多折线）──────┐ ┌─因子IC排名─┐      │
│  │  ECharts 折线图          │ │  条形图    │      │
│  │  下拉选择标的（默认5只）  │ └────────────┘      │
│  │  沪深300/中证500/        │ ┌─最近策略运行─┐    │
│  │  创业板/黄金/国债        │ │  列表        │    │
│  └──────────────────────────┘ └──────────────┘    │
├─────────────────────────────────────────────────┤
│  ┌─ 净值曲线对比（最近3次运行）────────────────┐  │
│  │  ECharts 多线对比                            │  │
│  └─────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

**默认 ETF 列表**：沪深300ETF、中证500ETF、创业板ETF、黄金ETF、国债ETF

### 6.3 因子看板

- 因子筛选（多选因子、时间范围、调仓频率）
- 指标概览卡片（IC、RankIC、ICIR、年化收益）
- IC / RankIC / ICIR 时序图（多因子对比）
- 分组收益柱状图（5分组）
- 因子相关性热力图

### 6.4 策略运行

- 左侧：策略选择 + 参数表单（根据策略类型动态渲染）
  - 线性因子：因子多选 + 权重滑块、Top N、调仓频率、约束配置
  - MVO：目标收益/风险厌恶、协方差窗口、约束配置
  - BL：观点编辑表格（标的/方向/幅度/置信度）、约束配置
- 右侧：运行结果（净值曲线、绩效卡片、权重条形图、约束校验）
- 底部：历史运行记录列表

### 6.5 标的池管理

- 标的列表表格（代码、名称、分类标签、状态）
- 添加/移除标的
- 搜索、筛选
- 批量导入

### 6.6 分类约束

- 分类规则列表（增删改）
- 规则编辑器（根据规则类型动态显示配置项）
- "应用分类"按钮，预览分类结果
- 约束配置表单（按类别设置权重上下限）

### 6.7 技术选型

- 原生 JS + 模块化，不引入前端框架
- ECharts 做图表（CDN 引入）
- 轻量 CSS 框架做基础样式
- 简易 Hash 路由
- fetch 封装 API 调用

---

## 七、数据源与配置体系

### 7.1 数据源架构

```
data_service
    ↓
CachedDataSource
    ├─ 优先读 SQLite 行情缓存表
    └─ 未命中 → AkShare（主）/ baostock（备）拉取 → 写入缓存
```

**缓存表：etf_daily_bar**

| 字段 | 类型 |
|------|------|
| sec_code | TEXT |
| trade_date | DATE |
| open / high / low / close / volume / amount | REAL |
| source | TEXT |

唯一索引：(sec_code, trade_date)

### 7.2 配置体系

YAML + Pydantic + 环境变量覆盖。

```yaml
# config/webapp.yaml
server:
  host: 0.0.0.0
  port: 8000

database:
  url: sqlite:///./data/simple_quant.db

datasource:
  primary: akshare
  secondary: baostock
  cache_enabled: true
  cache_days: 365

factors:
  auto_discover: true
  scan_package: core.factors

strategy:
  max_history_runs: 100
```

敏感信息通过环境变量（.env 文件）注入，不硬编码。

### 7.3 部署

- 启动命令：`uvicorn webapp.main:app --host 0.0.0.0 --port 8000`
- 前端静态文件由 FastAPI 直接托管
- 内网部署，不做认证
- Windows 下可用 nssm 注册为服务常驻运行

### 7.4 新增依赖

| 包 | 用途 |
|----|------|
| fastapi | Web 框架 |
| uvicorn | ASGI 服务器 |
| sqlalchemy | ORM |
| pydantic + pydantic-settings | 配置与校验 |
| pyyaml | YAML 配置 |
| scipy | 优化求解 |
| baostock | 备用数据源 |
| python-dotenv | 环境变量 |

---

## 八、API 清单

### 因子
- `GET /api/factors` — 因子列表（元信息）
- `POST /api/factors/compute` — 计算因子并返回分析结果
- `GET /api/factors/{name}/history` — 因子历史表现

### 策略
- `GET /api/strategies` — 可用策略列表 + 参数 schema
- `POST /api/strategies/run` — 运行策略
- `GET /api/strategies/runs` — 历史运行记录
- `GET /api/strategies/runs/{id}` — 单次运行详情
- `GET /api/strategies/runs/{id}/export` — 导出结果

### 标的池
- `GET /api/universe` — 当前标的池
- `POST /api/universe` — 批量添加
- `DELETE /api/universe/{sec_code}` — 移除
- `GET /api/universe/available` — 可添加 ETF 列表

### 分类约束
- `GET /api/classifications/rules` — 分类规则列表
- `POST /api/classifications/rules` — 新增规则
- `PUT /api/classifications/rules/{id}` — 修改规则
- `DELETE /api/classifications/rules/{id}` — 删除规则
- `POST /api/classifications/apply` — 执行分类并返回结果
- `GET /api/constraints` — 当前约束配置
- `PUT /api/constraints` — 更新约束配置

### 行情数据
- `GET /api/market/etf/list` — ETF 列表
- `GET /api/market/etf/{sec_code}/history` — ETF 历史行情
- `POST /api/market/refresh` — 刷新数据

### 健康检查
- `GET /api/health` — 服务状态
