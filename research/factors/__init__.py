"""Research factor pool.

Factors here follow the same ``FactorBuilder`` protocol as core factors but
are isolated from the core/web registry. Importing the package registers the
built-in research factors (see ``research/factors/registry.py``).
"""

from __future__ import annotations

from research.factors.price_position import PricePositionFactor
from research.factors.registry import (
    get_research_factor_class,
    list_research_factor_names,
    register_factor,
    resolve_factor_class,
)

# NOTE(2026-08-23): 此前这里 `from research.factors.etf15_* import ...` 引用了一组
# 仓库中不存在的文档因子模块（被 .gitignore 忽略且磁盘未提供），导致本包一导入即
# ModuleNotFoundError、research.main 整条流程崩溃。已移除这些缺失引用，仅保留磁盘上
# 实际存在的因子（price_position）与 registry 接口。若后续补充 etf15_* 实现，请把
# 对应类的导入加回这里。

__all__ = [
    "PricePositionFactor",
    "get_research_factor_class",
    "list_research_factor_names",
    "register_factor",
    "resolve_factor_class",
]