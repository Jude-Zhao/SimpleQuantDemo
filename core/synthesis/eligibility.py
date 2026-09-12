"""携带因子身份的类别构建与严格资格检查（B3 / OPT-03）。

规则（需求2 已确认，不得擅自放宽）：
- 只考察实际启用且权重/指数 > 0 的类别（由 faa_composite / eaa_composite 保证）；
- 当日全部必需因子为有限数值才有资格：NaN（missing）与 ±inf（non_finite）
  均不合格，不把异常当 0；
- 类内均值要求该类别所有因子实例当日有限，任一无效则该类别得分 NaN；
- FAA/EAA 跨启用类别取有效性 AND，不用 add(fill_value=0) / mul(fill_value=1)
  抹掉缺失；
- 禁用类别（权重/指数为 0）的 issues 不影响本次资格，也不参与合成。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core.factors.config import FactorCategory
from core.factors.utils import validate_factor_matrix
from core.synthesis.faa_eaa import Resolver, build_category_factors, normalize_cross_section

# 资格明细表固定列契约（date 为 Timestamp，params 为 dict 或 None）
ISSUE_COLUMNS = ["date", "sec", "category", "factor", "instance_index", "params", "reason"]


@dataclass(frozen=True)
class CategoryBuildResult:
    """类别得分与资格明细。

    scores: {category_key: date×sec 得分矩阵}，无效 (date, sec) 单元为 NaN；
    issues: 全部无效实例明细（含同名不同参数实例，逐条记录）。
    """

    scores: dict[str, pd.DataFrame]
    issues: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=ISSUE_COLUMNS))


def _issue_rows_for_matrix(
    matrix: pd.DataFrame,
    category_key: str,
    instance_index: int,
    instance_name: str,
    instance_params: dict,
) -> list[dict]:
    """对一个原始因子矩阵逐单元检查有限性，产出 issues 行。

    NaN → reason="missing"；±inf → reason="non_finite"。检查发生在归一化前。
    """
    arr = matrix.to_numpy(dtype=float)
    rows: list[dict] = []
    nan_pos = zip(*np.where(np.isnan(arr)))
    inf_pos = zip(*np.where(np.isinf(arr)))
    for i, j in nan_pos:
        rows.append(
            {
                "date": matrix.index[i],
                "sec": str(matrix.columns[j]),
                "category": category_key,
                "factor": instance_name,
                "instance_index": instance_index,
                "params": dict(instance_params),
                "reason": "missing",
            }
        )
    for i, j in inf_pos:
        rows.append(
            {
                "date": matrix.index[i],
                "sec": str(matrix.columns[j]),
                "category": category_key,
                "factor": instance_name,
                "instance_index": instance_index,
                "params": dict(instance_params),
                "reason": "non_finite",
            }
        )
    return rows


def _align_axes(matrices: list[pd.DataFrame], category_key: str) -> list[pd.DataFrame]:
    """校验并统一矩阵轴：证券列必须完全一致；日期对齐到首个矩阵（缺失日期
    产生 NaN，走 missing 逻辑，不静默删证券）。"""
    ref = matrices[0]
    ref_cols = [str(c) for c in ref.columns]
    aligned: list[pd.DataFrame] = []
    for idx, m in enumerate(matrices):
        cols = [str(c) for c in m.columns]
        if cols != ref_cols:
            raise ValueError(
                f"类别 '{category_key}' 第 {idx} 个因子实例的证券列与统一轴不一致，"
                "不能静默删证券"
            )
        out = m.copy()
        out.columns = cols
        if not out.index.equals(ref.index):
            out = out.reindex(ref.index)
        aligned.append(out)
    return aligned


def build_category_scores_with_details(
    price_data: pd.DataFrame,
    universe: list[str],
    categories: tuple[FactorCategory, ...],
    resolver: Resolver | None = None,
) -> CategoryBuildResult:
    """构建每个非空类别的得分矩阵，并产出全部资格明细。

    类别得分 = 类内各因子实例归一化后的等权平均；任一实例当日无效则该
    (date, sec) 类别得分为 NaN。归一化 min/max 使用当日有限数值（inf 先按
    NaN 处理再归一化，其单元仍判为不合格），完整数据时公式与旧实现完全一致。
    """
    issue_rows: list[dict] = []
    scores: dict[str, pd.DataFrame] = {}

    for cat in categories:
        if cat.is_empty:
            continue
        matrices = build_category_factors(price_data, universe, cat, resolver=resolver)
        matrices = _align_axes(matrices, cat.key)

        # 归一化前检查原矩阵有限性并记录 issues（含 inf 单元的归一化保护）。
        work: list[pd.DataFrame] = []
        for instance_index, (inst, m) in enumerate(zip(cat.factors, matrices)):
            try:
                validate_factor_matrix(m, universe, name=f"{cat.key}:{inst.name}")
            except Exception as exc:
                raise ValueError(f"类别 '{cat.key}' 因子 '{inst.name}' 矩阵非法: {exc}") from exc
            issue_rows.extend(
                _issue_rows_for_matrix(m, cat.key, instance_index, inst.name, dict(inst.params))
            )
            # inf 参与截面 min/max 会污染同行有限单元，先替换为 NaN；
            # 该单元的资格由原矩阵有限性判定（non_finite）。
            work.append(m.replace([np.inf, -np.inf], np.nan))

        normed = [normalize_cross_section(m) for m in work]
        stack = np.stack([n.to_numpy(dtype=float) for n in normed], axis=0)
        all_valid = ~np.isnan(stack).any(axis=0)
        count = stack.shape[0]
        with np.errstate(invalid="ignore"):
            mean_vals = np.where(all_valid, np.where(all_valid, stack, 0.0).sum(axis=0) / count, np.nan)
        score = pd.DataFrame(mean_vals, index=normed[0].index, columns=normed[0].columns)
        scores[cat.key] = score

    issues = (
        pd.DataFrame(issue_rows, columns=ISSUE_COLUMNS)
        if issue_rows
        else pd.DataFrame(columns=ISSUE_COLUMNS)
    )
    return CategoryBuildResult(scores=scores, issues=issues)
