from ..db import get_db_connection
from typing import Dict, Any
from datetime import datetime, timedelta
from ..observability import estimate_burn_windows
from ..ledger import verify_hash_chain


def get_metrics() -> Dict[str, Any]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Global stats
        total_agents = cursor.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
        total_spent_micro = cursor.execute("SELECT SUM(spent_micro) FROM agents").fetchone()[0] or 0
        active_processes = cursor.execute("SELECT COUNT(*) FROM pids").fetchone()[0]
        
        # Event stats
        total_requests = cursor.execute(
            "SELECT COUNT(*) FROM events WHERE action IN ('usage.commit', 'USAGE_RECORDED')"
        ).fetchone()[0]
        total_denied_budget = cursor.execute(
            "SELECT COUNT(*) FROM events WHERE action IN ('budget.deny', 'DENIED_BUDGET')"
        ).fetchone()[0]
        total_denied_rate_limit = cursor.execute("SELECT COUNT(*) FROM events WHERE action = 'RATE_LIMIT'").fetchone()[0]
        total_policy_violations = cursor.execute("SELECT COUNT(*) FROM events WHERE action = 'POLICY_VIOLATION'").fetchone()[0]
        total_executions = cursor.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
        stale_reservations = cursor.execute(
            "SELECT COUNT(*) FROM reservations WHERE state = 'RESERVED' AND expiry_at IS NOT NULL AND datetime(expiry_at) < CURRENT_TIMESTAMP"
        ).fetchone()[0]
        execution_states_rows = cursor.execute(
            "SELECT state, COUNT(*) as c FROM executions GROUP BY state"
        ).fetchall()
        execution_states = {row["state"]: row["c"] for row in execution_states_rows}
        hash_chain_rows = cursor.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
        
        # Top models used (from event metadata or upstream — we track via model name in events)
        top_models = []
        model_rows = cursor.execute(
            "SELECT metadata, COUNT(*) as cnt FROM events "
            "WHERE action IN ('usage.commit', 'USAGE_RECORDED') AND metadata IS NOT NULL "
            "GROUP BY metadata ORDER BY cnt DESC LIMIT 5"
        ).fetchall()
        for row in model_rows:
            top_models.append({"model": row["metadata"], "count": row["cnt"]})
        
        # Usage histogram — requests per hour, last 24h
        now = datetime.utcnow()
        histogram = []
        for i in range(24):
            hour_start = now - timedelta(hours=24 - i)
            hour_end = now - timedelta(hours=23 - i)
            count = cursor.execute(
                "SELECT COUNT(*) FROM events WHERE action IN ('usage.commit', 'USAGE_RECORDED') "
                "AND timestamp >= ? AND timestamp < ?",
                (hour_start.isoformat(), hour_end.isoformat())
            ).fetchone()[0]
            histogram.append({
                "hour": hour_start.strftime("%H:%M"),
                "requests": count
            })
        
        # Per-agent stats with burn rate and TTB
        agents = []
        rows = cursor.execute(
            "SELECT name, spent_micro, budget_micro, reserved_micro, "
            "rpm_limit, last_activity, created_at FROM agents"
        ).fetchall()
        
        for row in rows:
            spent = row["spent_micro"]
            budget = row["budget_micro"]
            reserved = row["reserved_micro"]
            remaining = budget - spent - reserved
            
            # Burn rate: µ/sec since first activity
            burn_rate_micro_per_sec = 0
            ttb_seconds = None
            
            created_at = row["created_at"]
            if created_at and spent > 0:
                try:
                    created_dt = datetime.fromisoformat(created_at)
                    elapsed_sec = max(1, int((now - created_dt).total_seconds()))
                    burn_rate_micro_per_sec = spent // elapsed_sec  # integer division
                    
                    # Time-to-budget-exhaustion
                    if burn_rate_micro_per_sec > 0:
                        ttb_seconds = remaining // burn_rate_micro_per_sec
                except (ValueError, TypeError):
                    pass
            
            agents.append({
                "name": row["name"],
                "spent_usd": spent / 1_000_000,
                "remaining_usd": remaining / 1_000_000,
                "budget_usd": budget / 1_000_000,
                "reserved_usd": reserved / 1_000_000,
                "burn_rate_micro_per_sec": burn_rate_micro_per_sec,
                "ttb_seconds": ttb_seconds,
                "rpm_limit": row["rpm_limit"],
                "last_activity": row["last_activity"],
            })

        # Burn-rate windows from committed usage events.
        burn_events = cursor.execute(
            "SELECT agent, cost_micro, timestamp FROM events WHERE action IN ('usage.commit', 'USAGE_RECORDED')"
        ).fetchall()
        grouped_burn = {}
        for ev in burn_events:
            grouped_burn.setdefault(ev["agent"], []).append(dict(ev))

        burn_rate_windows = {
            agent: estimate_burn_windows(events)
            for agent, events in grouped_burn.items()
        }

        chain_check = verify_hash_chain()
            
        return {
            "total_agents": total_agents,
            "total_spent_global_usd": total_spent_micro / 1_000_000,
            "active_processes": active_processes,
            "total_requests": total_requests,
            "total_denied_budget": total_denied_budget,
            "total_denied_rate_limit": total_denied_rate_limit,
            "total_policy_violations": total_policy_violations,
            "total_executions": total_executions,
            "execution_states": execution_states,
            "stale_reservations": stale_reservations,
            "event_log_size": hash_chain_rows,
            "hash_chain_ok": chain_check.ok,
            "hash_chain_detail": chain_check.detail,
            "top_models": top_models,
            "usage_histogram": histogram,
            "burn_rate_windows": burn_rate_windows,
            "agents": agents,
        }
