from __future__ import annotations

from pathlib import Path

import pandas as pd

from research.visualization import plot_equity_curve, plot_factor_stats, plot_latest_weights


def _assert_nonempty_png(path: Path) -> None:
    assert path.exists()
    assert path.stat().st_size > 0
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_plot_equity_curve(tmp_path: Path) -> None:
    equity = pd.Series(
        [1.0, 1.1, 1.05],
        index=pd.date_range("2026-01-01", periods=3, freq="D"),
        name="equity",
    )

    output_path = plot_equity_curve(equity, tmp_path / "equity.png")

    _assert_nonempty_png(output_path)


def test_plot_factor_stats(tmp_path: Path) -> None:
    index = pd.date_range("2026-01-01", periods=3, freq="D")
    data = {
        "factor_a": pd.Series([0.1, -0.1, 0.2], index=index),
        "factor_b": pd.Series([0.0, 0.2, 0.1], index=index),
    }

    output_path = plot_factor_stats(data, data, data, tmp_path / "factor_stats.png")

    _assert_nonempty_png(output_path)


def test_plot_latest_weights(tmp_path: Path) -> None:
    weights = pd.DataFrame(
        [[0.0, 0.0, 0.0], [0.2, 0.3, 0.5]],
        index=pd.date_range("2026-01-01", periods=2, freq="D"),
        columns=["A.SH", "B.SH", "C.SH"],
    )

    output_path = plot_latest_weights(weights, tmp_path / "weights.png")

    _assert_nonempty_png(output_path)

