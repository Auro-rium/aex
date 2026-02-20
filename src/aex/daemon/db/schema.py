"""Database schema initialization and migrations for AEX."""

import sqlite3

from ..utils.logging_config import StructuredLogger
from .connection import get_db_connection

logger = StructuredLogger(__name__)

# Columns added via safe migration — ALTER TABLE ADD COLUMN
_MIGRATE_COLUMNS = [
    # v1.1.1 — capability governance
    ("allowed_models", "TEXT"),            # JSON array of model names, NULL = all
    ("max_input_tokens", "INTEGER"),       # per-request cap, NULL = model default
    ("max_output_tokens", "INTEGER"),      # per-request cap, NULL = model default
    ("allow_streaming", "INTEGER DEFAULT 1"),
    ("allow_tools", "INTEGER DEFAULT 1"),
    ("allowed_tool_names", "TEXT"),         # JSON array, NULL = all
    ("allow_function_calling", "INTEGER DEFAULT 1"),
    ("allow_vision", "INTEGER DEFAULT 0"),
    ("strict_mode", "INTEGER DEFAULT 0"),
    # v1.2.0 — auth hardening
    ("token_hash", "TEXT"),                # SHA-256 of api_token
    ("token_expires_at", "TEXT"),          # ISO timestamp, NULL = no expiry
    ("token_scope", "TEXT DEFAULT 'execution'"),  # execution | read-only
    # v1.2.0 — passthrough mode
    ("allow_passthrough", "INTEGER DEFAULT 0"),
    # v2.0.0 — token governance
    ("max_tokens_per_request", "INTEGER"),
    ("max_tokens_per_minute", "INTEGER"),
    ("tokens_used_prompt", "INTEGER DEFAULT 0"),
    ("tokens_used_completion", "INTEGER DEFAULT 0"),
]


def init_db():
    """Initialize the database with the required schema."""
    from .connection import DB_PATH

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
                allowed_models TEXT,
                max_input_tokens INTEGER,
                max_output_tokens INTEGER,
                max_tokens_per_request INTEGER,
                max_tokens_per_minute INTEGER,
                tokens_used_prompt INTEGER DEFAULT 0,
                tokens_used_completion INTEGER DEFAULT 0,
                allow_streaming INTEGER DEFAULT 1,
                allow_tools INTEGER DEFAULT 1,
                allowed_tool_names TEXT,
                allow_function_calling INTEGER DEFAULT 1,
                allow_vision INTEGER DEFAULT 0,
                strict_mode INTEGER DEFAULT 0,
                token_hash TEXT,
                token_expires_at TEXT,
                token_scope TEXT DEFAULT 'execution',
                allow_passthrough INTEGER DEFAULT 0,
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
                tokens_count INTEGER DEFAULT 0,
                FOREIGN KEY(agent) REFERENCES agents(name)
            )
        """)

        conn.commit()

    # Run safe migration for existing databases
    _migrate_schema()
    logger.info("Database initialized successfully")


def _migrate_schema():
    """Safely add new capability columns to existing databases.

    Uses ALTER TABLE ADD COLUMN which is safe and idempotent-ish:
    SQLite will raise an error if the column already exists, which we catch.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for col_name, col_type in _MIGRATE_COLUMNS:
            try:
                cursor.execute(f"ALTER TABLE agents ADD COLUMN {col_name} {col_type}")
                logger.info("Schema migration: added column", column=col_name)
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    pass  # Column already exists — safe to ignore
                else:
                    raise

        # Migrate rate_windows table
        try:
            cursor.execute("ALTER TABLE rate_windows ADD COLUMN tokens_count INTEGER DEFAULT 0")
            logger.info("Schema migration: added column", column="tokens_count", table="rate_windows")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                pass
            else:
                raise

        conn.commit()
