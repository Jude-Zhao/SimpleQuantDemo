"""Research factor registry (isolated from the core/web registry).

Research factors live in this pool and are NOT registered into the core
global registry, so experimental factors never pollute the production
``core.factors`` registry. The protocol is identical to core's
``FactorBuilder`` (``build -> date x sec`` matrix), which makes a validated
factor trivially migratable to ``core/factors/builtin`` + ``factors.yaml``.

``resolve_factor_class`` looks up the research pool first, then falls back to
the core registry, so research configs can mix custom factors with core
built-ins (momentum / volatility / reversal ...).
"""

from __future__ import annotations

from typing import Callable

from core.factors.base import FactorBuilder
from core.factors.registry import get_factor_class

_research_registry: dict[str, type[FactorBuilder]] = {}


def register_factor(
    name: str | None = None,
) -> Callable[[type[FactorBuilder]], type[FactorBuilder]]:
    """Decorator: register a research factor class in the research pool."""

    def decorator(cls: type[FactorBuilder]) -> type[FactorBuilder]:
        key = name or getattr(cls, "registry_name", None) or cls.__name__
        _research_registry[key] = cls
        return cls

    return decorator


def get_research_factor_class(name: str) -> type[FactorBuilder] | None:
    """Get a factor class from the research pool only."""
    return _research_registry.get(name)


def resolve_factor_class(name: str) -> type[FactorBuilder] | None:
    """Resolve a factor class: research pool first, then core registry."""
    return _research_registry.get(name) or get_factor_class(name)


def list_research_factor_names() -> list[str]:
    """List all factor names registered in the research pool."""
    return sorted(_research_registry)