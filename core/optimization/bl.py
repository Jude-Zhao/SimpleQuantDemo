"""Black-Litterman posterior estimation and portfolio optimizer."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.optimization.constraints import OptimizationConstraints
from core.optimization.exceptions import OptimizationError
from core.optimization.mvo import MVOptimizer, ObjectiveType


@dataclass
class View:
    """A single Black-Litterman view.

    ``assets`` holds the participating asset codes with weights summing to 1
    (e.g. [("A.SH", 0.5), ("B.SH", 0.5)] for relative outperformance).
    ``q`` is the expected return of the portfolio described by the view.
    ``confidence`` is the confidence level [0, 1] used to scale the view
    uncertainty.
    """

    assets: list[tuple[str, float]]
    q: float
    confidence: float = 1.0


class BlackLittermanModel:
    """Compute Black-Litterman posterior expected returns and covariance.

    Reference formulas:
        posterior mean  E[R] = (Sigma^-1 + P^T Omega^-1 P)^-1 (Sigma^-1 Pi + P^T Omega^-1 Q)
        posterior var   M   = Sigma + (Sigma^-1 + P^T Omega^-1 P)^-1
    """

    def __init__(
        self,
        market_caps: pd.Series,
        cov_matrix: pd.DataFrame,
        views: list[View] | None = None,
        tau: float = 0.05,
    ) -> None:
        """Initialize the BL model.

        Args:
            market_caps: Market capitalisation per asset (used to derive the
                equilibrium prior Pi from the reverse-optimisation formula).
            cov_matrix: Asset covariance matrix.
            views: Optional list of views. If empty, posterior defaults to the
                prior (pure market equilibrium).
            tau: Scalar controlling the uncertainty of the prior. Typical
                range is 0.01-0.10.
        """
        if market_caps is None or market_caps.empty:
            raise OptimizationError("market_caps is empty.")
        if cov_matrix is None or cov_matrix.empty:
            raise OptimizationError("cov_matrix is empty.")

        caps = market_caps.copy()
        caps.index = caps.index.astype(str).str.strip().str.upper()
        cov = cov_matrix.copy()
        cov.index = cov.index.astype(str).str.strip().str.upper()
        cov.columns = cov.columns.astype(str).str.strip().str.upper()

        # Align to common asset universe
        common = caps.index.intersection(cov.index).intersection(cov.columns)
        if len(common) == 0:
            raise OptimizationError(
                "market_caps and cov_matrix have no common assets."
            )
        caps = caps.loc[common]
        cov = cov.loc[common, common]

        self.market_caps = caps
        self.cov = cov
        self.views = views or []
        self.tau = tau or 0.05

        n = len(common)
        self._n = n
        self._assets = list(common)

        # Market weights
        w_mkt_np = (caps / caps.sum()).to_numpy(dtype=float)

        # Risk aversion lambda: derived from a global maximum-Sharpe
        # normalisation. lambda = (E[r_mkt] - rf) / sigma^2_mkt.
        lam = 2.5

        sigma_np = cov.to_numpy(dtype=float)
        try:
            sigma_inv = np.linalg.inv(sigma_np)
        except np.linalg.LinAlgError:
            sigma_inv = np.linalg.pinv(sigma_np)

        self._sigma = sigma_np
        self._sigma_inv = sigma_inv
        self._w_mkt = w_mkt_np
        self._prior_excess = lam * sigma_inv @ w_mkt_np

    def _build_views(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Build P (view portfolio matrix), Q (view returns), Omega (uncertainty)."""
        n = self._n
        assets = self._assets
        views = self.views

        if not views:
            return (
                np.zeros((0, n)),
                np.zeros(0),
                np.zeros((0, 0)),
            )

        P_list = []
        Q_list = []
        Omega_list = []

        for v in views:
            p = np.zeros(n)
            total_abs = sum(abs(w) for _, w in v.assets)
            if total_abs <= 0:
                continue
            for sec, w in v.assets:
                sec_key = sec.strip().upper()
                if sec_key not in assets:
                    raise OptimizationError(f"View asset not in universe: {sec}")
                p[assets.index(sec_key)] = w
            P_list.append(p)
            Q_list.append(v.q)
            conf = max(min(v.confidence, 1.0), 0.0)
            uncertainty_scale = (1.0 - conf) + 1e-6
            omega = self.tau * uncertainty_scale * float(p @ self._sigma @ p)
            Omega_list.append(omega)

        P = np.array(P_list, dtype=float).reshape(-1, n)
        Q = np.array(Q_list, dtype=float)
        Omega = np.diag(Omega_list) if Omega_list else np.zeros((0, 0))
        return P, Q, Omega

    def posterior(self) -> tuple[pd.Series, pd.DataFrame]:
        """Return (posterior mean, posterior covariance) for the universe."""
        P, Q, Omega = self._build_views()
        sigma_inv = self._sigma_inv
        prior = self._prior_excess

        if P.shape[0] == 0:
            # No views => posterior equals prior.
            post_mean = pd.Series(prior, index=self._assets, name="posterior_return")
            post_cov = pd.DataFrame(
                self._sigma, index=self._assets, columns=self._assets
            )
            return post_mean, post_cov

        omega_inv = np.linalg.inv(Omega)

        # Posterior precision matrix
        post_prec = sigma_inv + P.T @ omega_inv @ P
        try:
            post_cov_np = np.linalg.inv(post_prec)
        except np.linalg.LinAlgError:
            post_cov_np = np.linalg.pinv(post_prec)

        post_mean_np = post_cov_np @ (sigma_inv @ prior + P.T @ omega_inv @ Q)

        post_mean = pd.Series(post_mean_np, index=self._assets, name="posterior_return")
        post_cov = pd.DataFrame(
            post_cov_np, index=self._assets, columns=self._assets
        )
        return post_mean, post_cov


class BLOptimizer:
    """Black-Litterman portfolio optimizer.

    Estimates posterior expected returns/covariance with the BL model and
    solves the resulting mean-variance problem with :class:`MVOptimizer`.
    """

    def __init__(
        self,
        market_caps: pd.Series,
        cov_matrix: pd.DataFrame,
        views: list[View] | None = None,
        tau: float = 0.05,
        objective: ObjectiveType = "max_sharpe",
        max_weight: float = 0.5,
        min_weight: float = 0.0,
        constraints: OptimizationConstraints | None = None,
    ) -> None:
        self.model = BlackLittermanModel(
            market_caps=market_caps,
            cov_matrix=cov_matrix,
            views=views,
            tau=tau,
        )
        self.mvo = MVOptimizer(
            objective=objective,
            max_weight=max_weight,
            min_weight=min_weight,
            constraints=constraints,
        )

    def optimize(
        self,
        classifications: dict[str, dict[str, str]] | None = None,
    ) -> pd.Series:
        """Return target weights from the BL posterior estimates."""
        post_mean, post_cov = self.model.posterior()
        return self.mvo.optimize(
            expected_returns=post_mean,
            cov_matrix=post_cov,
            classifications=classifications,
        )