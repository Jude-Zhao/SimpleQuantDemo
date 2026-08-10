"""Factor synthesis utilities."""

from core.synthesis.base import FactorSynthesizer
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
    "EPS",
    "FactorSynthesizer",
    "ICIRWeightedSynthesizer",
    "Resolver",
    "build_category_factors",
    "build_category_scores",
    "calculate_decayed_icir_score",
    "category_score_from_matrices",
    "eaa_composite",
    "faa_composite",
    "normalize_cross_section",
]

