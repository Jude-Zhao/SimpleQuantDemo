"""Matplotlib style helpers."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt


def prepare_figure(figsize: tuple[float, float] = (10, 5)):
    """Create a figure and axis with project-default styling."""
    plt.rcParams["axes.unicode_minus"] = False
    figure, axis = plt.subplots(figsize=figsize, dpi=120)
    axis.grid(True, alpha=0.25)
    return figure, axis


def save_figure(figure, output_path: Path) -> Path:
    """Save and close a matplotlib figure."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(output_path, bbox_inches="tight")
    plt.close(figure)
    return output_path

