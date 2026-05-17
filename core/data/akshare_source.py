"""AkShare adapter placeholder for daily macro data.

The V1 data module first standardizes local files. Live AkShare fetching will
reuse the catalog in macro_catalog.py and should be implemented field by field
after confirming function signatures in the installed AkShare version.
"""

from __future__ import annotations

from core.data.macro_catalog import MacroFieldSpec


class AkShareDataUnavailable(RuntimeError):
    """Raised when AkShare is not installed or a field adapter is not ready."""


def ensure_akshare_available():
    """Import AkShare lazily so local CSV workflows do not require it at import time."""
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:
        raise AkShareDataUnavailable(
            "AkShare is not installed. Install project dependencies before using live data."
        ) from exc
    return ak


def fetch_macro_field(spec: MacroFieldSpec):
    """Fetch one AkShare macro field.

    This generic adapter intentionally stays conservative. AkShare function
    signatures vary by endpoint and version, so production fetchers should
    normalize each field explicitly before it is enabled in an automated job.
    """
    if spec.provider != "akshare":
        raise AkShareDataUnavailable(f"Spec {spec.name} is not an AkShare field.")
    if spec.function is None:
        raise AkShareDataUnavailable(f"Spec {spec.name} does not define an AkShare function.")

    ak = ensure_akshare_available()
    func = getattr(ak, spec.function, None)
    if func is None:
        raise AkShareDataUnavailable(f"AkShare function not found: {spec.function}")
    return func(**spec.params)

