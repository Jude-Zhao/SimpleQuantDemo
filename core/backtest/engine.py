"""唯一权威组合执行账本（OPT-01 / B2）。

时间轴（需求1 BUG-08 已确认规则）：
- T 收盘产生稀疏目标行（决策日）；T+1 个交易日收盘执行（T+1 指下一个输入
  交易日，不是自然日加一天）；T+1 收盘前旧持仓承担收益，新持仓首次影响
  T+2 收盘收益行。
- 执行日先对整个方案校验（需要交易的证券全部有有效报价），再先卖后买；
  任一需要交易的证券缺有效成交价则整次取消、原仓保留、无费用、不重试。
- 买卖均按实际成交金额收取同一比例费率；允许分数份额；目标金额按扣费后
  净值分配（solve_target_amounts），现金不得为负。
- 份额在非调仓期间保持不变（BUG-07）；缺价持仓按最后有效价估值，恢复报价
  后完整计入价格变化（BUG-09）。
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from core.backtest.execution import amount_tolerance, solve_target_amounts
from core.backtest.models import BacktestConfig, BacktestResult, ExecutionConfig
from core.backtest.targets import build_target_weights
from core.calendar import get_trading_dates
from core.factors.utils import pivot_price_field

TRADES_COLUMNS = [
    "decision_date",
    "execution_date",
    "sec",
    "side",
    "price",
    "quantity",
    "notional",
    "fee",
]

# 单行权重和的浮点超额容差：仅 <=1e-12 的超额按原比例归一到 1
_WEIGHT_SUM_TOL = 1e-12


# ── 输入校验（数据契约，见 docs/统一回测与绩效框架.md）──────────────────

def _validate_close(close: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(close, pd.DataFrame):
        raise ValueError("close 必须是 DataFrame")
    if close.empty or close.columns.empty:
        raise ValueError("close 不能为空")
    if not isinstance(close.index, pd.DatetimeIndex):
        raise ValueError("close 索引必须是 DatetimeIndex")
    if close.index.tz is not None:
        raise ValueError("close 索引不得携带时区")
    if close.index.has_duplicates:
        dup = close.index[close.index.duplicated()].strftime("%Y-%m-%d").tolist()[:5]
        raise ValueError(f"close 索引存在重复日期: {dup}（不能 groupby 取平均掩盖）")
    if not close.index.is_monotonic_increasing:
        raise ValueError("close 索引必须递增")
    if close.columns.has_duplicates:
        dup = close.columns[close.columns.duplicated()].tolist()[:5]
        raise ValueError(f"close 存在重复证券列: {dup}")
    try:
        close.to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"close 必须是数值矩阵: {exc}") from exc
    return close


def _validate_target_weights(target_weights: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(target_weights, pd.DataFrame):
        raise ValueError("target_weights 必须是 DataFrame")
    if list(target_weights.columns) != list(close.columns):
        raise ValueError("target_weights 列必须与 close 完全一致")
    if not isinstance(target_weights.index, pd.DatetimeIndex):
        raise ValueError("target_weights 索引必须是 DatetimeIndex")
    if target_weights.index.tz is not None:
        raise ValueError("target_weights 索引不得携带时区")
    if target_weights.index.has_duplicates:
        raise ValueError("target_weights 索引存在重复决策日")
    outside = target_weights.index.difference(close.index)
    if len(outside) > 0:
        raise ValueError(
            f"target_weights 存在不在 close 日期内的决策日: "
            f"{[str(d.date()) for d in outside[:5]]}"
        )
    if target_weights.empty:
        return target_weights.astype(float)

    arr = target_weights.to_numpy(dtype=float)
    if np.isnan(arr).any():
        raise ValueError("目标行含 NaN：部分 NaN 非法（缺行=没有新订单，全零行=明确清仓）")
    if np.isinf(arr).any():
        raise ValueError("目标行含 inf")
    if (arr < 0).any():
        raise ValueError("目标权重必须非负")
    row_sums = arr.sum(axis=1)
    bad = row_sums > 1.0 + _WEIGHT_SUM_TOL
    if bad.any():
        first = target_weights.index[np.argmax(bad)]
        raise ValueError(
            f"决策日 {first.date()} 目标权重和 {row_sums.max()!r} 超过 1（真实超配必须拒绝）"
        )
    overshoot = row_sums > 1.0
    if overshoot.any():
        # 仅容差内浮点超额：按原比例归一到 1
        tw = target_weights.astype(float).copy()
        tw.iloc[overshoot] = tw.iloc[overshoot].div(
            pd.Series(row_sums[overshoot], index=tw.index[overshoot]), axis=0
        )
        return tw
    return target_weights.astype(float)


# ── 执行账本 ───────────────────────────────────────────────────────────

def execute_backtest(
    close: pd.DataFrame,
    target_weights: pd.DataFrame,
    config: ExecutionConfig | None = None,
) -> BacktestResult:
    """以价格矩阵与稀疏目标权重驱动份额/现金账本（唯一执行入口）。

    Args:
        close: date × sec 收盘价矩阵；正有限值为有效报价，
            NaN/inf/非正值视为当日无有效报价（走缺价逻辑）。
        target_weights: 稀疏目标矩阵；索引为决策日子集（不 ffill），
            缺行=没有新订单，全零行=明确清仓。
        config: 执行配置（初始资金与费率）。

    Returns:
        BacktestResult：净值/日收益/实际权重/换手/费用/现金/份额/逐笔成交
        与执行日志。decision_log 为空、rebalance_dates 为目标行日期；
        run_backtest 负责附加完整计划决策日与资格记录。
    """
    cfg = config or ExecutionConfig()
    if not float(cfg.initial_cash) > 0:
        raise ValueError(f"initial_cash 必须为正，得到 {cfg.initial_cash!r}")
    rate = float(cfg.transaction_cost_bps) / 10000.0
    if not 0.0 <= rate < 1.0:
        raise ValueError(f"transaction_cost_bps 必须满足 0 <= bps < 10000，得到 {cfg.transaction_cost_bps!r}")

    close = _validate_close(close)
    target_weights = _validate_target_weights(target_weights, close)

    dates = close.index
    cols = list(close.columns)
    n_secs = len(cols)
    values = close.to_numpy(dtype=float)
    valid = np.isfinite(values) & (values > 0.0)

    decision_pos = {date: i for i, date in enumerate(target_weights.index)}

    shares = np.zeros(n_secs)
    cash = float(cfg.initial_cash)
    last_valid = np.full(n_secs, np.nan)
    pending: dict | None = None  # {"decision_date": ts, "weights": np.ndarray}

    equities = np.empty(len(dates))
    cash_rows = np.empty(len(dates))
    fee_rows = np.empty(len(dates))
    v_before_rows = np.empty(len(dates))
    turnover_rows = np.empty(len(dates))
    weights_mat = np.empty((len(dates), n_secs))
    holdings_mat = np.empty((len(dates), n_secs))
    trades: list[dict] = []
    execution_log: list[dict] = []

    for i, date in enumerate(dates):
        # 1. 更新当日有效报价到 last_valid；不更新缺价证券。
        valid_today = valid[i]
        last_valid = np.where(valid_today, values[i], last_valid)

        # 2. 用 last_valid 估值旧份额；份额非零但无任何历史有效价属非法状态。
        if ((shares > 0) & np.isnan(last_valid)).any():
            ghost = [cols[j] for j in range(n_secs) if shares[j] > 0 and np.isnan(last_valid[j])]
            raise ValueError(f"证券 {ghost} 持仓份额非零但从无有效价格，非法状态")
        hold_value = np.where(shares > 0, shares * np.nan_to_num(last_valid, nan=0.0), 0.0)
        v_before = cash + float(hold_value.sum())

        fee_today = 0.0
        notional_today = 0.0
        tol = amount_tolerance(v_before)

        # 3. 执行昨日决策订单（若有）。
        if pending is not None:
            row = pending["weights"]
            # 需要报价的集合 = 旧持仓 或 正目标权重（保守边界）。
            need = (shares > 0) | (row > 0)
            missing_mask = need & ~valid_today
            if missing_mask.any():
                missing_codes = [cols[j] for j in np.where(missing_mask)[0]]
                execution_log.append(
                    {
                        "decision_date": pending["decision_date"],
                        "execution_date": date,
                        "status": "cancelled_missing_price",
                        "reason": "需要交易的证券当日缺有效报价，整次取消",
                        "missing_codes": missing_codes,
                    }
                )
                pending = None
            else:
                old_amounts = np.where(shares > 0, shares * last_valid, 0.0)
                after, target, delta, fee_plan = solve_target_amounts(v_before, old_amounts, row, rate)
                if float(np.abs(delta).max()) <= tol:
                    # 全部目标差在数值容差内：合法无交易，不改变原仓。
                    execution_log.append(
                        {
                            "decision_date": pending["decision_date"],
                            "execution_date": date,
                            "status": "no_trade",
                            "reason": "目标与现仓一致，无成交",
                            "missing_codes": [],
                        }
                    )
                    pending = None
                else:
                    # 先卖后买；逐笔记录，费用按腿收取。
                    for j in np.where(delta < 0)[0]:
                        notional = -float(delta[j])
                        fee_j = rate * notional
                        cash += notional - fee_j
                        new_shares = float(target[j]) / float(values[i, j])
                        trades.append(
                            {
                                "decision_date": pending["decision_date"],
                                "execution_date": date,
                                "sec": cols[j],
                                "side": "sell",
                                "price": float(values[i, j]),
                                "quantity": abs(float(shares[j]) - new_shares),
                                "notional": notional,
                                "fee": fee_j,
                            }
                        )
                        shares[j] = new_shares
                        fee_today += fee_j
                        notional_today += notional
                    for j in np.where(delta > 0)[0]:
                        notional = float(delta[j])
                        fee_j = rate * notional
                        cash -= notional + fee_j
                        new_shares = float(target[j]) / float(values[i, j])
                        trades.append(
                            {
                                "decision_date": pending["decision_date"],
                                "execution_date": date,
                                "sec": cols[j],
                                "side": "buy",
                                "price": float(values[i, j]),
                                "quantity": abs(new_shares - float(shares[j])),
                                "notional": notional,
                                "fee": fee_j,
                            }
                        )
                        shares[j] = new_shares
                        fee_today += fee_j
                        notional_today += notional

                    # 账户守恒与现金非负校验（不剪裁掩盖错误）。
                    hold_value = np.where(shares > 0, shares * last_valid, 0.0)
                    equity_after = cash + float(hold_value.sum())
                    if abs(equity_after + fee_today - v_before) > tol:
                        raise ValueError(
                            f"{date.date()} 执行后账本不守恒: equity={equity_after!r} "
                            f"+ fee={fee_today!r} != v_before={v_before!r}"
                        )
                    if cash < -tol:
                        raise ValueError(f"{date.date()} 现金为负: {cash!r}")

                    execution_log.append(
                        {
                            "decision_date": pending["decision_date"],
                            "execution_date": date,
                            "status": "executed",
                            "reason": "按扣费后净值分配完成先卖后买",
                            "missing_codes": [],
                        }
                    )
                    pending = None

        # 4. 记录当日收盘状态。
        hold_value = np.where(shares > 0, shares * last_valid, 0.0)
        equity = cash + float(hold_value.sum())
        if equity <= 0:
            raise ValueError(f"{date.date()} 净值非正: {equity!r}")
        equities[i] = equity
        cash_rows[i] = cash
        fee_rows[i] = fee_today
        v_before_rows[i] = v_before
        turnover_rows[i] = notional_today / v_before if notional_today > 0 else 0.0
        weights_mat[i] = hold_value / equity
        holdings_mat[i] = shares

        # 5. 读取当日稀疏目标行，安排到下一输入交易日执行。
        if date in decision_pos:
            if pending is not None:
                raise ValueError(f"{date.date()} 已有待执行方案，同一日只能产生一个待执行方案")
            if i == len(dates) - 1:
                execution_log.append(
                    {
                        "decision_date": date,
                        "execution_date": None,
                        "status": "unexecuted_end",
                        "reason": "末日信号没有后续交易日，不制造范围外成交",
                        "missing_codes": [],
                    }
                )
            else:
                pending = {
                    "decision_date": date,
                    "weights": target_weights.iloc[decision_pos[date]].to_numpy(dtype=float),
                }

    equity_curve = pd.Series(equities / float(cfg.initial_cash), index=dates, name="equity")
    daily_returns = equity_curve.pct_change()
    daily_returns.iloc[0] = 0.0
    daily_returns = daily_returns.fillna(0.0).rename("daily_return")

    weights_df = pd.DataFrame(weights_mat, index=dates, columns=cols)
    weights_df.index.name = "date"
    holdings_df = pd.DataFrame(holdings_mat, index=dates, columns=cols)
    holdings_df.index.name = "date"

    trades_df = pd.DataFrame(trades, columns=TRADES_COLUMNS)
    if not trades_df.empty:
        trades_df = trades_df.astype(
            {"price": float, "quantity": float, "notional": float, "fee": float}
        )

    return BacktestResult(
        equity_curve=equity_curve,
        daily_returns=daily_returns,
        weights=weights_df,
        turnover=pd.Series(turnover_rows, index=dates, name="turnover"),
        costs=pd.Series(
            np.divide(fee_rows, v_before_rows, out=np.zeros_like(fee_rows), where=v_before_rows > 0),
            index=dates,
            name="cost",
        ),
        rebalance_dates=target_weights.index.copy(),
        target_weights=target_weights,
        cash=pd.Series(cash_rows, index=dates, name="cash"),
        holdings=holdings_df,
        fees=pd.Series(fee_rows, index=dates, name="fee"),
        trades=trades_df,
        execution_log=execution_log,
        decision_log=[],
        initial_cash=float(cfg.initial_cash),
        config_snapshot={
            "execution": {
                "initial_cash": float(cfg.initial_cash),
                "transaction_cost_bps": float(cfg.transaction_cost_bps),
            }
        },
    )


# ── 便捷入口（业务层使用；只准备输入后调用 execute_backtest，无第二本账）──

def run_backtest(
    price_data: pd.DataFrame,
    factor_scores: pd.DataFrame,
    config: BacktestConfig | None = None,
    *,
    decision_issues: pd.DataFrame | None = None,
) -> BacktestResult:
    """从长表行情与因子得分运行完整回测。

    trading_dates 取 price_data 全部日期并集（不与有效因子日期做
    intersection 删除缺信号日期）；在全部计划调仓决策日检查资格并构建
    稀疏目标；执行交给 execute_backtest。
    """
    cfg = config or BacktestConfig()
    if not isinstance(factor_scores, pd.DataFrame) or factor_scores.columns.empty:
        raise ValueError("factor_scores 必须是带证券列的 DataFrame")
    if factor_scores.columns.has_duplicates:
        raise ValueError("factor_scores 存在重复证券列")

    close = pivot_price_field(price_data, field="close", universe=list(factor_scores.columns))
    _validate_close(close)
    trading_dates = get_trading_dates(price_data)

    plan = build_target_weights(
        factor_scores,
        trading_dates,
        cfg,
        decision_issues=decision_issues,
    )
    exec_cfg = ExecutionConfig(initial_cash=1.0, transaction_cost_bps=cfg.transaction_cost_bps)
    result = execute_backtest(close, plan.target_weights, exec_cfg)

    snapshot = dict(result.config_snapshot)
    snapshot["selection"] = {
        "rebalance_freq": cfg.rebalance_freq,
        "rebalance_day": cfg.rebalance_day,
        "top_n": cfg.top_n,
        "max_weight": cfg.max_weight,
        "min_weight": cfg.min_weight,
        "weight_mode": cfg.weight_mode,
    }
    return replace(
        result,
        decision_log=plan.decision_log,
        rebalance_dates=plan.rebalance_dates,
        config_snapshot=snapshot,
    )
