from __future__ import annotations

import subprocess
from pathlib import Path

import pandas as pd

from research.config import default_research_config, discover_example_data_paths
from research.main import calculate_backtest_summary, run_research


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(r"D:\Coding\APPS\Miniconda3Py38_4.9.2\envs\QuantitativeTrading\python.exe")


def test_discover_example_data_paths() -> None:
    etf_path, macro_path, universe_path = discover_example_data_paths()

    assert etf_path.exists()
    assert macro_path.exists()
    assert universe_path.exists()
    assert etf_path.suffix == ".csv"
    assert macro_path.suffix == ".csv"
    assert universe_path.suffix == ".xlsx"
    assert etf_path.stat().st_size > macro_path.stat().st_size


def test_run_research_writes_outputs(tmp_path: Path) -> None:
    config = default_research_config(output_dir=tmp_path)
    config = config.__class__(
        etf_price_path=config.etf_price_path,
        macro_factors_path=config.macro_factors_path,
        universe_path=config.universe_path,
        output_dir=config.output_dir,
        start_date="2025-06-02",
        end_date="2026-03-13",
        momentum_window=config.momentum_window,
        volatility_window=config.volatility_window,
        forward_return_horizon=config.forward_return_horizon,
        ic_min_periods=config.ic_min_periods,
        icir_window=config.icir_window,
        icir_min_periods=config.icir_min_periods,
        half_life_periods=config.half_life_periods,
        collinearity_threshold=config.collinearity_threshold,
        backtest=config.backtest,
    )

    result = run_research(config)

    assert result.selected_factors
    assert {"total_return", "annual_return", "max_drawdown"}.issubset(result.summary.index)
    assert result.backtest.equity_curve.dropna().iloc[-1] > 0
    for path in result.output_paths.values():
        assert path.exists()
    summary = pd.read_csv(result.output_paths["summary"], index_col=0)
    factor_stats = pd.read_csv(result.output_paths["factor_stats"], index_col=0, header=[0, 1])
    assert "value" in summary.columns
    assert len(factor_stats) < len(result.backtest.equity_curve)


def test_calculate_backtest_summary_has_expected_fields(tmp_path: Path) -> None:
    config = default_research_config(output_dir=tmp_path)
    config = config.__class__(
        etf_price_path=config.etf_price_path,
        macro_factors_path=config.macro_factors_path,
        universe_path=config.universe_path,
        output_dir=config.output_dir,
        start_date="2025-12-01",
        end_date="2026-03-13",
        momentum_window=config.momentum_window,
        volatility_window=config.volatility_window,
        forward_return_horizon=config.forward_return_horizon,
        ic_min_periods=config.ic_min_periods,
        icir_window=config.icir_window,
        icir_min_periods=config.icir_min_periods,
        half_life_periods=config.half_life_periods,
        collinearity_threshold=config.collinearity_threshold,
        backtest=config.backtest,
    )
    result = run_research(config)

    summary = calculate_backtest_summary(result.backtest)

    assert summary["rebalance_count"] > 0
    assert summary["cost_sum"] >= 0
    assert -1 <= summary["max_drawdown"] <= 0


def test_research_cli_runs(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            str(PYTHON),
            "-m",
            "research.main",
            "--start-date",
            "2025-06-02",
            "--end-date",
            "2026-03-13",
            "--output-dir",
            str(tmp_path),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Research pipeline completed." in completed.stdout
    assert (tmp_path / "summary.csv").exists()
