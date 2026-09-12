"""自融资费用求解：扣费后净值分配的唯一数值实现（B1）。

核心方程（BUG-08 已确认的资金分配规则）：
    V_after + c × Σ|w_i × V_after − H_i| = V_before
其中 V_before 为执行日按成交价估值、扣本次费用前的组合净值，H_i 为执行前
持仓金额，w_i 为目标权重，c 为费率。残差函数在 sum(w)<=1 且 c<1 时严格递增，
区间 [0, V_before] 包含根，使用二分法求解，不依赖 scipy。
"""

from __future__ import annotations

import numpy as np

# 金额容差系数：tol = 1e-10 * max(1.0, before)
AMOUNT_TOL_FACTOR = 1e-10

_BISECT_ITERATIONS = 100


def amount_tolerance(before: float) -> float:
    """相对金额容差。"""
    return AMOUNT_TOL_FACTOR * max(1.0, float(before))


def solve_target_amounts(
    before: float,
    old: np.ndarray,
    weights: np.ndarray,
    rate: float,
) -> tuple[float, np.ndarray, np.ndarray, float]:
    """求解扣费后目标分配。

    Args:
        before: 执行日成交前组合净值（现金 + 持仓市值），必须 > 0。
        old: 执行前各证券持仓金额（按成交价估值），非负。
        weights: 目标权重，非负、有限；引擎侧已保证 sum(w) <= 1 + 1e-12。
        rate: 费率（0 <= rate < 1），买卖均按成交金额收取。

    Returns:
        (after, target, delta, fee)：
        - after：扣费后净值 V_after；
        - target：目标持仓金额 w_i × V_after；
        - delta：目标 − 执行前持仓（正=买入，负=卖出）；
        - fee：总费用 rate × Σ|delta|。

    Raises:
        ValueError: 输入非法或结算守恒校验失败。不通过剪裁负现金掩盖错误。
    """
    before = float(before)
    old = np.asarray(old, dtype=float)
    weights = np.asarray(weights, dtype=float)
    rate = float(rate)

    if not before > 0:
        raise ValueError(f"成交前净值必须为正，得到 {before!r}")
    if not 0.0 <= rate < 1.0:
        raise ValueError(f"费率必须满足 0 <= rate < 1，得到 {rate!r}")
    if old.shape != weights.shape:
        raise ValueError(f"old 与 weights 形状不一致: {old.shape} vs {weights.shape}")
    if not (np.isfinite(old).all() and np.isfinite(weights).all()):
        raise ValueError("old/weights 含非有限值")
    if (old < 0).any() or (weights < 0).any():
        raise ValueError("old/weights 必须非负")
    tol = amount_tolerance(before)
    if old.sum() > before + tol:
        raise ValueError(
            f"持仓市值 {old.sum()!r} 超过成交前净值 {before!r}（容差 {tol!r}）"
        )

    def residual(x: float) -> float:
        return x + rate * float(np.abs(weights * x - old).sum()) - before

    lo, hi = 0.0, before
    for _ in range(_BISECT_ITERATIONS):
        mid = (lo + hi) / 2.0
        if residual(mid) > 0.0:
            hi = mid
        else:
            lo = mid
    after = (lo + hi) / 2.0
    target = weights * after
    delta = target - old
    fee = rate * float(np.abs(delta).sum())

    # 结算守恒校验：容差以内的结算残差置零（费用吸收），否则报错。
    settle = before - after - fee
    if abs(settle) > tol:
        raise ValueError(
            f"结算守恒校验失败：after={after!r} + fee={fee!r} != before={before!r}"
            f"（残差 {settle!r} 超出容差 {tol!r}）"
        )
    if after - float(target.sum()) < -tol:
        raise ValueError(
            f"扣费后现金为负：after={after!r} - target.sum={float(target.sum())!r}"
        )
    return after, target, delta, fee
