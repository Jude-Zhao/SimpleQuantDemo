"""Factor IC/RankIC/ICIR visualization."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from research.visualization.style import prepare_figure, save_figure


def plot_factor_stats(
    ic_data: dict[str, pd.Series],
    rank_ic_data: dict[str, pd.Series],
    icir_data: dict[str, pd.Series],
    output_path: Path,
) -> Path:
    """Plot IC, RankIC, and ICIR time series for all factors."""
    figure, axes = prepare_factor_figure()
    _plot_series_dict(axes[0], ic_data, "IC")
    _plot_series_dict(axes[1], rank_ic_data, "RankIC")
    _plot_series_dict(axes[2], icir_data, "ICIR")
    return save_figure(figure, output_path)


def prepare_factor_figure():
    import matplotlib.pyplot as plt

    plt.rcParams["axes.unicode_minus"] = False
    figure, axes = plt.subplots(3, 1, figsize=(10, 8), dpi=120, sharex=True)
    for axis in axes:
        axis.grid(True, alpha=0.25)
    return figure, axes


def _plot_series_dict(axis, series_dict: dict[str, pd.Series], title: str) -> None:
    for name, series in series_dict.items():
        values = series.astype(float).dropna()
        axis.plot(values.index, values.values, linewidth=1.2, label=name)
    axis.axhline(0, color="black", linewidth=0.8, alpha=0.35)
    axis.set_title(title)
    if series_dict:
        axis.legend(fontsize=8)

