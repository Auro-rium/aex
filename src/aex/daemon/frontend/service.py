"""Backend-oriented dashboard payload builder."""

from __future__ import annotations

import json

from ..db import get_db_connection
from ..ledger import replay_ledger_balances, verify_hash_chain
from ..observability import liveness_report, readiness_report, summarize_alerts
from ..utils.metrics import get_metrics


def _parse_payload(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return {"raw": value}


def activity_snapshot(limit: int = 40) -> dict:
    with get_db_connection() as conn:
        executions = conn.execute(
            """
            SELECT execution_id, tenant_id, project_id, agent, endpoint, state, status_code, created_at, updated_at, terminal_at
            FROM executions
            ORDER BY COALESCE(updated_at, created_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        reservations = conn.execute(
            """
            SELECT execution_id, tenant_id, project_id, agent, estimated_micro, actual_micro, state, reserved_at, settled_at, expiry_at
            FROM reservations
            ORDER BY COALESCE(settled_at, reserved_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        event_log = conn.execute(
            """
            SELECT seq, tenant_id, project_id, execution_id, agent, event_type, payload_json, ts
            FROM event_log
            ORDER BY seq DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        compat_events = conn.execute(
            """
            SELECT id, tenant_id, project_id, agent, action, cost_micro, timestamp, metadata
            FROM events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    execution_states: dict[str, int] = {}
    for row in executions:
        state = str(row["state"] or "UNKNOWN")
        execution_states[state] = execution_states.get(state, 0) + 1

    return {
        "execution_state_counts": execution_states,
        "executions": [dict(r) for r in executions],
        "reservations": [dict(r) for r in reservations],
        "event_log": [{**dict(r), "payload": _parse_payload(r["payload_json"])} for r in event_log],
        "compat_events": [dict(r) for r in compat_events],
    }


def dashboard_payload(limit: int = 120) -> dict:
    chain = verify_hash_chain()
    replay = replay_ledger_balances()
    ready, readiness = readiness_report()
    alerts = list(readiness.get("alerts", []))
    metrics = get_metrics()
    health = liveness_report()
    summary = {
        "daemon_status": health.get("status"),
        "ready": bool(ready),
        "requests": int(metrics.get("total_requests", 0) or 0),
        "executions": int(metrics.get("total_executions", 0) or 0),
        "spent_usd": float(metrics.get("total_spent_global_usd", 0.0) or 0.0),
        "stale_reservations": int(metrics.get("stale_reservations", 0) or 0),
    }

    return {
        "summary": summary,
        "health": health,
        "ready": readiness,
        "metrics": metrics,
        "replay": {
            "hash_chain_ok": chain.ok,
            "hash_chain_detail": chain.detail,
            "balance_replay_ok": replay.ok,
            "balance_replay_detail": replay.detail,
        },
        "activity": activity_snapshot(limit=limit),
        "alerts": alerts,
        "alert_summary": summarize_alerts(alerts),
        "dashboard_ok": bool(ready),
    }
