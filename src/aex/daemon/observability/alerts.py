"""Operational alert synthesis for readiness and dashboards."""

from __future__ import annotations

from datetime import datetime, UTC, timedelta
import os

from ..db import get_db_connection
from ..utils.invariants import run_all_checks


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        return None


def collect_active_alerts() -> list[dict]:
    """Collect active alerts from DB state, invariants, and short-window trends."""
    stale_threshold = int(os.getenv("AEX_ALERT_STALE_RESERVATIONS", "20"))
    non_terminal_threshold = int(os.getenv("AEX_ALERT_NON_TERMINAL_EXECUTIONS", "50"))
    denied_ratio_threshold = float(os.getenv("AEX_ALERT_DENIED_RATIO", "0.50"))
    provider_429_threshold = int(os.getenv("AEX_ALERT_PROVIDER_429", "30"))
    window_minutes = int(os.getenv("AEX_ALERT_WINDOW_MINUTES", "10"))
    include_hash_chain = (os.getenv("AEX_ALERTS_INCLUDE_HASH_CHAIN", "0").strip() == "1")
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=window_minutes)

    alerts: list[dict] = []
    with get_db_connection() as conn:
        stale_rows = conn.execute(
            """
            SELECT execution_id, expiry_at
            FROM reservations
            WHERE state = 'RESERVED' AND expiry_at IS NOT NULL
            """
        ).fetchall()
        stale_count = 0
        for row in stale_rows:
            expiry = _parse_iso(row["expiry_at"])
            if expiry and expiry < now:
                stale_count += 1
        if stale_count >= stale_threshold:
            alerts.append(
                {
                    "id": "stale_reservations",
                    "severity": "critical",
                    "message": "Stale RESERVED tickets exceed threshold",
                    "value": stale_count,
                    "threshold": stale_threshold,
                    "window_minutes": window_minutes,
                }
            )

        non_terminal = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM executions
            WHERE state NOT IN ('COMMITTED', 'DENIED', 'RELEASED', 'FAILED')
            """
        ).fetchone()
        non_terminal_count = int(non_terminal["c"] or 0)
        if non_terminal_count >= non_terminal_threshold:
            alerts.append(
                {
                    "id": "non_terminal_executions",
                    "severity": "critical",
                    "message": "Non-terminal executions exceed threshold",
                    "value": non_terminal_count,
                    "threshold": non_terminal_threshold,
                }
            )

        exec_rows = conn.execute(
            """
            SELECT state, status_code, updated_at, created_at
            FROM executions
            ORDER BY COALESCE(updated_at, created_at) DESC
            LIMIT 2000
            """
        ).fetchall()
        recent_exec = []
        for row in exec_rows:
            ts = _parse_iso(row["updated_at"] or row["created_at"])
            if ts and ts >= cutoff:
                recent_exec.append(row)

        recent_total = len(recent_exec)
        recent_denied = sum(1 for r in recent_exec if str(r["state"]) == "DENIED")
        recent_429 = sum(1 for r in recent_exec if int(r["status_code"] or 0) == 429)
        if recent_total > 0:
            denied_ratio = recent_denied / recent_total
            if denied_ratio >= denied_ratio_threshold:
                alerts.append(
                    {
                        "id": "high_denial_ratio",
                        "severity": "warning",
                        "message": "High denial ratio in recent execution window",
                        "value": round(denied_ratio, 4),
                        "threshold": denied_ratio_threshold,
                        "window_minutes": window_minutes,
                        "samples": recent_total,
                    }
                )

        if recent_429 >= provider_429_threshold:
            alerts.append(
                {
                    "id": "provider_429_spike",
                    "severity": "warning",
                    "message": "Provider 429 spike detected",
                    "value": recent_429,
                    "threshold": provider_429_threshold,
                    "window_minutes": window_minutes,
                }
            )

        checks = run_all_checks(conn, include_event_hash_chain=include_hash_chain)
        for check in checks:
            if check.passed:
                continue
            severity = "critical" if check.name in {
                "spent_within_budget",
                "no_negative_values",
                "reserved_matches_reservations",
                "event_hash_chain",
            } else "warning"
            alerts.append(
                {
                    "id": f"invariant_{check.name}",
                    "severity": severity,
                    "message": f"Invariant failed: {check.name}",
                    "detail": check.detail,
                }
            )

    return alerts


def summarize_alerts(alerts: list[dict]) -> dict[str, int]:
    summary = {"critical": 0, "warning": 0, "info": 0, "total": 0}
    for alert in alerts:
        sev = str(alert.get("severity", "info")).lower()
        if sev not in summary:
            sev = "info"
        summary[sev] += 1
        summary["total"] += 1
    return summary
