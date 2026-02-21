"""
AEX Formal Invariant Layer — Database integrity and lifecycle verification.

All checks are deterministic queries against the SQLite database.
No mutations. No side effects.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class InvariantResult:
    name: str
    passed: bool
    detail: Optional[str] = None


def check_spent_within_budget(conn) -> InvariantResult:
    """INV-1: spent_micro <= budget_micro for all agents."""
    cursor = conn.cursor()
    rows = cursor.execute(
        "SELECT name, spent_micro, budget_micro FROM agents WHERE spent_micro > budget_micro"
    ).fetchall()

    if rows:
        violations = [f"{r['name']}: spent={r['spent_micro']} > budget={r['budget_micro']}" for r in rows]
        return InvariantResult(
            name="spent_within_budget",
            passed=False,
            detail=f"Violations: {'; '.join(violations)}"
        )
    return InvariantResult(name="spent_within_budget", passed=True)


def check_no_negative_values(conn) -> InvariantResult:
    """INV-2: No negative values in budget_micro, spent_micro, or reserved_micro."""
    cursor = conn.cursor()
    rows = cursor.execute(
        "SELECT name, budget_micro, spent_micro, reserved_micro FROM agents "
        "WHERE budget_micro < 0 OR spent_micro < 0 OR reserved_micro < 0"
    ).fetchall()

    if rows:
        violations = [f"{r['name']}: budget={r['budget_micro']}, spent={r['spent_micro']}, reserved={r['reserved_micro']}" for r in rows]
        return InvariantResult(
            name="no_negative_values",
            passed=False,
            detail=f"Violations: {'; '.join(violations)}"
        )
    return InvariantResult(name="no_negative_values", passed=True)


def check_no_orphaned_reservations(conn) -> InvariantResult:
    """INV-3: reserved_micro should be 0 when no active requests are in flight.
    
    Note: This check is best-effort. If the daemon is processing requests,
    there may be legitimate non-zero reservations. This check is most meaningful
    when the daemon is idle or stopped.
    """
    cursor = conn.cursor()
    rows = cursor.execute(
        "SELECT name, reserved_micro FROM agents WHERE reserved_micro != 0"
    ).fetchall()

    if rows:
        agents_with_reservations = [f"{r['name']}: {r['reserved_micro']}µ" for r in rows]
        return InvariantResult(
            name="no_orphaned_reservations",
            passed=False,
            detail=f"Non-zero reservations (may be in-flight): {'; '.join(agents_with_reservations)}"
        )
    return InvariantResult(name="no_orphaned_reservations", passed=True)


def check_event_log_integrity(conn) -> InvariantResult:
    """INV-4: Every USAGE_RECORDED event should have a positive cost_micro."""
    cursor = conn.cursor()
    rows = cursor.execute(
        "SELECT id, agent, cost_micro FROM events WHERE action = 'USAGE_RECORDED' AND (cost_micro IS NULL OR cost_micro < 0)"
    ).fetchall()

    if rows:
        violations = [f"event #{r['id']} agent={r['agent']} cost={r['cost_micro']}" for r in rows]
        return InvariantResult(
            name="event_log_integrity",
            passed=False,
            detail=f"Invalid usage events: {'; '.join(violations)}"
        )
    return InvariantResult(name="event_log_integrity", passed=True)


def check_spent_matches_events(conn) -> InvariantResult:
    """INV-5: Sum of USAGE_RECORDED events per agent should match spent_micro."""
    cursor = conn.cursor()
    
    # Get per-agent event sums
    event_sums = cursor.execute(
        "SELECT agent, SUM(cost_micro) as total_cost FROM events "
        "WHERE action = 'USAGE_RECORDED' GROUP BY agent"
    ).fetchall()
    
    event_map = {r["agent"]: r["total_cost"] for r in event_sums}
    
    # Get agent spent values
    agents = cursor.execute("SELECT name, spent_micro FROM agents").fetchall()
    
    mismatches = []
    for agent in agents:
        event_total = event_map.get(agent["name"], 0) or 0
        if event_total != agent["spent_micro"]:
            mismatches.append(
                f"{agent['name']}: spent_micro={agent['spent_micro']}, event_sum={event_total}"
            )
    
    if mismatches:
        return InvariantResult(
            name="spent_matches_events",
            passed=False,
            detail=f"Mismatches: {'; '.join(mismatches)}"
        )
    return InvariantResult(name="spent_matches_events", passed=True)


def run_all_checks(conn) -> list[InvariantResult]:
    """Run all invariant checks and return results."""
    return [
        check_spent_within_budget(conn),
        check_no_negative_values(conn),
        check_no_orphaned_reservations(conn),
        check_event_log_integrity(conn),
        check_spent_matches_events(conn),
    ]
