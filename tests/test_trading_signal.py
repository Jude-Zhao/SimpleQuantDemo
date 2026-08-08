from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

from trading.config import default_trading_config
from trading.signal import generate_trading_signal


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)


def test_generate_trading_signal_writes_position_file(tmp_path: Path, test_db_path: Path) -> None:
    config = replace(
        default_trading_config(output_dir=tmp_path),
        db_path=test_db_path,
        start_date="2024-06-03",
        end_date="2026-03-13",
    )

    result = generate_trading_signal(config)

    assert result.signal_date == pd.Timestamp("2026-03-13")
    assert result.output_path.exists()
    assert result.positions.shape[0] == 5
    assert result.positions["weight"].sum() == 1.0
    saved = pd.read_csv(result.output_path)
    assert saved.columns.tolist() == ["sec", "weight"]


def test_trading_cli_runs(tmp_path: Path, test_db_path: Path) -> None:
    completed = subprocess.run(
        [
            str(PYTHON),
            "-m",
            "trading.main",
            "--db-path",
            str(test_db_path),
            "--start-date",
            "2024-06-03",
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

