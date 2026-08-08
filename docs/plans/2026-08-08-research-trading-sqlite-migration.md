---

# research / trading 数据源迁移到 SQLite 设计文档

**版本**: V2.1
**日期**: 2026-08-08
**状态**: 已实施

---

## 一、背景与目标

### 1.1 现状

| 入口 | 数据来源 | 说明 |
|------|----------|------|
| Web 看板（webapp） | `data/simple_quant.db`（SQLite） | 全量行情/宏观/标的，5 只 active ETF，行情 2021-01-04 ~ 最新 |
| research（研究回测） | `data_example/`（CSV + xlsx） | 通过 `CsvDataSource` 读取 |
| trading（交易信号） | `data_example/`（CSV + xlsx） | 通过 `CsvDataSource` 读取 |

项目实际数据已统一沉淀在 `data/simple_quant.db`，但 `research` / `trading` 两条管线仍依赖被 gitignore 的 `data_example/` 示例文件，数据源不统一。

### 1.2 目标

1. `research` / `trading` 改为**只从 SQLite 数据库读取数据**
2. 新增 `SqliteDataSource`，实现与 `DataSource` 相同的接口
3. **删除 `CsvDataSource` 类**及其所有引用（含测试），数据只在数据库层适配

### 1.3 明确不做（本期范围外）

- 不改造 Web 数据同步机制（`data/` 数据如何灌入 DB 由已有同步流程负责）
- 不动 `etf_minute_bar` / `classification_rules` / `strategy_runs` 等与回测无关的表
- `data_example/` 目录物理文件保留，但**不再被任何代码引用**（gitignore、pyproject exclude 保持现状）

---

## 二、核心设计：SqliteDataSource

### 2.1 位置与依赖

新增文件：`core/data/sqlite_source.py`

- 用**标准库 `sqlite3`** 直连数据库，不依赖 SQLAlchemy ORM、不依赖 webapp/FastAPI
- 符合 `core` 层"不依赖 Web / 不依赖 ORM"的既有架构原则
- 构造参数：`SqliteDataSource(db_path: str | Path)`

### 2.2 实现 DataSource 接口

实现 `core/data/base.py` 的 `DataSource` 抽象类，三个方法：

| 方法 | 读取表 | 产出格式 |
|------|--------|----------|
| `get_etf_price()` | `etf_daily_bar` | 长表 `date/sec/open/high/low/close/volume/amount` |
| `get_macro_factors()` | `macro_daily` | 以 `date`（DatetimeIndex）为索引的宽表，列为标准因子名 |
| `get_universe()` | `universe_items` | `list[str]`，取 `is_active == 1` 的 `sec_code` |

另提供便捷方法 `load_all()`（依次调用三个方法并做 `validate_price_universe_coverage`）。

### 2.3 数据格式映射

**etf_daily_bar → get_etf_price**

```sql
SELECT trade_date, sec_code, open, high, low, close, volume, amount
FROM etf_daily_bar
WHERE trade_date BETWEEN ? AND ?   -- 按 start/end 过滤
ORDER BY trade_date, sec_code
```

映射：`trade_date→date`、`sec_code→sec`。复用 `utils.py` 的清洗函数（`standardize_etf_price` / `filter_by_date_range`），保证与 CSV 路径输出一致。

**macro_daily → get_macro_factors**

```sql
SELECT * FROM macro_daily WHERE trade_date BETWEEN ? AND ?
```

- `trade_date` 为 `YYYY-MM-DD` 字符串，转 `DatetimeIndex` 后 `set_index`
- 列名已与标准因子名对齐（`shibor_3m`、`cn_gov_10y` 等），无需改名
- 复用 `utils.py` 的 `align_macro_to_trading_dates`（按交易日 reindex + ffill）与 `filter_by_date_range`

> `macro_monthly` 本期不接入（内置因子 momentum/volatility 不依赖宏观），后续需要再扩展。

**universe_items → get_universe**

```sql
SELECT sec_code FROM universe_items WHERE is_active = 1 ORDER BY sec_code
```

- `sec_code` 格式（`510300.SH`）与 `etf_daily_bar.sec_code` 一致，天然满足覆盖校验

### 2.4 只读原则

`SqliteDataSource` 仅执行 SELECT，不改写数据库，避免与 Web 写路径冲突。

---

## 三、删除 CsvDataSource

- 删除文件：`core/data/csv_source.py`
- 删除 `core/data/__init__.py` 中的 `CsvDataSource` 导入与导出，改为导出 `SqliteDataSource`
- 清理所有 `from core.data import CsvDataSource` 引用（见"测试改造"）

---

## 四、research / trading 改造

### 4.1 research/config.py

`ResearchConfig` 字段变更：

- **移除**：`etf_price_path`、`macro_factors_path`、`universe_path`
- **新增**：`db_path: Path = PROJECT_ROOT / "data" / "simple_quant.db"`
- **移除**：`discover_example_data_paths()`、`DATA_EXAMPLE_DIR`
- `default_research_config()` 直接指向 `data/simple_quant.db`

### 4.2 trading/config.py

`TradingConfig` 同样变更：移除三个文件路径，新增 `db_path`，默认指向 `data/simple_quant.db`；移除对 `discover_example_data_paths` 的依赖。

### 4.3 research/main.py

`run_research()` 数据源构造：

```python
from core.data import SqliteDataSource
data_source = SqliteDataSource(db_path=config.db_path)
```

其余管线逻辑（因子计算、IC、合成、回测）**完全不变**。

### 4.4 trading/signal.py

`generate_trading_signal()` 数据源构造同样改为 `SqliteDataSource(db_path=config.db_path)`，其余逻辑不变。

---

## 五、测试改造（内置哑数据 fixture）

### 5.1 思路

测试**不再依赖任何外部文件**（data_example / 真实 db）。新增 `tests/conftest.py`，用代码生成一套**确定性哑数据**，写入 `tmp_path` 下的临时 SQLite 库（schema 与真实 db 一致），测试通过 `SqliteDataSource` 读取。

优点：完全自包含、可重现、可在 CI 运行；`data_example` 彻底不再被任何代码引用。

### 5.2 哑数据规模

| 项目 | 规模 |
|------|------|
| ETF 标的 | 12 只（`510300.SH`、`510500.SH`、`159915.SZ`、`518880.SH`、`511010.SH`、`510050.SH`、`510880.SH`、`159901.SZ`、`510180.SH`、`159919.SZ`、`588000.SH`、`512100.SH`） |
| 交易日 | `pd.bdate_range("2024-01-01", "2026-03-13")`（约 550 个交易日） |
| 价格 | 确定性随机游走（固定 seed，初始价 100，日收益 ~N(0, 0.01)） |
| macro_daily | 14 个日频标准字段的哑值（与交易日对齐） |
| universe_items | 12 只，`is_active = 1` |

足以跑通完整 research 管线（momentum/volatility → IC → ICIR → 合成 → 回测）。

### 5.3 conftest.py 内容

```python
# tests/conftest.py
@pytest.fixture
def sqlite_source(tmp_path):
    db_path = tmp_path / "test.db"
    create_test_db(db_path)   # 建表 + 种入哑数据
    return SqliteDataSource(db_path)
```

### 5.4 各测试文件改造

| 文件 | 改动 |
|------|------|
| `test_factors.py` / `test_backtest.py` / `test_analysis_ic.py` / `test_collinearity.py` / `test_optimization.py` / `test_synthesis.py` | 删除 `_example_paths()` / `DATA_EXAMPLE` / `CsvDataSource` 导入；端到端测试改为接收 `sqlite_source` fixture，`load_all(...)` 结果不变，宽松断言保留 |
| `test_data_module.py` | `CsvDataSourceTests` 改为 `SqliteDataSourceTests`，断言改为基于哑数据规模（去掉硬编码 43176/1542/28 等）；`DataUtilsTests` 保留 |
| `test_research_main.py` | 用 `replace(config, db_path=tmp_db)` 指向临时库；新增对 `SqliteDataSource` 的验证 |
| 新增 `test_sqlite_source.py` | 专项验证 `get_etf_price` / `get_macro_factors` / `get_universe` / `load_all`、日期过滤、universe 覆盖校验 |

---

## 六、实施步骤

1. 新增 `core/data/sqlite_source.py`，实现 `SqliteDataSource`
2. 更新 `core/data/__init__.py`：导出 `SqliteDataSource`，移除 `CsvDataSource`
3. 新增 `tests/conftest.py`（哑数据 fixture）
4. 改造 `research/config.py`、`trading/config.py`
5. 改造 `research/main.py`、`trading/signal.py`
6. 改造 6 个 core 测试文件 + `test_data_module.py` + `test_research_main.py`
7. 新增 `tests/test_sqlite_source.py`
8. 删除 `core/data/csv_source.py`
9. 更新 `docs/用户手册.md` 中关于数据源/`data_example` 的描述
10. 运行全量测试验证

---

## 七、风险与注意事项

| 风险/注意 | 说明 | 应对 |
|-----------|------|------|
| 标的池与行情覆盖率 | universe 若有代码缺行情，覆盖校验报错 | 已核验当前 5 只 active 标的均有完整行情；哑数据天然一致 |
| 删除 CsvDataSource 的影响面 | 引用点达 14 个文件 | 已全量盘点，逐个替换 |
| `macro_daily` 起始 1994 | 回测范围受行情限制（哑数据 2024 起），宏观 reindex 后自动对齐 |
| 测试断言依赖具体数据 | 端到端断言多为宽松（shape/index/notna>0） | 哑数据可支撑；`test_data_module` 硬编码断言改为哑数据规模 |
| 数据库只读 | 仅 SELECT | 避免与 Web 写路径冲突 |

---

## 八、实施记录（V2.1 补充）

实施过程中发现并处理了以下问题，均已在代码中落地：

1. **`validate_price_universe_coverage` 放宽为单向校验**：真实库 `etf_daily_bar` 存有比 active `universe_items` 更多的 ETF（历史同步数据多），原"双向严格相等"会导致 `missing_in_universe` 报错。改为只校验 `universe ⊆ price`（universe 代码必须都有行情，允许 price 有额外代码）。

2. **CLI 新增 `--db-path` 参数**（research.main / trading.main）：便于指定数据库，也让 CLI 测试可指向临时哑库。

3. **CLI 测试修复**：原本硬编码不存在的 Python 路径 `D:\Coding\APPS\Miniconda3Py38_4.9.2\...`，是预存失败；改用 `sys.executable` 并传 `--db-path` 指向临时哑库，现已通过。

4. **`SqliteDataSource._iso_date` 用 `YYYY-MM-DD`**：`normalize_datetime(...).isoformat()` 会带 `T00:00:00`，与表中 `trade_date`（`YYYY-MM-DD`）做 SQLite 字符串比较时会把当天排除，导致日期过滤丢一天。改为 `strftime("%Y-%m-%d")`。

5. **真实库 5 只 active 标的 IC 不可算**：IC 计算需横截面 ≥ `ic_min_periods=10`，5 只不够导致 IC 全 NaN。这是数据量配置问题，不属本次迁移范围；若要跑真实库 research，需扩大 universe 至 ≥10 只或调低 `ic_min_periods`。

**验证结果**：全量测试 `222 passed, 1 skipped`（此前 `216 passed, 2 failed, 1 skipped`）。