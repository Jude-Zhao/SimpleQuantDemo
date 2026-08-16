"""Research pipeline configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "simple_quant.db"


@dataclass(frozen=True)
class ResearchConfig:
    """Configuration for the built-in research pipeline.

    Factors are driven by ``research/factor_config.yaml`` (not hard-coded).
    ``strategy_type`` selects between the FAA (equal-weight Top-N) and EAA
    (score-proportional Top-N) composite strategies.
    """

    db_path: Path = DEFAULT_DB_PATH
    output_dir: Path = PROJECT_ROOT / "research" / "output" / "backtest_results"
    start_date: str = "2019-11-01"
    end_date: str | None = None
    # Data loading start (warm-up history). If None, defaults to ``start_date``
    # so evaluation/backtest and data loading share the same origin. When set
    # earlier than ``start_date``, factors get look-back history for windows
    # (e.g. 120d) while IC/backtest still start at ``start_date``.
    data_start_date: str | None = None

    # Strategy selection
    strategy_type: str = "faa"  # "faa" | "eaa"
    top_n: int = 5
    max_weight: float = 1.0
    min_weight: float = 0.0
    rebalance_freq: str = "5d"  # "5d"（每5个交易日） | "weekly" | "monthly"
    weight_mode: str = "equal"  # "equal" (FAA) | "score" (EAA)

    # FAA / EAA category-level parameters. Empty dicts default to equal
    # weight / exponent across all non-empty categories.
    # 默认值来自 research/tune_strategy_params.py 网格搜索（区间
    # 2021-01-04~2026-08-14，5d 调仓，1bp，top_n=5）：
    #   FAA：动量0.20/反转0.30/波动0.25/量能0.25
    #   EAA：α 0.5/1/1/1.25，β 0.5
    class_weights: dict[str, float] = field(
        default_factory=lambda: {
            "momentum": 0.20, "reversal": 0.30, "volatility": 0.25, "volume": 0.25,
        }
    )
    exponents: dict[str, float] = field(
        default_factory=lambda: {
            "momentum": 0.5, "reversal": 1.0, "volatility": 1.0, "volume": 1.25,
        }
    )
    beta: float = 0.5

    # Factor evaluation parameters (independent research output, not used to
    # drive the composite strategy).
    forward_return_horizon: int = 5
    ic_min_observations: int = 10
    icir_window: int = 20
    icir_min_periods: int = 10
    collinearity_threshold: float = 0.7


def default_research_config(output_dir: Path | None = None) -> ResearchConfig:
    """Return a config pointed at the project SQLite database."""
    return ResearchConfig(
        output_dir=output_dir or PROJECT_ROOT / "research" / "output" / "backtest_results",
    )