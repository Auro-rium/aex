"""Crash recovery for non-terminal executions and stale reservations."""

from __future__ import annotations

from datetime import datetime, UTC

from ..db import get_db_connection
from ..ledger.budget import mark_execution_failed, release_execution_reservation
from ..utils.logging_config import StructuredLogger

logger = StructuredLogger(__name__)


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        value = datetime.fromisoformat(ts)
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value
    except Exception:
        return None


def reconcile_incomplete_executions() -> dict[str, int]:
    """Recover reservations/executions that were left non-terminal by crashes."""
    now = datetime.now(UTC)
    released = 0
    failed = 0

    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT e.execution_id, e.agent, e.state AS exec_state,
                   r.estimated_micro, r.state AS res_state, r.expiry_at
            FROM executions e
            LEFT JOIN reservations r ON r.execution_id = e.execution_id
            WHERE e.state NOT IN ('COMMITTED', 'DENIED', 'RELEASED', 'FAILED')
            """
        ).fetchall()

    for row in rows:
        execution_id = row["execution_id"]
        agent = row["agent"]
        estimated = int(row["estimated_micro"] or 0)
        res_state = row["res_state"]

        expiry = _parse_iso(row["expiry_at"])
        is_expired = bool(expiry and now > expiry)

        if res_state == "RESERVED" and is_expired:
            release_execution_reservation(
                agent=agent,
                execution_id=execution_id,
                estimated_cost_micro=estimated,
                reason="Recovered stale reservation",
                status_code=504,
            )
            released += 1
            continue

        if row["exec_state"] == "RESERVING" and not res_state:
            mark_execution_failed(execution_id, reason="Interrupted during reserving", status_code=500)
            failed += 1
            continue

        if row["exec_state"] in {"DISPATCHED", "RESPONSE_RECEIVED"} and not res_state:
            mark_execution_failed(execution_id, reason="Missing reservation during recovery", status_code=500)
            failed += 1

    if released or failed:
        logger.warning(
            "Crash recovery sweep completed",
            reservations_released=released,
            executions_failed=failed,
        )

    return {"released": released, "failed": failed, "scanned": len(rows)}
