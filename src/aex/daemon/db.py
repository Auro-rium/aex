import sqlite3
import os
from contextlib import contextmanager
from .logging_config import StructuredLogger

logger = StructuredLogger(__name__)

DB_PATH = os.getenv("AEX_DB_PATH", os.path.expanduser("~/.aex/aex.db"))

def init_db():
    """Initialize the database with the required schema."""
    logger.info("Initializing database", path=DB_PATH)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # WAL mode for concurrency
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        
        # Agents Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                name TEXT PRIMARY KEY,
                api_token TEXT UNIQUE NOT NULL,
                budget_micro INTEGER NOT NULL,
                spent_micro INTEGER DEFAULT 0,
                reserved_micro INTEGER DEFAULT 0,
                rpm_limit INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_activity TEXT,
                CHECK (budget_micro >= 0),
                CHECK (spent_micro >= 0),
                CHECK (reserved_micro >= 0),
                CHECK (spent_micro <= budget_micro)
            )
        """)
        
        # PIDs Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pids (
                agent TEXT PRIMARY KEY,
                pid INTEGER,
                started_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(agent) REFERENCES agents(name)
            )
        """)
        
        # Events Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT,
                action TEXT,
                cost_micro INTEGER,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT
            )
        """)
        
        # Rate Windows Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rate_windows (
                agent TEXT PRIMARY KEY,
                window_start TEXT,
                request_count INTEGER DEFAULT 0,
                FOREIGN KEY(agent) REFERENCES agents(name)
            )
        """)
        
        conn.commit()
    logger.info("Database initialized successfully")

@contextmanager
def get_db_connection():
    """
    Yields a SQLite connection.
    Usage:
        with get_db_connection() as conn:
            conn.execute("...")
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    # Enable foreign keys
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def check_db_integrity():
    """Checks for database integrity issues (negative values, constraints)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Check for negative values
        cursor.execute("SELECT count(*) FROM agents WHERE budget_micro < 0 OR spent_micro < 0 OR reserved_micro < 0")
        if cursor.fetchone()[0] > 0:
            logger.critical("Integrity Error: Negative values detected in agents table")
            return False

        # Check for overspending (spent > budget)
        cursor.execute("SELECT count(*) FROM agents WHERE spent_micro > budget_micro")
        if cursor.fetchone()[0] > 0:
             logger.critical("Integrity Error: Overspending detected (spent > budget)")
             return False
            
    return True
