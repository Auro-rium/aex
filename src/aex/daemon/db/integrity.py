"""Database integrity check — delegates to invariants layer."""

from ..utils.invariants import run_all_checks
from .connection import get_db_connection


def check_db_integrity() -> bool:
    """Quick integrity check using the first 2 formal invariants (spent ≤ budget, no negatives)."""
    with get_db_connection() as conn:
        results = run_all_checks(conn)
        # Only check the first 2 invariants for startup gate
        for result in results[:2]:
            if not result.passed:
                return False
    return True
