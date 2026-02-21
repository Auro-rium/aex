"""Agent lifecycle state machine enforcement."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

from ..db import get_db_connection
from ..ledger.events import append_hash_event, append_compat_event


ALLOWED_TRANSITIONS = {
    "REGISTERED": {"READY", "DECOMMISSIONED"},
    "READY": {"RUNNING", "QUARANTINED", "DECOMMISSIONED", "PAUSED"},
    "RUNNING": {"PAUSED", "STOPPING", "ERROR_RECOVERY"},
    "PAUSED": {"READY", "STOPPING", "DECOMMISSIONED"},
    "STOPPING": {"STOPPED", "ERROR_RECOVERY"},
    "STOPPED": {"READY", "DECOMMISSIONED"},
    "QUARANTINED": {"READY", "DECOMMISSIONED"},
    "ERROR_RECOVERY": {"READY", "QUARANTINED"},
    "DECOMMISSIONED": set(),
}


@dataclass
class LifecycleTransition:
    agent: str
    from_state: str
    to_state: str
    reason: str


def get_agent_state(agent: str) -> str:
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT lifecycle_state FROM agents WHERE name = ?",
            (agent,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Agent not found")
    return row["lifecycle_state"] or "READY"


def ensure_agent_can_execute(agent_info: dict) -> None:
    state = agent_info.get("lifecycle_state") or "READY"
    if state != "READY":
        raise HTTPException(status_code=423, detail=f"Agent state is {state}; execution blocked")


def transition_agent_state(agent: str, to_state: str, reason: str) -> LifecycleTransition:
    to_state = to_state.strip().upper()
    if to_state not in ALLOWED_TRANSITIONS:
        raise HTTPException(status_code=400, detail=f"Invalid target state '{to_state}'")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        row = cursor.execute(
            "SELECT lifecycle_state FROM agents WHERE name = ?",
            (agent,),
        ).fetchone()
        if not row:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Agent not found")

        from_state = (row["lifecycle_state"] or "READY").upper()
        allowed = ALLOWED_TRANSITIONS.get(from_state, set())
        if to_state not in allowed:
            conn.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"Transition not allowed: {from_state} -> {to_state}",
            )

        cursor.execute(
            """
            UPDATE agents
            SET lifecycle_state = ?, lifecycle_reason = ?, last_activity = CURRENT_TIMESTAMP
            WHERE name = ?
            """,
            (to_state, reason, agent),
        )
        payload = {"from": from_state, "to": to_state, "reason": reason}
        append_hash_event(
            conn,
            execution_id=None,
            agent=agent,
            event_type="agent.state.transition",
            payload=payload,
        )
        append_compat_event(conn, agent=agent, action="AGENT_STATE", metadata=payload)
        conn.commit()

    return LifecycleTransition(agent=agent, from_state=from_state, to_state=to_state, reason=reason)
