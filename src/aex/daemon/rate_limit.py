from datetime import datetime, timedelta
from fastapi import HTTPException
from .db import get_db_connection
from .logging_config import StructuredLogger

logger = StructuredLogger(__name__)

def check_rate_limit(agent: str):
    """
    Checks rate limit for an agent.
    Raises HTTPException(429) if limit exceeded.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        
        # Get Limit
        agent_row = cursor.execute("SELECT rpm_limit FROM agents WHERE name = ?", (agent,)).fetchone()
        if not agent_row:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Agent not found")
            
        limit = agent_row["rpm_limit"]
        
        # Get Current Window
        window_row = cursor.execute("SELECT window_start, request_count FROM rate_windows WHERE agent = ?", (agent,)).fetchone()
        
        now = datetime.utcnow()
        if window_row:
            window_start = datetime.fromisoformat(window_row["window_start"])
            if now - window_start > timedelta(minutes=1):
                # Window expired, reset
                cursor.execute("UPDATE rate_windows SET window_start = ?, request_count = 1 WHERE agent = ?", (now.isoformat(), agent))
            else:
                # Within window
                if window_row["request_count"] >= limit:
                    cursor.execute("INSERT INTO events (agent, action, cost_micro, metadata) VALUES (?, ?, ?, ?)",
                                   (agent, "RATE_LIMIT", 0, f"Limit: {limit}"))
                    conn.commit()
                    logger.warning("Rate limit exceeded", agent=agent, limit=limit)
                    raise HTTPException(status_code=429, detail="Rate limit exceeded")
                
                cursor.execute("UPDATE rate_windows SET request_count = request_count + 1 WHERE agent = ?", (agent,))
        else:
             # First request
             cursor.execute("INSERT INTO rate_windows (agent, window_start, request_count) VALUES (?, ?, 1)", (agent, now.isoformat()))
             
        conn.commit()
