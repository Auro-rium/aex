"""AEX Process Supervisor — dead PID cleanup."""

import psutil
from ..db import get_db_connection
from .logging_config import StructuredLogger

logger = StructuredLogger(__name__)


def cleanup_dead_processes():
    """Removes PID entries for processes that are no longer running."""
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
