"""Tests for JoinQuant adapter _rebalance order semantics (audit F09).

聚宽平台脚本无法直接运行：注入 jqdata stub 后导入策略模块，用理想化撮合
（order_target_value 按目标市值全额成交，无费用/滑点/整手取整；fail_secs
中的订单静默拒单）验证下单语义——order_target_value 的 value 是成交后目标
持仓市值（含已有持仓），可用现金只约束新增买入。两个适配器同参数跑一遍，
防止实现漂移。
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest


class _Position:
    def __init__(self, value: float, total_amount: int = 1000):
        self.value = value
        self.total_amount = total_amount


class _Portfolio:
    def __init__(self, total_value: float, available_cash: float, positions: dict):
        self.total_value = total_value
        self.available_cash = available_cash
        self.positions = positions


class _Context:
    def __init__(self, portfolio: _Portfolio):
        self.portfolio = portfolio


class _FakePlatform:
    """理想化撮合：按目标市值全额成交；fail_secs 中的订单静默拒单。"""

    def __init__(self, ctx: _Context, fail_secs=()):
        self.ctx = ctx
        self.fail_secs = set(fail_secs)
        self.orders: list[tuple[str, float]] = []

    def order_target_value(self, sec, target):
        self.orders.append((sec, float(target)))
        if sec in self.fail_secs:
            return None
        pf = self.ctx.portfolio
        pos = pf.positions.get(sec)
        current = pos.value if pos is not None and pos.total_amount > 0 else 0.0
        delta = float(target) - current
        if abs(delta) < 1e-9:
            return None
        if delta > 0:
            if delta > pf.available_cash:
                return None  # 真实平台资金不足拒单
            pos = pf.positions.setdefault(sec, _Position(0.0, total_amount=0))
            pos.value = current + delta
            pos.total_amount += 1
            pf.available_cash -= delta
        else:
            pos.value = current + delta
            if pos.total_amount > 0 and abs(pos.value) < 1e-9:
                pos.total_amount = 0  # 清仓后份额归零
            pf.available_cash += -delta
        return None


@pytest.fixture(params=["faa_strategy", "eaa_strategy"])
def strategy(request):
    sys.modules.setdefault("jqdata", types.ModuleType("jqdata"))
    return importlib.import_module(f"research.joinquant.{request.param}")


def _run(strategy, monkeypatch, positions, cash, total, weights, fail_secs=()):
    ctx = _Context(
        _Portfolio(total, cash, {k: _Position(v) for k, v in positions.items()})
    )
    fake = _FakePlatform(ctx, fail_secs)
    monkeypatch.setattr(
        strategy, "order_target_value", fake.order_target_value, raising=False
    )
    strategy._rebalance(ctx, weights)
    return fake.orders, ctx


def test_exact_positions_place_no_orders(strategy, monkeypatch):
    """验收·原仓不变：已达目标的持仓不产生任何订单。"""
    orders, ctx = _run(
        strategy, monkeypatch,
        positions={"A": 500.0, "B": 500.0}, cash=0.0, total=1000.0,
        weights={"A": 0.5, "B": 0.5},
    )
    assert orders == []
    assert ctx.portfolio.available_cash == pytest.approx(0.0)


def test_underweight_in_target_buys_increment(strategy, monkeypatch):
    """审计反例：总值 2000、A/B 各持 900、现金 300、目标各 1000。

    旧实现 order_target_value(A, min(1000, 298.5)) 会把 A 减到 298.5；
    修复后按增量买入 +100 到目标 1000。
    """
    orders, ctx = _run(
        strategy, monkeypatch,
        positions={"A": 900.0, "B": 900.0}, cash=300.0, total=2000.0,
        weights={"A": 0.5, "B": 0.5},
    )
    # 任何订单都不得低于当前持仓市值（旧实现误减仓的签名）
    assert all(target >= 900.0 for _, target in orders)
    assert ctx.portfolio.positions["A"].value == pytest.approx(1000.0)
    assert ctx.portfolio.positions["B"].value == pytest.approx(1000.0)
    assert ctx.portfolio.available_cash == pytest.approx(100.0)


def test_in_target_overweight_sells_then_buys(strategy, monkeypatch):
    """验收·目标内减仓再增仓：超配的目标内标的先减持释放资金，再补足低配。

    旧实现零现金时两个标的都会被压到 0 并跳过。
    """
    orders, ctx = _run(
        strategy, monkeypatch,
        positions={"A": 1500.0, "B": 500.0}, cash=0.0, total=2000.0,
        weights={"A": 0.5, "B": 0.5},
    )
    assert orders[0] == ("A", pytest.approx(1000.0))  # 第一笔是目标内超配减持
    assert ctx.portfolio.positions["A"].value == pytest.approx(1000.0)
    # B 用释放资金增持：allowed = min(500, 500*0.995)
    assert ctx.portfolio.positions["B"].value == pytest.approx(500.0 + 500.0 * 0.995)
    assert ctx.portfolio.available_cash == pytest.approx(500.0 * 0.005)


def test_zero_cash_still_releases_and_skips_buys(strategy, monkeypatch):
    """验收·现金为零：减仓照常执行，买入不产生订单。"""
    orders, ctx = _run(
        strategy, monkeypatch,
        positions={"A": 1500.0, "C": 500.0}, cash=0.0, total=2000.0,
        weights={"A": 0.5},
    )
    assert ("A", pytest.approx(1000.0)) in orders  # 目标内超配减持
    assert ("C", pytest.approx(0.0)) in orders  # 非目标旧仓清零
    assert ctx.portfolio.available_cash == pytest.approx(1000.0)


def test_partial_order_failure_updates_budget(strategy, monkeypatch):
    """验收·部分订单失败：失败订单不吞预算，后续标的按真实可用现金封顶。"""
    orders, ctx = _run(
        strategy, monkeypatch,
        positions={"A": 1500.0}, cash=0.0, total=2000.0,
        weights={"A": 0.25, "B": 0.25, "C": 0.25},
        fail_secs={"B"},
    )
    assert ("B", pytest.approx(500.0)) in orders  # 尝试过但被拒单
    assert "B" not in ctx.portfolio.positions  # 拒单不建仓
    # C 不受 B 失败影响：按失败后的真实现金 1000 封顶，仍足额买入 500
    assert ctx.portfolio.positions["C"].value == pytest.approx(500.0)
    assert ctx.portfolio.available_cash == pytest.approx(500.0)
