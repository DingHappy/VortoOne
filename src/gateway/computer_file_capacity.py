"""Local admission for the connector's bounded file adapters."""
from __future__ import annotations

_MIB = 1024 * 1024
SYSTEM_RESERVE = 128 * _MIB
FILE_RESERVATION = {"read": 8 * _MIB, "create": 8 * _MIB, "patch": 32 * _MIB}


def available_memory() -> int | None:
    try:
        import psutil
    except ImportError:
        return None

    try:
        available = psutil.virtual_memory().available
    except (AttributeError, OSError, RuntimeError, psutil.Error):
        return None
    return available if type(available) is int and available >= 0 else None


def has_file_capacity(operation: str) -> bool:
    """Call while holding the machine-wide file reservation."""
    available = available_memory()
    # The existing exclusive reservation still limits concurrency when the
    # platform has no measurement. Neither the home nor the model supplies a budget.
    return available is None or available >= SYSTEM_RESERVE + FILE_RESERVATION[operation]
