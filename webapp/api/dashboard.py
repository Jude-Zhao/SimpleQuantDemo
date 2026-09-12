"""Dashboard summary API endpoints."""

from __future__ import annotations

import threading

import pandas as pd
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.analysis.ic import calculate_forward_returns, calculate_rank_ic
from core.factors.config import list_factor_categories
from webapp.models.database import get_db, utc_now
from webapp.models.strategy_run import StrategyRun
from webapp.services.data_service import get_etf_price, get_etf_list
from webapp.services.eaa_faa import build_category_factors, category_score_from_matrices
from webapp.services.factor_service import list_factor_categories_meta
from webapp.services.universe_service import list_active_universe
from webapp.services.strategy_service import DEFAULT_START, default_end_date

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# Forward-return horizon used for the home-page factor RankIC computation.
RANK_IC_HORIZON = 5

# FactorRanking cache: keyed by (universe, latest bar date) so it is
# invalidated as soon as new market data arrives. Guarded by a lock because
# concurrent requests may otherwise recompute the (expensive) RankIC in
# parallel and starve other endpoints.
_ranking_cache: dict[tuple[tuple[str, ...], str], list[FactorRankingItem]] = {}
_ranking_cache_lock = threading.Lock()


class DashboardStats(BaseModel):
    universe_count: int
    factor_count: int
    run_count_today: int
    system_status: str


class FactorInstanceRanking(BaseModel):
    name: str
    params: dict
    rank_ic_mean: float
    rank_icir: float

    @property
    def display_label(self) -> str:
        """e.g. 'momentum(20)' or 'momentum' when no window param."""
        window = self.params.get("window")
        return f"{self.name}({window})" if window is not None else self.name


class FactorRankingItem(BaseModel):
    key: str
    display_name: str
    is_empty: bool
    class_rank_ic_mean: float | None = None
    class_rank_icir: float | None = None
    factors: list[FactorInstanceRanking] = []


class ReturnRankingItem(BaseModel):
    sec_code: str
    sec_name: str
    return_pct: float


class ReturnRankingResponse(BaseModel):
    days: int
    as_of: str
    momentum: list[ReturnRankingItem]
    reversal: list[ReturnRankingItem]


class RecentRunItem(BaseModel):
    id: int
    strategy_type: str
    status: str
    total_return: float | None = None
    created_at: str | None = None
    error_msg: str | None = None


@router.get("/stats", response_model=DashboardStats)
def get_stats(db: Session = Depends(get_db)):
    """Get summary statistics for the dashboard cards."""
    universe_count = len(list_active_universe(db))
    factor_count = sum(
        len(cat.factors) for cat in list_factor_categories_meta()
    )

    # "今日"按 UTC 自然日口径统计，与 StrategyRun.created_at 落库(UTC)一致，
    # 避免 naive datetime 与本地时区比较产生跨天偏差。
    today_start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
    run_count_today = (
        db.query(StrategyRun)
        .filter(StrategyRun.created_at >= today_start)
        .count()
    )

    return DashboardStats(
        universe_count=universe_count,
        factor_count=factor_count,
        run_count_today=run_count_today,
        system_status="ok",
    )


@router.get("/factor-ranking", response_model=list[FactorRankingItem])
def get_factor_ranking(db: Session = Depends(get_db)):
    """Compute RankIC statistics for home-page factor ranking, organized by the
    factor categories declared in ``factors.yaml``.

    Every non-empty category reports both the RankIC of each of its factor
    instances and the RankIC of the equal-weighted category score. Empty
    categories (e.g. volume / other) are returned as headers only.

    The RankIC computation is expensive (it re-derives each factor and its IC
    series on every call), so results are cached keyed by the universe and the
    latest bar date. The cache is invalidated automatically whenever new market
    data is synced.
    """
    etfs = get_etf_list(db)
    universe = [e["sec_code"] for e in etfs]
    if not universe:
        return []

    price_data = get_etf_price(db, universe, DEFAULT_START, default_end_date())
    if price_data.empty:
        return []

    key = (
        tuple(sorted(universe)),
        str(pd.to_datetime(price_data["date"]).max()),
    )
    with _ranking_cache_lock:
        cached = _ranking_cache.get(key)
    if cached is not None:
        return cached

    categories = list_factor_categories()
    forward_returns = calculate_forward_returns(
        price_data, horizon=RANK_IC_HORIZON, universe=universe
    )

    ranking: list[FactorRankingItem] = []
    for cat in categories:
        if cat.is_empty:
            ranking.append(FactorRankingItem(
                key=cat.key,
                display_name=cat.display_name,
                is_empty=True,
            ))
            continue

        try:
            # Build the category's factor matrices once and reuse them for both
            # the per-instance RankIC and the equal-weighted category score.
            matrices = build_category_factors(price_data, universe, cat)
        except Exception:
            # Skip categories that fail to build on the default data range.
            continue

        factors: list[FactorInstanceRanking] = []
        for inst, matrix in zip(cat.factors, matrices):
            rank_ic = calculate_rank_ic(matrix, forward_returns).dropna()
            if rank_ic.empty:
                continue
            rank_icir = rank_ic.mean() / rank_ic.std() if rank_ic.std() != 0 else 0.0
            factors.append(FactorInstanceRanking(
                name=inst.name,
                params=inst.params,
                rank_ic_mean=float(rank_ic.mean()),
                rank_icir=float(rank_icir),
            ))

        class_mean = class_icir = None
        score = category_score_from_matrices(matrices)
        if score is not None:
            class_rank_ic = calculate_rank_ic(score, forward_returns).dropna()
            if not class_rank_ic.empty:
                class_mean = float(class_rank_ic.mean())
                std = class_rank_ic.std()
                class_icir = float(class_mean / std) if std != 0 else 0.0

        ranking.append(FactorRankingItem(
            key=cat.key,
            display_name=cat.display_name,
            is_empty=False,
            class_rank_ic_mean=class_mean,
            class_rank_icir=class_icir,
            factors=factors,
        ))

    with _ranking_cache_lock:
        _ranking_cache[key] = ranking
    return ranking


@router.get("/returns-ranking", response_model=ReturnRankingResponse)
def get_returns_ranking(
    days: int = Query(20, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Rank universe ETFs by trailing ``days``-day return.

    ``momentum`` is the top-10 highest-returning ETFs (buy-winners logic),
    ``reversal`` the bottom-10 lowest-returning ETFs (buy-losers logic).
    """
    etfs = get_etf_list(db)
    if not etfs:
        return ReturnRankingResponse(days=days, as_of="", momentum=[], reversal=[])
    universe = [e["sec_code"] for e in etfs]
    name_map = {e["sec_code"]: e["sec_name"] for e in etfs}

    price_data = get_etf_price(db, universe, DEFAULT_START, default_end_date())
    if price_data.empty:
        return ReturnRankingResponse(days=days, as_of="", momentum=[], reversal=[])

    piv = price_data.pivot(index="date", columns="sec", values="close").sort_index()
    piv = piv.dropna(how="all")
    if len(piv) < 2:
        return ReturnRankingResponse(days=days, as_of="", momentum=[], reversal=[])

    window = piv.iloc[-days:]
    if len(window) < 2:
        return ReturnRankingResponse(days=days, as_of="", momentum=[], reversal=[])

    ret = (window.iloc[-1] / window.iloc[0] - 1).dropna().sort_values(ascending=False)

    as_of_ts = piv.index[-1]
    as_of = str(as_of_ts.date()) if hasattr(as_of_ts, "date") else str(as_of_ts)

    def item(sec_code: str, return_pct: float) -> ReturnRankingItem:
        return ReturnRankingItem(
            sec_code=sec_code,
            sec_name=name_map.get(sec_code, sec_code),
            return_pct=float(return_pct),
        )

    momentum = [item(c, v) for c, v in ret.head(10).items()]
    reversal = [item(c, v) for c, v in ret.tail(10).sort_values(ascending=True).items()]

    return ReturnRankingResponse(days=days, as_of=as_of, momentum=momentum, reversal=reversal)


@router.get("/recent-runs", response_model=list[RecentRunItem])
def get_recent_runs(limit: int = 10, db: Session = Depends(get_db)):
    """Get recent strategy run records for the dashboard list."""
    runs = (
        db.query(StrategyRun)
        .order_by(StrategyRun.id.desc())
        .limit(limit)
        .all()
    )

    result = []
    for run in runs:
        # 只读已存 metrics.total_return；无该指标返回 None（前端显示未记录）。
        # B7：不再从 equity_curve 字典首尾值重算兜底——列表/详情/导出均只读，
        # 不为补字段写回数据库。
        summary = run.result_summary or {}
        raw_metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else None
        total_return = raw_metrics.get("total_return") if raw_metrics is not None else None

        result.append(RecentRunItem(
            id=run.id,
            strategy_type=run.strategy_type,
            status=run.status,
            total_return=total_return,
            created_at=run.created_at.isoformat() if run.created_at else None,
            error_msg=run.error_msg,
        ))
    return result