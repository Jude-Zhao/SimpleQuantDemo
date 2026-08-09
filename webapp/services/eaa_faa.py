"""EAA / FAA scoring pipeline (re-exported from the core layer).

The pure synthesis functions now live in ``core.synthesis.faa_eaa`` so they
can be reused by both the webapp and the ``research`` module. This module is
kept as a thin re-export for backwards compatibility with existing callers.
"""

from __future__ import annotations

from core.synthesis.faa_eaa import (
    EPS,
    Resolver,
    build_category_factors,
    build_category_scores,
    eaa_composite,
    faa_composite,
    normalize_cross_section,
)

__all__ = [
    "EPS",
    "Resolver",
    "build_category_factors",
    "build_category_scores",
    "eaa_composite",
    "faa_composite",
    "normalize_cross_section",
]