"""Research pipeline configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from research.backtest import BacktestConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "simple_quant.db"


@dataclass(frozen=True)
class ResearchConfig:
    """Configuration for the built-in research pipeline."""

    db_path: Path = DEFAULT_DB_PATH
    output_dir: Path = PROJECT_ROOT / "research" / "output" / "backtest_results"
    start_date: str = "2019-11-01"
    end_date: str | None = None
    momentum_window: int = 5
    volatility_window: int = 20
    forward_return_horizon: int = 5
    ic_min_observations: int = 10
    icir_window: int = 20
    icir_min_periods: int = 10
    half_life_periods: int = 20
    collinearity_threshold: float = 0.7
    backtest: BacktestConfig = BacktestConfig()


def default_research_config(output_dir: Path | None = None) -> ResearchConfig:
    """Return a config pointed at the project SQLite database."""
    return ResearchConfig(
        output_dir=output_dir or PROJECT_ROOT / "research" / "output" / "backtest_results",
    )