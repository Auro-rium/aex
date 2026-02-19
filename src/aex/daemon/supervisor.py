import psutil
import time
from .db import get_db_connection
from .logging_config import StructuredLogger

logger = StructuredLogger(__name__)

def kill_process_tree(pid: int):
    try:
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            child.kill()
        parent.kill()
    except psutil.NoSuchProcess:
        pass

def enforce_budget_kill(agent: str):
    """
    Checks if an agent is over budget and kills its process if so.
    Returns True if killed, False otherwise.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Check budget status using connection state snapshot
        row = cursor.execute("SELECT budget_micro, spent_micro FROM agents WHERE name = ?", (agent,)).fetchone()
        if not row:
            return False
            
        over_budget = row["spent_micro"] >= row["budget_micro"]
        
        if over_budget:
            # Check for active PID
            pid_row = cursor.execute("SELECT pid FROM pids WHERE agent = ?", (agent,)).fetchone()
            if pid_row:
                pid = pid_row["pid"]
                logger.warning("Killing process due to budget violation", agent=agent, pid=pid)
                kill_process_tree(pid)
                
                # Cleanup PID record
                cursor.execute("DELETE FROM pids WHERE agent = ?", (agent,))
                cursor.execute("INSERT INTO events (agent, action, cost_micro, metadata) VALUES (?, ?, ?, ?)",
                               (agent, "PROCESS_KILLED", 0, "Budget Violation"))
                conn.commit()
                return True
                
    return False

def register_process(agent: str, pid: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO pids (agent, pid, started_at) VALUES (?, ?, CURRENT_TIMESTAMP)", (agent, pid))
        conn.commit()

def cleanup_dead_processes():
    """
    Removes PID entries for processes that are no longer running.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        pids = cursor.execute("SELECT agent, pid FROM pids").fetchall()
        
        for row in pids:
            try:
                if not psutil.pid_exists(row["pid"]):
                     cursor.execute("DELETE FROM pids WHERE agent = ?", (row["agent"],))
                     logger.info("Cleaned up dead PID", agent=row["agent"], pid=row["pid"])
            except Exception as e:
                logger.error("Error checking PID", error=str(e))
        
        conn.commit()
