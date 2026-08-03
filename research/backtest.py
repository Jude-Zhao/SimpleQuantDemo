"""Backtest engine (re-exported from core layer).

For backwards compatibility, research code can keep importing from
``research.backtest``. The actual implementation lives in
``core.backtest.engine``.
"""

from __future__ import annotations

from core.backtest.engine import BacktestConfig, BacktestResult, run_backtest

__all__ = ["BacktestConfig", "BacktestResult", "run_backtest"]