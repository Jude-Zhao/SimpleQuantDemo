"""Equity curve visualization."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from research.visualization.style import prepare_figure, save_figure


def plot_equity_curve(equity_curve: pd.Series, output_path: Path) -> Path:
    """Plot strategy equity curve."""
    figure, axis = prepare_figure(figsize=(10, 5))
    curve = equity_curve.astype(float).dropna()
    axis.plot(curve.index, curve.values, linewidth=1.8, label="Strategy")
    axis.set_title("Equity Curve")
    axis.set_xlabel("Date")
    axis.set_ylabel("Net Value")
    axis.legend()
    return save_figure(figure, output_path)

