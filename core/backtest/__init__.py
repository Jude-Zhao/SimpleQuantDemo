"""统一回测框架包（OPT-01）。

唯一权威组合执行账本与目标构建入口：
- execute_backtest：份额/现金账本执行（唯一执行入口）；
- run_backtest：长表行情 + 因子得分 → 资格检查 → 稀疏目标 → 执行；
- build_target_weights：稀疏目标与决策日志构建；
- solve_target_amounts：扣费后目标分配求解；
- calculate_metrics / calculate_yearly_returns：唯一绩效计算（metrics.py）。
"""

from core.backtest.engine import BacktestConfig, BacktestResult, execute_backtest, run_backtest
from core.backtest.execution import solve_target_amounts
from core.backtest.metrics import MetricsConfig, calculate_metrics, calculate_yearly_returns
from core.backtest.models import ExecutionConfig, TargetPlan
from core.backtest.targets import build_target_weights

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "ExecutionConfig",
    "MetricsConfig",
    "TargetPlan",
    "build_target_weights",
    "calculate_metrics",
    "calculate_yearly_returns",
    "execute_backtest",
    "run_backtest",
    "solve_target_amounts",
]
