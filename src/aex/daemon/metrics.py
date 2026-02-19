from .db import get_db_connection
from typing import Dict, Any

def get_metrics() -> Dict[str, Any]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Global stats
        total_agents = cursor.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
        total_spent_micro = cursor.execute("SELECT SUM(spent_micro) FROM agents").fetchone()[0] or 0
        active_processes = cursor.execute("SELECT COUNT(*) FROM pids").fetchone()[0]
        
        # Event stats
        total_requests = cursor.execute("SELECT COUNT(*) FROM events WHERE action = 'USAGE_RECORDED'").fetchone()[0]
        total_denied_budget = cursor.execute("SELECT COUNT(*) FROM events WHERE action = 'DENIED_BUDGET'").fetchone()[0]
        total_denied_rate_limit = cursor.execute("SELECT COUNT(*) FROM events WHERE action = 'RATE_LIMIT'").fetchone()[0]
        
        # Per-agent stats
        agents = []
        rows = cursor.execute("SELECT name, spent_micro, budget_micro, rpm_limit, last_activity FROM agents").fetchall()
        for row in rows:
            agents.append({
                "name": row["name"],
                "spent_usd": row["spent_micro"] / 1_000_000,
                "remaining_usd": (row["budget_micro"] - row["spent_micro"]) / 1_000_000,
                "rpm_limit": row["rpm_limit"],
                "last_activity": row["last_activity"]
            })
            
        return {
            "total_agents": total_agents,
            "total_spent_global_usd": total_spent_micro / 1_000_000,
            "active_processes": active_processes,
            "total_requests": total_requests,
            "total_denied_budget": total_denied_budget,
            "total_denied_rate_limit": total_denied_rate_limit,
            "agents": agents
        }
