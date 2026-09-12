"""Factor synthesis utilities."""

from core.synthesis.base import FactorSynthesizer
from core.synthesis.eligibility import (
    ISSUE_COLUMNS as CATEGORY_ISSUE_COLUMNS,
    CategoryBuildResult,
    build_category_scores_with_details,
)
from core.synthesis.faa_eaa import (
    EPS,
    Resolver,
    build_category_factors,
    build_category_scores,
    category_score_from_matrices,
    eaa_composite,
    faa_composite,
    normalize_cross_section,
)
from core.synthesis.icir_weight import ICIRWeightedSynthesizer, calculate_decayed_icir_score

__all__ = [
    "CATEGORY_ISSUE_COLUMNS",
    "EPS",
    "CategoryBuildResult",
    "FactorSynthesizer",
    "ICIRWeightedSynthesizer",
    "Resolver",
    "build_category_factors",
    "build_category_scores",
    "build_category_scores_with_details",
    "calculate_decayed_icir_score",
    "category_score_from_matrices",
    "eaa_composite",
    "faa_composite",
    "normalize_cross_section",
]
