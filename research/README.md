# 投研板块使用说明

本目录是独立的**因子研究 + 策略回测**工作区。它把「研究」和「生产」分开：

- **research/**：做实验因子、跑 IC 分析、做策略回测的地方。这里可以随意加实验因子，不会污染生产环境。
- **core/** + **webapp/**：生产环境。只有经过验证、值得上线到 Web 看板的因子，才移植到这里。

研究数据直接读项目共享的 SQLite 数据库（`data/simple_quant.db`，和 Web 看板同一份），研究结论可一键移植到 Web 看板。

---

## 1. 目录结构

```
research/
├── main.py               # 入口：一键跑「因子研究 → 合成 → 回测 → 输出」
├── config.py             # ResearchConfig：策略、IC、共线性等参数（dataclass）
├── factor_config.yaml    # ★ 因子清单：研究用哪些因子、分几类 [改这里最常用]
├── factors/              # 独立研究因子池（和 core 的注册表隔离）
│   ├── registry.py       # 研究因子注册表：register_factor / resolve_factor_class
│   ├── price_position.py # 示例研究因子：价格位置（N日高低区间位置）
│   └── config.py         # 读取 factor_config.yaml → FactorCategory
├── bt_engine.py          # bt(开源库) 回测引擎：权重驱动，产出净值/换手
├── backtest.py           # 兼容层：re-export core.backtest.engine（研究暂未用）
├── visualization/        # 结果绘图（净值曲线 / 因子统计 / 最新持仓）
└── output/               # 回测结果输出（自动生成）
    └── backtest_results/ # summary.csv / factor_stats.csv / equity_curve.csv / *.png ...
```

> research 的因子池与 core 的注册表是**两套独立**的。研究因子用 `research.factors` 注册，`resolve_factor_class` 会**先查研究池、再回退到 core 内置因子**，所以研究配置里可以混用自定义因子和 core 内置因子（momentum / volatility / reversal）。

---

## 2. 数据从哪里来、怎么获取

### 2.1 数据链路

行情数据**不是** research 自己下载的，而是从共享数据库 `data/simple_quant.db` 读取：

```
外部数据源(AkShare 腾讯hqkline后复权 / baostock兜底)
        │  同步(手动触发)
        ▼
data/simple_quant.db  ── etf_daily_bar 表（后复权价 + adj_factor 复权因子）
        │  research 用 SqliteDataSource 读取
        ▼
factor 计算 / IC 分析 / 策略回测
```

- 行情统一为**后复权(hfq)**，避免拆分断崖污染动量/波动率因子。
- research 只读，不写库，数据同步统一走 Web 看板。

### 2.2 获取数据（推荐：Web 设置页）

1. 启动看板：`python run_webapp.py`，访问 <http://localhost:8000>。
2. 进入 **设置页** → 点「同步行情」/「同步宏观数据」。
3. 后台线程拉取并写入 `etf_daily_bar`，带进度条。内部走 `webapp/services/sync_service.py` 的 `start_etf_sync()`（主源 AkShare 腾讯后复权，失败自动回退 baostock）。

> 新增标的：先在 Web 「标的池管理」添加 ETF，再同步行情。数据库里 `universe_items.is_active=1` 的代码就是 research 的标的池。

### 2.3 代码里直接拉数据（不启动 Web）

AkShare 主源拉取指定标的日线（返回长表，含 `date/sec/open/high/low/close/volume/amount`）：

```python
from core.data.akshare_source import AkShareDataSource

ds = AkShareDataSource()
df = ds.get_etf_price_by_codes(
    sec_codes=["510300.SH", "510500.SH", "159915.SZ"],
    start_date="2024-01-01",
    end_date="2025-12-31",
)
```

### 2.4 research 读取数据库

```python
from core.data import SqliteDataSource

ds = SqliteDataSource("data/simple_quant.db")
price_data, macro_data, universe = ds.load_all(
    start_date="2024-01-01", end_date="2025-12-31",
)
# price_data: 长表(date/sec/open/high/low/close/volume/amount)
# universe:   激活标的代码列表，如 ["510300.SH", ...]
```

---

## 3. 配置因子清单（factor_config.yaml）

`factor_config.yaml` 决定研究用哪些因子、怎么分「类」。格式与 `core/factors/builtin/factors.yaml` 对齐，方便日后直接迁移。

```yaml
categories:
  momentum:            # 分类名（key）
    display_name: "动量"  # 展示名
    factors:           # 该类下的因子实例列表
      - name: momentum          # 因子注册名（研究池或 core 内置均可）
        params: { window: 20 }  # 构造参数
      - name: momentum
        params: { window: 60 }
  volatility:
    display_name: "波动"
    factors:
      - name: volatility
        params: { window: 20 }
  research_demo:       # ★ 研究示例分类（含自定义研究因子）
    display_name: "研究示例"
    factors:
      - name: price_position     # 研究池里的自定义因子
        params: { window: 20 }
```

- `name` 解析顺序：**研究因子池 → core 内置注册表**。写错名字会在加载时报错（fail-fast）。
- 同一分类内多个因子实例会**等权合成**成一个类得分。

---

## 4. 一键运行：因子研究 + 策略回测

在项目根目录执行：

```bash
# 默认：FAA 策略、月度调仓、Top 5、研究因子池全部类别
python -m research.main

# 常用参数组合
python -m research.main \
    --start-date 2024-01-01 \
    --end-date 2025-12-31 \
    --strategy eaa \
    --top-n 5 \
    --rebalance-freq monthly
```

### 4.1 CLI 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--start-date` | `2019-11-01` | 回测/研究起始日 |
| `--end-date` | 今天 | 结束日 |
| `--strategy` | `faa` | `faa` 或 `eaa` |
| `--top-n` | `5` | 持仓数量 |
| `--rebalance-freq` | `monthly` | `weekly` 或 `monthly` |
| `--db-path` | `data/simple_quant.db` | 数据库路径 |
| `--output-dir` | `research/output/backtest_results` | 结果输出目录 |

策略与权重模式的对应：

- **FAA** = 因子类得分线性加权 `L = Σ wₖ·norm(catₖ)`，选 Top N 后**组内等权**（`--strategy faa`）。
- **EAA** = 幂函数乘法组合 `S = (Π norm(catₖ)^αₖ)^β`，选 Top N 后**按得分加权**（`--strategy eaa`）。

公式用纯文本表示：`norm` 为截面 min-max 归一化，`w`/`α` 为类权重/缩放系数，`β` 为整体缩放（均为 `ResearchConfig` 可配）。

### 4.2 一次运行做了什么（pipeline）

```
1. 读数据       SqliteDataSource.load_all() → price_data / macro / universe
2. 因子研究     factor_config.yaml → 构建每个因子矩阵 → IC / RankIC / ICIR / 共线性
3. 合成得分     FAA 或 EAA → 合成截面得分矩阵
4. 目标权重     调仓日用 core 优化器(等权/得分加权)算目标权重，ffill 到全部交易日
5. 回测         bt 引擎：WeighTarget + Rebalance → 净值 / 日收益 / 换手
6. 绩效         quantstats 计算年化/波动/夏普/回撤/Sortino/Calmar/胜率
7. 输出         写 CSV + PNG 到 output/
```

### 4.3 输出文件（`research/output/backtest_results/`）

| 文件 | 内容 |
|------|------|
| `summary.csv` | ★ 策略绩效汇总：总收益/年化/波动/夏普/最大回撤/Sortino/Calmar/胜率/换手/调仓次数 |
| `factor_stats.csv` | ★ 因子研究结果：每个因子的 IC / RankIC / ICIR 时间序列 |
| `equity_curve.csv` | 净值曲线 |
| `weights.csv` | 每日目标权重矩阵（date × sec） |
| `synthesized_scores.csv` | 合成得分矩阵 |
| `warnings.txt` | 共线性等警告（高相关因子被标记） |
| `equity_curve.png` | 净值曲线图 |
| `factor_stats.png` | 因子 IC/ICIR 统计图 |
| `latest_weights.png` | 最新一期持仓权重图 |

> **怎么看因子好坏**：看 `factor_stats.csv` 里各因子的 IC 均值 / RankIC 均值 / ICIR。一般经验：|IC| 越大越好、ICIR 越稳越好；`warnings.txt` 里被标记的高相关因子建议二选一，避免冗余。

---

## 5. 写一个自己的研究因子

研究因子和 core 因子用**同一个协议**（继承 `core.factors.base.FactorBuilder`），只是注册到研究池。新建 `research/factors/your_factor.py`：

```python
from core.factors.base import FactorBuilder
from core.factors.utils import pivot_price_field, validate_factor_matrix
from research.factors.registry import register_factor

@register_factor()          # 注册到研究池
class YourFactor(FactorBuilder):
    registry_name = "your_factor"      # 注册名（factor_config.yaml 里用）
    display_name = "你的因子"
    category = "动量"
    description = "一句话说明"
    formula = "F(t) = ..."             # 纯文本公式
    direction = "positive"             # positive / negative
    params_schema = {
        "window": {"type": "int", "default": 20, "min": 1, "max": 252,
                   "step": 1, "label": "窗口天数"},
    }

    def __init__(self, window: int = 20) -> None:
        self.window = window

    @property
    def name(self) -> str:
        return f"your_factor_{self.window}"   # 唯一名

    def build(self, price_data, macro_data, universe) -> "pd.DataFrame":
        # 返回 date × sec 的因子得分矩阵
        close = pivot_price_field(price_data, field="close", universe=universe)
        factor = close.pct_change(self.window)   # 示例
        validate_factor_matrix(factor, universe, name=self.name)
        return factor
```

然后在 `factor_config.yaml` 的某个分类下登记：

```yaml
  momentum:
    factors:
      - { name: your_factor, params: { window: 20 } }
```

重新跑 `python -m research.main` 即可看到该因子的 IC 与回测。

> `build` 必须返回 **index=date、columns=sec** 的 DataFrame，且 columns 顺序与 `universe` 完全一致（`validate_factor_matrix` 会校验）。

---

## 6. 把验证过的因子移植到生产（Web 看板）

研究通过后，把因子从研究池移植到 `core`，就能在 Web 看板被自动发现、用于 EAA/FAA 策略。步骤：

### 6.1 移动文件

把 `research/factors/your_factor.py` 移到 `core/factors/builtin/your_factor/your_factor.py`（目录名 = 因子名）。

### 6.2 改注册方式

研究池用 `research.factors.registry.register_factor`，生产用 `core.factors.registry.register_factor`（带注册名）：

```python
- from research.factors.registry import register_factor
- @register_factor()
+ from core.factors.registry import register_factor
+ @register_factor("your_factor")
```

其余类体（`FactorBuilder` 协议、`build`）**原样保留，不用改**——这正是研究协议与生产协议一致的好处。

### 6.3 登记到分类配置

编辑 `core/factors/builtin/factors.yaml`，把因子登记到某个分类（当前有 momentum/volatility/reversal/volume/other）：

```yaml
  momentum:
    display_name: "动量"
    factors:
      - name: momentum
        params: { window: 20 }
      - name: your_factor          # ★ 新增
        params: { window: 20 }
```

### 6.4 验证

```bash
# 分类配置加载校验（名字写错会在这里报错）
python -c "from core.factors.config import categories_to_dict; print([c['key'] for c in categories_to_dict()])"

# 跑全量测试
pytest -q
```

重启 Web 看板后，因子会出现在「因子列表」，并能作为 EAA/FAA 策略的类内因子使用。

> 移植后建议把旧的 `research/factors/your_factor.py` 删掉，避免研究池重复注册同名因子。

---

## 7. 常见问题

- **报 `Factor 'xxx' ... is not registered / not resolvable`**：`factor_config.yaml` 里因子名写错了，或该因子既不在研究池也不在 core 内置。检查 `name` 拼写。
- **IC 全是 NaN**：横截面标的太少。IC 需要至少 `ic_min_observations`（默认 10）个有效观测，标的池最好 ≥ 10 只。
- **回测结果空 / 净值恒 1**：检查 `universe_items` 是否有激活标的、`etf_daily_bar` 在该时间段是否有数据。
- **研究因子不影响 Web**：研究池与 core 注册表隔离是**有意设计**，移植请按第 6 节操作。
- **需要更细参数**（类权重、α/β、IC 窗口、共线性阈值等）：改 `research/config.py` 的 `ResearchConfig` 字段。