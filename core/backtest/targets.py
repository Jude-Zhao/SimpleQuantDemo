"""稀疏目标权重与决策日志构建（B3）。

业务入口（Web/研究/调参）负责生成因子得分矩阵；本模块在全部计划调仓决策日
检查合格数量：不足 Top N 记录跳过且不生成目标行；足够时调用既有优化器生成
稀疏目标行。decision_log 记录每个计划决策日的资格明细（含全部无效实例），
供 Web 历史报告与复核使用。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.backtest.models import BacktestConfig, TargetPlan
from core.calendar import generate_rebalance_dates
from core.optimization import EqualWeightOptimizer, OptimizationError, ScoreWeightedOptimizer

# decision_issues / exclusions 的固定列契约
ISSUE_COLUMNS = ["date", "sec", "category", "factor", "instance_index", "params", "reason"]


def _build_optimizer(config: BacktestConfig):
    """按 weight_mode 构建选股优化器（不接收组合约束——约束仅提示）。"""
    if config.weight_mode == "score":
        return ScoreWeightedOptimizer(
            top_n=config.top_n,
            max_weight=config.max_weight,
            min_weight=config.min_weight,
        )
    return EqualWeightOptimizer(
        top_n=config.top_n,
        max_weight=config.max_weight,
        min_weight=config.min_weight,
    )


def _score_row(scores: pd.DataFrame, date: pd.Timestamp) -> pd.Series:
    """取某决策日得分行；scores 缺该日时返回全 NaN 行（资格为 0）。"""
    if date in scores.index:
        return scores.loc[date].astype(float)
    return pd.Series(np.nan, index=scores.columns, dtype=float)


def _collect_exclusions(
    date: pd.Timestamp,
    row: pd.Series,
    decision_issues: pd.DataFrame | None,
    *,
    non_positive_excluded: bool = False,
) -> list[dict]:
    """汇总该决策日全部不合格标的及原因。

    - 优先取 decision_issues 中该日的全部无效实例行（同名不同参数实例逐条保留）；
    - 得分非有限（NaN/±inf）且无 issue 记录的证券补记原因（NaN 为
      "score_missing"，±inf 为 "score_non_finite"），不静默丢原因；
    - ``non_positive_excluded``（score 模式）时，有限但 <=0 的得分同样
      不合格（无法参与得分占比加权），补记 reason="score_non_positive"。
    """
    exclusions: list[dict] = []
    if decision_issues is not None and len(decision_issues) > 0:
        sub = decision_issues[decision_issues["date"] == date]
        for rec in sub.itertuples(index=False):
            exclusions.append(
                {
                    "sec": str(rec.sec),
                    "category": None if rec.category is None else str(rec.category),
                    "factor": None if rec.factor is None else str(rec.factor),
                    "instance_index": None if rec.instance_index is None else int(rec.instance_index),
                    "params": dict(rec.params) if isinstance(rec.params, dict) else rec.params,
                    "reason": str(rec.reason),
                }
            )
    covered = {e["sec"] for e in exclusions}
    values = row.to_numpy(dtype=float)
    for sec, value in zip(row.index, values):
        if str(sec) in covered or np.isfinite(value) and value > 0:
            continue
        if np.isnan(value):
            reason = "score_missing"
        elif np.isinf(value):
            reason = "score_non_finite"
        elif non_positive_excluded:
            reason = "score_non_positive"
        else:
            # equal 模式下有限值（含 0/负分）是合格候选，不记排除
            continue
        exclusions.append(
            {
                "sec": str(sec),
                "category": None,
                "factor": None,
                "instance_index": None,
                "params": None,
                "reason": reason,
            }
        )
    exclusions.sort(key=lambda e: (e["sec"], e["category"] or "", e["factor"] or "", e["instance_index"] or 0))
    return exclusions


def build_target_weights(
    scores: pd.DataFrame,
    trading_dates: pd.DatetimeIndex,
    config: BacktestConfig,
    decision_issues: pd.DataFrame | None = None,
) -> TargetPlan:
    """在全部计划调仓决策日构建稀疏目标权重与决策日志。

    Args:
        scores: date × sec 得分矩阵（如 FAA/EAA 合成得分）。
        trading_dates: 回测范围内全部交易日（价格日期并集，不得与有效因子
            日期做 intersection 删除缺信号日期）。
        config: 选股/调度配置。
        decision_issues: 可选的因子资格明细表（列见 ISSUE_COLUMNS），
            来自 core.synthesis.eligibility.build_category_scores_with_details。

    Returns:
        TargetPlan：稀疏目标矩阵（仅 target_created 日期有行）、全部计划
        决策日、逐日决策日志。
    """
    if not isinstance(scores, pd.DataFrame):
        raise ValueError("scores 必须是 DataFrame")
    if scores.columns.empty:
        raise ValueError("scores 缺少证券列")
    if scores.columns.has_duplicates:
        raise ValueError("scores 存在重复证券列")

    trading_dates = pd.DatetimeIndex(pd.to_datetime(list(trading_dates))).normalize().sort_values().unique()
    rebalance_dates = generate_rebalance_dates(
        trading_dates=trading_dates,
        rebalance_freq=config.rebalance_freq,  # type: ignore[arg-type]
        rebalance_day=config.rebalance_day,
    )

    optimizer = _build_optimizer(config)
    target_rows: dict[pd.Timestamp, pd.Series] = {}
    decision_log: list[dict] = []

    for date in rebalance_dates:
        row = _score_row(scores, date)
        # 资格标准的唯一定义处（与优化器口径一致，OPT-03：非有限值不合格）：
        # - equal 模式：有限值即合格（Top N 语义为"得分最高的 N 个"，0/负分
        #   仍可入选，保持单因子评估轮动的既有行为）；
        # - score 模式：有限且 >0（ScoreWeightedOptimizer 按得分占比加权，
        #   非正得分无法参与），避免 eligible_count 与优化器口径分裂导致
        #   优化器抛错而非按 OPT-03 记录 skipped_insufficient。
        values = row.to_numpy(dtype=float)
        finite_mask = np.isfinite(values)
        if config.weight_mode == "score":
            eligible_mask = finite_mask & (values > 0)
        else:
            eligible_mask = finite_mask
        eligible_count = int(eligible_mask.sum())
        exclusions = _collect_exclusions(
            date, row, decision_issues, non_positive_excluded=config.weight_mode == "score"
        )

        if eligible_count < config.top_n:
            decision_log.append(
                {
                    "decision_date": date,
                    "eligible_count": eligible_count,
                    "top_n": config.top_n,
                    "status": "skipped_insufficient",
                    "exclusions": exclusions,
                }
            )
            continue

        try:
            weights_row = optimizer.optimize(row[eligible_mask]).reindex(
                scores.columns, fill_value=0.0
            )
        except OptimizationError as exc:
            raise OptimizationError(
                f"决策日 {date.date()} 合格证券不足以生成目标组合: {exc}"
            ) from exc

        target_rows[date] = weights_row.astype(float)
        decision_log.append(
            {
                "decision_date": date,
                "eligible_count": eligible_count,
                "top_n": config.top_n,
                "status": "target_created",
                "exclusions": exclusions,
            }
        )

    if target_rows:
        target_weights = pd.DataFrame.from_dict(target_rows, orient="index", columns=list(scores.columns))
        target_weights = target_weights.sort_index()
        target_weights.index.name = "date"
    else:
        target_weights = pd.DataFrame(
            np.zeros((0, len(scores.columns))), index=pd.DatetimeIndex([], name="date"), columns=list(scores.columns)
        )

    return TargetPlan(
        target_weights=target_weights,
        rebalance_dates=rebalance_dates,
        decision_log=decision_log,
    )
