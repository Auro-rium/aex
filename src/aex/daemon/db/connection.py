"""Database connection management for AEX."""

import os
import sqlite3
from contextlib import contextmanager

from ..utils.logging_config import StructuredLogger

logger = StructuredLogger(__name__)

DB_PATH = os.getenv("AEX_DB_PATH", os.path.expanduser("~/.aex/aex.db"))


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
