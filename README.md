# SimpleQuantDemo

量化研究演示项目：因子研究、策略回测、组合优化与 Web 看板。

## 功能模块

| 模块 | 说明 |
|------|------|
| `core/` | 核心计算层：因子库（插件式注册 + 分类配置）、分析（IC/ICIR）、合成、优化器（等权/得分加权）、约束校验、回测引擎 |
| `research/` | 研究脚本与回测入口（复用 core 层回测引擎） |
| `trading/` | 交易信号模块 |
| `webapp/` | FastAPI + SQLAlchemy + 静态前端：Web 因子看板 |
| `tests/` | 全量 pytest 测试 |

## Web 看板

提供五大能力：

1. **因子展示** — 因子列表、IC/RankIC/ICIR 分析、分组收益、因子相关性
2. **两种策略运行** — FAA（因子类加权线性组合）、EAA（幂函数乘法组合），因子按分类配置、类间权重可配
3. **标的池管理** — 添加/移除 ETF、三维分类（资产类别/风格/行业）
4. **插件式因子库** — 因子注册表 + 自动发现 + 分类配置文件（`core/factors/builtin/factors.yaml`）
5. **分类约束体系** — 分类规则引擎、约束校验、持仓校验结果展示

### 启动 Web 看板

```bash
python run_webapp.py
```

访问 <http://localhost:8000>。API 文档见 <http://localhost:8000/docs>。

详细使用说明见 [docs/Web看板使用手册.md](docs/Web看板使用手册.md)。

## 开发

```bash
# 安装依赖（含测试）
pip install -e ".[test]"

# 运行全部测试
pytest -v
```

## 项目结构

```
SimpleQuantDemo/
├── core/
│   ├── factors/        # 插件式因子库
│   ├── analysis/       # IC / 收益分析
│   ├── synthesis/      # 因子合成
│   ├── optimization/   # 等权 / 得分加权 / 约束校验
│   └── backtest/       # 共享回测引擎
├── research/           # 研究脚本
├── webapp/             # Web 看板（FastAPI + 静态前端）
│   ├── api/            # REST API
│   ├── services/       # 服务层
│   ├── models/         # SQLAlchemy ORM
│   ├── schemas/        # Pydantic 模型
│   └── static/         # 前端 SPA
├── docs/               # 文档
└── tests/              # 测试