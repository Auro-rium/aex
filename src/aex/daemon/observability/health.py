"""Liveness and readiness helpers."""

from __future__ import annotations

from datetime import datetime, UTC
import os

from aex import __version__
from ..db import get_db_connection
from ..utils.config_loader import config_loader
from ..utils.invariants import run_all_checks
from .alerts import collect_active_alerts, summarize_alerts


_READINESS_CRITICAL_INVARIANTS = {
    "spent_within_budget",
    "no_negative_values",
    "reserved_matches_reservations",
    "event_hash_chain",
}


def liveness_report() -> dict:
    return {
        "status": "ok",
        "version": __version__,
        "ts": datetime.now(UTC).isoformat(),
    }


def readiness_report() -> tuple[bool, dict]:
    checks: dict[str, dict] = {}
    ready = True
    include_hash_chain = (os.getenv("AEX_READINESS_INCLUDE_HASH_CHAIN", "0").strip() == "1")

    try:
        with get_db_connection() as conn:
            conn.execute("SELECT 1").fetchone()
            checks["database"] = {"ok": True}

            failed = [c for c in run_all_checks(conn, include_event_hash_chain=include_hash_chain) if not c.passed]
            critical = [c for c in failed if c.name in _READINESS_CRITICAL_INVARIANTS]
            checks["invariants"] = {
                "ok": len(critical) == 0,
                "failed": [{"name": c.name, "detail": c.detail} for c in failed],
                "hash_chain_included": include_hash_chain,
            }
            if critical:
                ready = False
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)}
        ready = False

    try:
        default_model = config_loader.get_default_model()
        cfg_model = config_loader.get_model(default_model)
        checks["config"] = {"ok": bool(cfg_model), "default_model": default_model}
        if not cfg_model:
            ready = False
    except Exception as exc:
        checks["config"] = {"ok": False, "error": str(exc)}
        ready = False

    alerts = collect_active_alerts()
    summary = summarize_alerts(alerts)
    checks["alerts"] = summary
    if summary["critical"] > 0:
        ready = False

    payload = {
        "ready": ready,
        "status": "ready" if ready else "not_ready",
        "version": __version__,
        "ts": datetime.now(UTC).isoformat(),
        "checks": checks,
        "alerts": alerts,
    }
    return ready, payload
