"""Database connection management for AEX."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..utils.logging_config import StructuredLogger

logger = StructuredLogger(__name__)


def get_db_path() -> str:
    """Resolve DB path from environment at call time.

    This avoids stale path capture when AEX_DB_PATH is changed at runtime
    (for tests, CLI wrappers, and multi-process harnesses).
    """
    return os.getenv("AEX_DB_PATH", os.path.expanduser("~/.aex/aex.db"))


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    # Enforce relational consistency and bounded lock wait behavior.
    busy_timeout_ms = int(os.getenv("AEX_SQLITE_BUSY_TIMEOUT_MS", "5000"))
    synchronous = os.getenv("AEX_SQLITE_SYNCHRONOUS", "NORMAL").strip().upper() or "NORMAL"
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute(f"PRAGMA synchronous = {synchronous};")
    conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms};")
    conn.execute("PRAGMA temp_store = MEMORY;")


# Backward-compatible export for callers that read a default path value.
DB_PATH = get_db_path()


@contextmanager
def get_db_connection():
    """
    Yields a SQLite connection.
    Usage:
        with get_db_connection() as conn:
            conn.execute("...")
    """
    db_path = Path(get_db_path())
    db_path.parent.mkdir(parents=True, exist_ok=True)
    timeout_sec = float(os.getenv("AEX_SQLITE_CONNECT_TIMEOUT_SEC", "10"))
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=timeout_sec)
    _apply_pragmas(conn)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
