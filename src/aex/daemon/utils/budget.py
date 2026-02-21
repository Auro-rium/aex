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
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            
            row = cursor.execute("SELECT budget_micro, spent_micro, reserved_micro FROM agents WHERE name = ?", (agent,)).fetchone()
            if not row:
                conn.rollback()
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
            raise 
        except Exception as e:
            conn.rollback()
            logger.error("Database error during reservation", error=str(e), agent=agent)
            raise HTTPException(status_code=500, detail="Internal accounting error")

def commit_usage(agent: str, estimated_cost_micro: int, actual_cost_micro: int, prompt_tokens: int = 0, completion_tokens: int = 0):
    """
    Commits actual usage and releases reservation.
    Atomic: Updates spent/reserved, token counts, and inserts event same transaction.
    Invariant: usage.commit event MUST equal spent_micro delta.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            
            # 1. Update Agent State
            cursor.execute("""
                UPDATE agents 
                SET reserved_micro = MAX(0, reserved_micro - ?),
                    spent_micro = spent_micro + ?,
                    tokens_used_prompt = tokens_used_prompt + ?,
                    tokens_used_completion = tokens_used_completion + ?,
                    last_activity = CURRENT_TIMESTAMP
                WHERE name = ?
            """, (estimated_cost_micro, actual_cost_micro, prompt_tokens, completion_tokens, agent))
            
            # 2. Update Rate Window Token Tracking
            # We add tokens to the CURRENT window (started at proxy ingress)
            # If the window rolled over during the request, rate_limit.py will reset it on next ingress.
            total_tokens = prompt_tokens + completion_tokens
            if total_tokens > 0:
                 cursor.execute("UPDATE rate_windows SET tokens_count = tokens_count + ? WHERE agent = ?", (total_tokens, agent))

            # 3. Log Event (Source of Truth)
            cursor.execute("INSERT INTO events (agent, action, cost_micro) VALUES (?, ?, ?)",
                           (agent, "usage.commit", actual_cost_micro))
                           
            # 4. Invariant Check (Post-Mutation)
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
            raise

def release_reservation_on_error(agent: str, estimated_cost_micro: int):
    """
    Releases reservation if request failed without usage.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute("UPDATE agents SET reserved_micro = MAX(0, reserved_micro - ?) WHERE name = ?", (estimated_cost_micro, agent))
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error("Failed to release reservation", agent=agent, error=str(e))

def clear_all_reservations():
    """
    Clears all reservations on startup.
    """
    with get_db_connection() as conn:
        try:
            conn.execute("UPDATE agents SET reserved_micro = 0")
            conn.commit()
        except Exception as e:
            logger.error("Failed to clear reservations", error=str(e))
    logger.warning("Cleared all stale reservations on startup")
