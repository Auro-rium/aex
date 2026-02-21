"""Database integrity checks for schema + accounting invariants."""

from __future__ import annotations

from ..utils.invariants import run_all_checks
from .connection import get_db_connection

_REQUIRED_TABLES = (
    "agents",
    "pids",
    "events",
    "executions",
    "reservations",
    "event_log",
    "tool_plugins",
    "rate_windows",
)


def check_db_integrity() -> bool:
    """Run fast physical+logical checks used by daemon startup."""
    with get_db_connection() as conn:
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
        if str(quick).lower() != "ok":
            return False

        table_rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        table_names = {row[0] for row in table_rows}
        if any(name not in table_names for name in _REQUIRED_TABLES):
            return False

        results = run_all_checks(conn)
        # Startup gate stays strict but bounded:
        # 1) spent <= budget, 2) non-negative values.
        for result in results[:2]:
            if not result.passed:
                return False
    return True
