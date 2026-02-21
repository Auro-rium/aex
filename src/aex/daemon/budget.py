from .db import get_db_connection
from .logging_config import StructuredLogger
from fastapi import HTTPException

logger = StructuredLogger(__name__)

def reserve_budget(agent: str, estimated_cost_micro: int) -> bool:
    """
    Reserves budget for a request.
    Returns True if successful, raises HTTPException(402) if insufficient funds.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        
        row = cursor.execute("SELECT budget_micro, spent_micro, reserved_micro FROM agents WHERE name = ?", (agent,)).fetchone()
        if not row:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Agent not found")
            
        remaining = row["budget_micro"] - row["spent_micro"] - row["reserved_micro"]
        
        if estimated_cost_micro > remaining:
            logger.warning("Budget exceeded", agent=agent, estimated=estimated_cost_micro, remaining=remaining)
            cursor.execute("INSERT INTO events (agent, action, cost_micro, metadata) VALUES (?, ?, ?, ?)",
                           (agent, "DENIED_BUDGET", 0, f"Req: {estimated_cost_micro} > Rem: {remaining}"))
            conn.commit() # Commit the denial event
            raise HTTPException(status_code=402, detail="Insufficient budget")
            
        # Reserve funds
        cursor.execute("UPDATE agents SET reserved_micro = reserved_micro + ? WHERE name = ?", (estimated_cost_micro, agent))
        conn.commit()
        return True

def commit_usage(agent: str, estimated_cost_micro: int, actual_cost_micro: int):
    """
    Commits actual usage and releases reservation.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        
        # Release reservation and apply actual cost
        # Note: We decrease reserved by estimated, and increase spent by actual.
        cursor.execute("""
            UPDATE agents 
            SET reserved_micro = MAX(0, reserved_micro - ?),
                spent_micro = spent_micro + ?,
                last_activity = CURRENT_TIMESTAMP
            WHERE name = ?
        """, (estimated_cost_micro, actual_cost_micro, agent))
        
        cursor.execute("INSERT INTO events (agent, action, cost_micro) VALUES (?, ?, ?)",
                       (agent, "USAGE_RECORDED", actual_cost_micro))
                       
        # Check for overspend post-commit (invariant check)
        row = cursor.execute("SELECT budget_micro, spent_micro FROM agents WHERE name = ?", (agent,)).fetchone()
        if row and row["spent_micro"] > row["budget_micro"]:
            logger.critical("Overspend detected AFTER commit", agent=agent, spent=row["spent_micro"], budget=row["budget_micro"])
            # Supervisor will catch this in the next tick, but we log it now.
            
        conn.commit()

def release_reservation_on_error(agent: str, estimated_cost_micro: int):
    """
    Releases reservation if request failed without usage.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute("UPDATE agents SET reserved_micro = MAX(0, reserved_micro - ?) WHERE name = ?", (estimated_cost_micro, agent))
        conn.commit()

def clear_all_reservations():
    """
    Clears all reservations on startup.
    This handles crash recovery where reserved funds might be stuck.
    """
    with get_db_connection() as conn:
        conn.execute("UPDATE agents SET reserved_micro = 0")
        conn.commit()
    logger.warning("Cleared all stale reservations on startup")
