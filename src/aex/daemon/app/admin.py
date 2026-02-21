"""AEX admin endpoints — health, metrics, dashboard, config reload."""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from aex import __version__
from ..db import get_db_connection
from ..ledger import replay_ledger_balances, verify_hash_chain
from ..utils.config_loader import config_loader
from ..utils.logging_config import StructuredLogger
from ..utils.metrics import get_metrics

logger = StructuredLogger(__name__)
router = APIRouter()


def _parse_payload(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return {"raw": value}


@router.get("/admin/activity")
async def activity_feed_endpoint(limit: int = Query(default=40, ge=10, le=200)):
    """Return recent backend activity for the local dashboard UI."""
    with get_db_connection() as conn:
        executions = conn.execute(
            """
            SELECT execution_id, agent, endpoint, state, status_code, created_at, updated_at, terminal_at
            FROM executions
            ORDER BY COALESCE(updated_at, created_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        reservations = conn.execute(
            """
            SELECT execution_id, agent, estimated_micro, actual_micro, state, reserved_at, settled_at, expiry_at
            FROM reservations
            ORDER BY COALESCE(settled_at, reserved_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        event_log = conn.execute(
            """
            SELECT seq, execution_id, agent, event_type, payload_json, ts
            FROM event_log
            ORDER BY seq DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        compat_events = conn.execute(
            """
            SELECT id, agent, action, cost_micro, timestamp, metadata
            FROM events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    execution_states = {}
    for row in executions:
        state = str(row["state"] or "UNKNOWN")
        execution_states[state] = execution_states.get(state, 0) + 1

    return {
        "execution_state_counts": execution_states,
        "executions": [dict(r) for r in executions],
        "reservations": [dict(r) for r in reservations],
        "event_log": [
            {
                **dict(r),
                "payload": _parse_payload(r["payload_json"]),
            }
            for r in event_log
        ],
        "compat_events": [dict(r) for r in compat_events],
    }


@router.post("/admin/reload_config")
async def reload_config_endpoint():
    try:
        config_loader.load_config()
        return {"status": "ok", "message": "Configuration reloaded"}
    except Exception as e:
        logger.error("Config reload failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/replay")
async def replay_audit_endpoint():
    chain = verify_hash_chain()
    replay = replay_ledger_balances()
    return {
        "hash_chain_ok": chain.ok,
        "hash_chain_detail": chain.detail,
        "balance_replay_ok": replay.ok,
        "balance_replay_detail": replay.detail,
    }


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_endpoint():
    """Serve lightweight local-only metrics dashboard."""
    dashboard_path = Path(__file__).parent.parent / "frontend" / "dashboard.html"
    if dashboard_path.exists():
        return HTMLResponse(content=dashboard_path.read_text(), status_code=200)
    raise HTTPException(status_code=404, detail="Dashboard not available")


@router.get("/health")
async def health():
    return {"status": "ok", "version": __version__}


@router.get("/metrics")
async def metrics_endpoint():
    return get_metrics()
