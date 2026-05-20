"""Trading signal configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from research.config import PROJECT_ROOT, discover_example_data_paths


@dataclass(frozen=True)
class TradingConfig:
    """Configuration for position signal generation."""

    etf_price_path: Path
    macro_factors_path: Path
    universe_path: Path
    output_dir: Path = PROJECT_ROOT / "trading" / "output" / "positions"
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
    top_n: int = 5
    max_weight: float = 0.5
    min_weight: float = 0.0


def default_trading_config(output_dir: Path | None = None) -> TradingConfig:
    """Return a config pointed at data_example files."""
    etf_price_path, macro_factors_path, universe_path = discover_example_data_paths()
    return TradingConfig(
        etf_price_path=etf_price_path,
        macro_factors_path=macro_factors_path,
        universe_path=universe_path,
        output_dir=output_dir or PROJECT_ROOT / "trading" / "output" / "positions",
    )

