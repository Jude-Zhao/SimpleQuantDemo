"""Research pipeline configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from research.backtest import BacktestConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_EXAMPLE_DIR = PROJECT_ROOT / "data_example"


@dataclass(frozen=True)
class ResearchConfig:
    """Configuration for the built-in research pipeline."""

    etf_price_path: Path
    macro_factors_path: Path
    universe_path: Path
    output_dir: Path = PROJECT_ROOT / "research" / "output" / "backtest_results"
    start_date: str = "2019-11-01"
    end_date: str | None = None
    momentum_window: int = 5
    volatility_window: int = 20
    forward_return_horizon: int = 5
    ic_min_periods: int = 10
    icir_window: int = 20
    icir_min_periods: int = 10
    half_life_periods: int = 20
    collinearity_threshold: float = 0.7
    backtest: BacktestConfig = BacktestConfig()


def default_research_config(output_dir: Path | None = None) -> ResearchConfig:
    """Return a config pointed at data_example files."""
    etf_price_path, macro_factors_path, universe_path = discover_example_data_paths()
    return ResearchConfig(
        etf_price_path=etf_price_path,
        macro_factors_path=macro_factors_path,
        universe_path=universe_path,
        output_dir=output_dir or PROJECT_ROOT / "research" / "output" / "backtest_results",
    )


def discover_example_data_paths(data_dir: Path = DATA_EXAMPLE_DIR) -> tuple[Path, Path, Path]:
    """Discover bundled sample ETF, macro, and universe files without hard-coded Chinese names."""
    csv_paths = sorted(
        [path for path in data_dir.iterdir() if path.suffix.lower() == ".csv"],
        key=lambda path: path.stat().st_size,
    )
    if len(csv_paths) != 2:
        raise FileNotFoundError(f"Expected exactly two CSV files in {data_dir}, found {len(csv_paths)}.")

    macro_factors_path = csv_paths[0]
    etf_price_path = csv_paths[-1]
    universe_path = next((path for path in data_dir.iterdir() if path.suffix.lower() == ".xlsx"), None)
    if universe_path is None:
        raise FileNotFoundError(f"Could not find universe xlsx file in {data_dir}.")
    return etf_price_path, macro_factors_path, universe_path

