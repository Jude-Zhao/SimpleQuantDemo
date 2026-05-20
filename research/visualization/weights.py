"""Portfolio weight visualization."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from research.visualization.style import prepare_figure, save_figure


def plot_latest_weights(weights: pd.DataFrame, output_path: Path) -> Path:
    """Plot the latest non-zero portfolio weights."""
    figure, axis = prepare_figure(figsize=(9, 5))
    latest = weights[weights.sum(axis=1) > 0].iloc[-1]
    latest = latest[latest > 0].sort_values(ascending=True)
    axis.barh(latest.index.astype(str), latest.values)
    axis.set_title("Latest Portfolio Weights")
    axis.set_xlabel("Weight")
    axis.set_xlim(0, max(float(latest.max()) * 1.15, 0.01))
    return save_figure(figure, output_path)

