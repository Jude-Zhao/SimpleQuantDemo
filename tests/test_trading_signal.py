from __future__ import annotations

import subprocess
from pathlib import Path

import pandas as pd

from trading.config import default_trading_config
from trading.signal import generate_trading_signal


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(r"D:\Coding\APPS\Miniconda3Py38_4.9.2\envs\QuantitativeTrading\python.exe")


def test_generate_trading_signal_writes_position_file(tmp_path: Path) -> None:
    config = default_trading_config(output_dir=tmp_path)
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
        top_n=config.top_n,
        max_weight=config.max_weight,
        min_weight=config.min_weight,
    )

    result = generate_trading_signal(config)

    assert result.signal_date == pd.Timestamp("2026-03-13")
    assert result.output_path.exists()
    assert result.positions.shape[0] == 5
    assert result.positions["weight"].sum() == 1.0
    saved = pd.read_csv(result.output_path)
    assert saved.columns.tolist() == ["sec", "weight"]


def test_trading_cli_runs(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            str(PYTHON),
            "-m",
            "trading.main",
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

    assert "Signal date: 2026-03-13" in completed.stdout
    assert list(tmp_path.glob("position_20260313.csv"))

