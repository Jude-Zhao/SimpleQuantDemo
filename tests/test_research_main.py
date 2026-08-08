from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

from research.config import default_research_config
from research.main import calculate_backtest_summary, run_research


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)


def _research_config(tmp_path: Path, test_db_path: Path):
    return replace(
        default_research_config(output_dir=tmp_path),
        db_path=test_db_path,
        start_date="2024-06-03",
        end_date="2025-12-31",
    )


def test_run_research_writes_outputs(tmp_path: Path, test_db_path: Path) -> None:
    config = _research_config(tmp_path, test_db_path)

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


def test_calculate_backtest_summary_has_expected_fields(tmp_path: Path, test_db_path: Path) -> None:
    config = _research_config(tmp_path, test_db_path)
    result = run_research(config)

    summary = calculate_backtest_summary(result.backtest)

    assert summary["rebalance_count"] > 0
    assert summary["cost_sum"] >= 0
    assert -1 <= summary["max_drawdown"] <= 0
    # Performance metrics are delegated to quantstats.
    assert "sortino" in summary.index
    assert "calmar" in summary.index
    assert summary["sharpe"] == summary["sharpe"]  # not NaN on a valid run


def test_research_cli_runs(tmp_path: Path, test_db_path: Path) -> None:
    completed = subprocess.run(
        [
            str(PYTHON),
            "-m",
            "research.main",
            "--db-path",
            str(test_db_path),
            "--start-date",
            "2024-06-03",
            "--end-date",
            "2025-12-31",
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