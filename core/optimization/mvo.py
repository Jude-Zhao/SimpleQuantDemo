"""Mean-variance portfolio optimizer (MVO)."""

from __future__ import annotations

from typing import Literal, Optional

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from core.optimization.base import PortfolioOptimizer
from core.optimization.constraints import OptimizationConstraints, validate_constraints
from core.optimization.exceptions import OptimizationError

ObjectiveType = Literal["min_variance", "max_sharpe", "target_return"]


class MVOptimizer(PortfolioOptimizer):
    """Mean-variance optimizer built on scipy.optimize.minimize.

    Solves for the weight vector that optimizes a chosen objective subject to
    full-investment (weights sum to 1), per-security bounds, and optional
    per-category weight bounds.

    Objectives:
        - ``min_variance``: minimise portfolio variance ``w'Σw``.
        - ``max_sharpe``: maximise ``(μ'w - rf) / sqrt(w'Σw)``.
        - ``target_return``: minimise variance subject to ``μ'w >= target_return``.
    """

    def __init__(
        self,
        objective: ObjectiveType = "max_sharpe",
        max_weight: float = 0.5,
        min_weight: float = 0.0,
        risk_free_rate: float = 0.0,
        target_return: float | None = None,
        constraints: OptimizationConstraints | None = None,
    ) -> None:
        if objective not in ("min_variance", "max_sharpe", "target_return"):
            raise ValueError(f"Unknown objective: {objective}")
        if not 0 <= min_weight <= max_weight <= 1:
            raise ValueError("weight constraints must satisfy 0 <= min_weight <= max_weight <= 1.")
        if objective == "target_return" and target_return is None:
            raise ValueError("target_return is required for objective='target_return'.")

        self.objective = objective
        self.max_weight = max_weight
        self.min_weight = min_weight
        self.risk_free_rate = risk_free_rate
        self.target_return = target_return
        self.constraints = constraints

    def optimize(
        self,
        expected_returns: pd.Series,
        cov_matrix: pd.DataFrame | None = None,
        max_weight: float | None = None,
        min_weight: float | None = None,
        classifications: dict[str, dict[str, str]] | None = None,
    ) -> pd.Series:
        """Return target weights indexed by security code.

        Args:
            expected_returns: Expected return per security.
            cov_matrix: Covariance matrix indexed by security code. If omitted,
                a diagonal approximation based on return dispersion is used.
            max_weight: Optional per-security maximum weight override.
            min_weight: Optional per-security minimum weight override.
            classifications: ``{sec_code: {category_key: category_value}}``
                used to resolve category membership for weight bounds.
        """
        if expected_returns is None or expected_returns.empty:
            raise OptimizationError("expected_returns is empty.")

        rets = expected_returns.copy()
        rets.index = rets.index.astype(str).str.strip().str.upper()
        if rets.index.has_duplicates:
            raise OptimizationError(
                f"expected_returns contains duplicate securities: "
                f"{rets.index[rets.index.duplicated()].unique().tolist()}"
            )

        assets = list(rets.index)
        n = len(assets)
        resolved_max = self.max_weight if max_weight is None else max_weight
        resolved_min = self.min_weight if min_weight is None else min_weight

        if cov_matrix is not None:
            cov = cov_matrix.copy()
            cov.index = cov.index.astype(str).str.strip().str.upper()
            cov.columns = cov.columns.astype(str).str.strip().str.upper()
            # Align with assets; missing rows/cols treated as zero covariance
            cov = cov.reindex(index=assets, columns=assets, fill_value=0.0)
            cov_np = cov.to_numpy(dtype=float)
        else:
            # Diagonal approximation: use variance of returns as a simple proxy.
            std = rets.abs().clip(lower=1e-6)
            cov_np = np.diag(std.to_numpy(dtype=float))

        mu = rets.to_numpy(dtype=float)
        mu = np.nan_to_num(mu, nan=0.0)

        # Initial guess: equal weights (respecting bounds if feasible).
        x0 = np.full(n, 1.0 / n)

        bounds = [(resolved_min, resolved_max)] * n

        constraints_list: list[dict] = [
            {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}
        ]

        if self.constraints is not None and classifications:
            constraints_list.extend(
                self._category_constraints(classifications, assets, n)
            )

        # Objective
        if self.objective == "min_variance":
            fun = lambda w: float(w @ cov_np @ w)
        elif self.objective == "max_sharpe":
            def fun(w: np.ndarray) -> float:
                var = float(w @ cov_np @ w)
                denom = np.sqrt(var) if var > 0 else 1e-8
                ret = float(mu @ w) - self.risk_free_rate
                return -ret / denom
        else:  # target_return
            def fun(w: np.ndarray) -> float:
                return float(w @ cov_np @ w)
            if self.target_return is not None:
                constraints_list.append(
                    {
                        "type": "ineq",
                        "fun": lambda w: float(mu @ w) - self.target_return,
                    }
                )

        result = minimize(
            fun,
            x0=x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints_list,
            options={"maxiter": 1000, "ftol": 1e-9},
        )

        if not result.success:
            # Fallback: try a few random starts before giving up.
            best = result
            for _ in range(5):
                rng = np.random.default_rng(_)
                start = rng.dirichlet(np.ones(n))
                trial = minimize(
                    fun,
                    x0=start,
                    method="SLSQP",
                    bounds=bounds,
                    constraints=constraints_list,
                    options={"maxiter": 1000, "ftol": 1e-9},
                )
                if trial.success and trial.fun < best.fun:
                    best = trial
            result = best

        if not result.success:
            raise OptimizationError(
                f"Optimization failed: {result.message}"
            )

        weights = pd.Series(result.x, index=assets, name="weight")
        # Small numerical cleanup
        weights = weights.clip(lower=0.0)
        total = weights.sum()
        if total <= 0:
            raise OptimizationError("Optimizer returned a zero-weight portfolio.")
        weights = weights / total
        weights = weights.round(8)

        # Safety-net validation: confirm the optimized portfolio satisfies
        # the configured constraints (in case the solver silently ignored one).
        if self.constraints is not None and classifications:
            violations = validate_constraints(
                weights.to_dict(),
                classifications,
                self.constraints,
            )
            if violations:
                details = "; ".join(f"{v.constraint}: {v.message}" for v in violations)
                raise OptimizationError(
                    f"Optimized portfolio violates constraints: {details}"
                )

        return weights

    def _category_constraints(
        self,
        classifications: dict[str, dict[str, str]],
        assets: list[str],
        n: int,
    ) -> list[dict]:
        """Build scipy inequality constraints for category weight bounds.

        Returns constraints of the form:
            sum(w_i for i in category) - min_weight >= 0
            max_weight - sum(w_i for i in category) >= 0
        """
        result: list[dict] = []
        if not self.constraints:
            return result

        for cat in self.constraints.category_constraints:
            members = [
                i
                for i, sec in enumerate(assets)
                if classifications.get(sec, {}).get(cat.category_key) == cat.category_value
            ]
            if not members:
                continue

            idx = np.array(members, dtype=int)

            if cat.min_weight is not None:
                result.append(
                    {
                        "type": "ineq",
                        "fun": lambda w, idx=idx, m=cat.min_weight: float(
                            np.sum(w[idx]) - m
                        ),
                    }
                )
            if cat.max_weight is not None:
                result.append(
                    {
                        "type": "ineq",
                        "fun": lambda w, idx=idx, m=cat.max_weight: float(
                            m - np.sum(w[idx])
                        ),
                    }
                )
        return result