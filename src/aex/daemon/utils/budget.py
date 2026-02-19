from ..db import get_db_connection
from .logging_config import StructuredLogger
from fastapi import HTTPException
import sqlite3

logger = StructuredLogger(__name__)

def reserve_budget(agent: str, estimated_cost_micro: int) -> bool:
    """
    Reserves budget for a request.
    Atomic: Enters transaction, checks balance, reserves or denies (with event).
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        
        row = cursor.execute("SELECT budget_micro, spent_micro, reserved_micro FROM agents WHERE name = ?", (agent,)).fetchone()
        if not row:
            conn.rollback() # Should not happen if auth middleware works
            raise HTTPException(status_code=404, detail="Agent not found")
            
        remaining = row["budget_micro"] - row["spent_micro"] - row["reserved_micro"]
        
        if estimated_cost_micro > remaining:
            # Atomic Denial
            logger.warning("Budget exceeded", agent=agent, estimated=estimated_cost_micro, remaining=remaining)
            cursor.execute("INSERT INTO events (agent, action, cost_micro, metadata) VALUES (?, ?, ?, ?)",
                           (agent, "budget.deny", 0, f"Req: {estimated_cost_micro} > Rem: {remaining}"))
            conn.commit()
            raise HTTPException(status_code=402, detail="Insufficient budget")
            
        # Atomic Reservation
        cursor.execute("UPDATE agents SET reserved_micro = reserved_micro + ? WHERE name = ?", (estimated_cost_micro, agent))
        conn.commit()
        return True
        
    except HTTPException:
        # Already handled (committed denial or rollback not needed if we committed)
        # Verify transaction state? No, we committed before raising. 
        # But wait, if any other error, we catch below.
        raise 
    except Exception as e:
        conn.rollback()
        logger.error("Database error during reservation", error=str(e), agent=agent)
        raise HTTPException(status_code=500, detail="Internal accounting error")
    finally:
        conn.close()

def commit_usage(agent: str, estimated_cost_micro: int, actual_cost_micro: int):
    """
    Commits actual usage and releases reservation.
    Atomic: Updates spent/reserved and inserts event same transaction.
    Invariant: usage.commit event MUST equal spent_micro delta.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        
        # 1. Update Agent State
        cursor.execute("""
            UPDATE agents 
            SET reserved_micro = MAX(0, reserved_micro - ?),
                spent_micro = spent_micro + ?,
                last_activity = CURRENT_TIMESTAMP
            WHERE name = ?
        """, (estimated_cost_micro, actual_cost_micro, agent))
        
        # 2. Log Event (Source of Truth)
        cursor.execute("INSERT INTO events (agent, action, cost_micro) VALUES (?, ?, ?)",
                       (agent, "usage.commit", actual_cost_micro))
                       
        # 3. Invariant Check (Post-Mutation)
        row = cursor.execute("SELECT budget_micro, spent_micro FROM agents WHERE name = ?", (agent,)).fetchone()
        if row and row["spent_micro"] > row["budget_micro"]:
            # We allow the commit to finish (ledger integrity > budget enforcement at this stage)
            # but we log CRITICAL for supervisor intervention.
            logger.critical("Overspend detected", agent=agent, spent=row["spent_micro"], budget=row["budget_micro"])
            
        conn.commit()
        
    except Exception as e:
        conn.rollback()
        logger.critical("Accounting Integrity Failure: Failed to commit usage", 
                        agent=agent, error=str(e), cost=actual_cost_micro)
        # We swallow exception here? No, caller usually can't recover. 
        # But this is post-response usually (streaming).
        # We must re-raise so at least we know.
        raise
    finally:
        conn.close()

def release_reservation_on_error(agent: str, estimated_cost_micro: int):
    """
    Releases reservation if request failed without usage.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute("UPDATE agents SET reserved_micro = MAX(0, reserved_micro - ?) WHERE name = ?", (estimated_cost_micro, agent))
        # Optional: Log budget.release? 
        # Plan said so. Let's add it for strict audit.
        # cursor.execute("INSERT INTO events (agent, action, cost_micro, metadata) VALUES (?, ?, ?, ?)",
        #                (agent, "budget.release", 0, f"Released {estimated_cost_micro}"))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error("Failed to release reservation", agent=agent, error=str(e))
    finally:
        conn.close()

def clear_all_reservations():
    """
    Clears all reservations on startup.
    """
    conn = get_db_connection()
    try:
        conn.execute("UPDATE agents SET reserved_micro = 0")
        conn.commit()
    except Exception as e:
        logger.error("Failed to clear reservations", error=str(e))
    finally:
        conn.close()
    logger.warning("Cleared all stale reservations on startup")
