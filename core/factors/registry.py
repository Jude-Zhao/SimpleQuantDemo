"""Factor registry with auto-discovery.

Provides a plugin-style mechanism: add a new .py file under core/factors/
with a @register_factor decorator, and it will be auto-discovered.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Callable

from core.factors.base import FactorBuilder

_factor_registry: dict[str, type[FactorBuilder]] = {}
_discovered = False


def register_factor(name: str | None = None) -> Callable[[type[FactorBuilder]], type[FactorBuilder]]:
    """Decorator: register a factor class in the global registry.

    Args:
        name: Registry key. If None, uses ``cls.registry_name`` or ``cls.__name__``.
    """

    def decorator(cls: type[FactorBuilder]) -> type[FactorBuilder]:
        factor_name = name or getattr(cls, "registry_name", None) or cls.__name__
        _factor_registry[factor_name] = cls
        return cls

    return decorator


def discover_factors(package_name: str = "core.factors") -> None:
    """Auto-discover factor modules in the given package.

    Scans all .py modules (excluding base, registry, utils, __init__)
    and imports them, which triggers @register_factor execution.
    """
    global _discovered
    if _discovered:
        return

    package = importlib.import_module(package_name)
    for _, module_name, _ in pkgutil.iter_modules(package.__path__):
        if module_name in ("base", "registry", "utils", "__init__"):
            continue
        importlib.import_module(f"{package_name}.{module_name}")

    _discovered = True


def get_factor_registry() -> dict[str, type[FactorBuilder]]:
    """Get a copy of the factor registry (ensures discovery has run)."""
    discover_factors()
    return dict(_factor_registry)


def get_factor_class(name: str) -> type[FactorBuilder] | None:
    """Get a factor class by registry name."""
    registry = get_factor_registry()
    return registry.get(name)


def list_factor_names() -> list[str]:
    """List all registered factor names."""
    return list(get_factor_registry().keys())
