"""Database integrity checks for schema + accounting invariants (PostgreSQL)."""

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
    "tenants",
    "projects",
    "users",
    "memberships",
    "budgets",
    "quota_limits",
    "webhook_subscriptions",
    "webhook_deliveries",
)


def check_db_integrity() -> bool:
    """Run fast physical+logical checks used by daemon startup."""
    with get_db_connection() as conn:
        # Basic liveness.
        conn.execute("SELECT 1")

        table_rows = conn.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        ).fetchall()
        table_names = {str(row["table_name"]) for row in table_rows}
        if any(name not in table_names for name in _REQUIRED_TABLES):
            return False

        results = run_all_checks(conn)
        # Startup gate stays strict but bounded:
        # 1) spent <= budget, 2) non-negative values.
        for result in results[:2]:
            if not result.passed:
                return False
    return True
