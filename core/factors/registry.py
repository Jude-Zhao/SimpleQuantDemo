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


# Modules that are infrastructure rather than factor implementations.
# They are imported as dependencies, not scanned for @register_factor.
_NON_FACTOR_MODULES = frozenset({"base", "registry", "utils", "exceptions", "__init__"})


def discover_factors(package_name: str = "core.factors") -> None:
    """Auto-discover factor modules under the given package (recursively).

    Imports every .py module in the package and its subpackages, which
    triggers execution of any ``@register_factor`` decorators. Modules in
    ``_NON_FACTOR_MODULES`` are skipped, as are private modules (``_*``).
    """
    global _discovered
    if _discovered:
        return

    package = importlib.import_module(package_name)
    _import_factor_modules(package, package_name)

    _discovered = True


def _import_factor_modules(package: object, package_path: str) -> None:
    """Recursively import all factor modules within a package."""
    for _, module_name, is_pkg in pkgutil.iter_modules(package.__path__):
        if module_name in _NON_FACTOR_MODULES or module_name.startswith("_"):
            continue
        full_name = f"{package_path}.{module_name}"
        if is_pkg:
            sub_package = importlib.import_module(full_name)
            _import_factor_modules(sub_package, full_name)
        else:
            importlib.import_module(full_name)


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
