"""Factor category configuration loader.

Loads ``core/factors/builtin/factors.yaml`` which declares how factors are
grouped into categories for the EAA / FAA strategies. Each category holds a
list of factor instances (registry name + constructor params); factors inside
a category are equal-weighted into a single category score, and categories are
then weighted by user configuration.

Loading is cached: the YAML is read once per process and only re-read when the
mtime changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from core.factors.registry import get_factor_registry

_CONFIG_PATH = Path(__file__).resolve().parent / "builtin" / "factors.yaml"


@dataclass(frozen=True)
class FactorInstance:
    """A single factor instance with its constructor params."""

    name: str
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FactorCategory:
    """A category of factors used by EAA / FAA scoring."""

    key: str
    display_name: str
    factors: tuple[FactorInstance, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not self.factors


def _load_yaml() -> dict:
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(f"Factor category config not found: {_CONFIG_PATH}")
    with _CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


@lru_cache(maxsize=1)
def load_factor_categories() -> tuple[FactorCategory, ...]:
    """Load and validate factor categories from the YAML config.

    Validates that every declared factor name exists in the registry so a
    typo in the config fails loudly at load time.
    """
    data = _load_yaml()
    raw_categories = data.get("categories", {})
    registry = get_factor_registry()

    categories: list[FactorCategory] = []
    for key, raw in raw_categories.items():
        display_name = (raw or {}).get("display_name", key)
        factors: list[FactorInstance] = []
        for item in (raw or {}).get("factors", []) or []:
            name = item["name"]
            if name not in registry:
                raise ValueError(
                    f"Factor '{name}' in category '{key}' is not registered. "
                    f"Available: {sorted(registry)}"
                )
            factors.append(
                FactorInstance(name=name, params=dict(item.get("params", {}) or {}))
            )
        categories.append(
            FactorCategory(key=key, display_name=display_name, factors=tuple(factors))
        )

    return tuple(categories)


def list_factor_categories(include_empty: bool = True) -> tuple[FactorCategory, ...]:
    """Return all configured categories (optionally filtering out empty ones)."""
    categories = load_factor_categories()
    if include_empty:
        return categories
    return tuple(c for c in categories if not c.is_empty)


def get_category(key: str) -> FactorCategory | None:
    """Get a single category by key."""
    for cat in load_factor_categories():
        if cat.key == key:
            return cat
    return None


def categories_to_dict(include_empty: bool = True) -> list[dict]:
    """Serialize categories to a JSON-friendly dict list (for API/frontend)."""
    return [
        {
            "key": cat.key,
            "display_name": cat.display_name,
            "is_empty": cat.is_empty,
            "factors": [
                {"name": f.name, "params": f.params} for f in cat.factors
            ],
        }
        for cat in list_factor_categories(include_empty=include_empty)
    ]