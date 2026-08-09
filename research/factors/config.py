"""Load the research factor category config.

Reads ``research/factor_config.yaml`` (format aligned with
``core/factors/builtin/factors.yaml``) and returns ``FactorCategory``
tuples. Factor names are resolved through the research pool first, then the
core registry, so custom research factors and core built-ins can coexist.
"""

from __future__ import annotations

from pathlib import Path

import yaml

import research.factors  # noqa: F401  # import registers research factors
from core.factors.config import FactorCategory, FactorInstance
from research.factors.registry import resolve_factor_class

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "factor_config.yaml"


def load_research_categories() -> tuple[FactorCategory, ...]:
    """Load and validate research factor categories from the YAML config."""
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(f"Research factor config not found: {_CONFIG_PATH}")
    with _CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    categories: list[FactorCategory] = []
    for key, raw in (data.get("categories") or {}).items():
        display_name = (raw or {}).get("display_name", key)
        factors: list[FactorInstance] = []
        for item in (raw or {}).get("factors") or []:
            name = item["name"]
            if not isinstance(name, str) or not name:
                raise ValueError(f"Invalid factor name in category '{key}': {item!r}")
            if resolve_factor_class(name) is None:
                raise ValueError(
                    f"Factor '{name}' in category '{key}' is not resolvable in "
                    f"the research or core registry."
                )
            factors.append(
                FactorInstance(name=name, params=dict(item.get("params") or {}))
            )
        categories.append(
            FactorCategory(key=key, display_name=display_name, factors=tuple(factors))
        )

    return tuple(categories)