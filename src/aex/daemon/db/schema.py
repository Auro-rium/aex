"""Database schema initialization and migrations for AEX."""

from __future__ import annotations

import sqlite3
from typing import Iterable

from ..utils.logging_config import StructuredLogger
from .connection import get_db_connection, get_db_path

logger = StructuredLogger(__name__)

SCHEMA_VERSION = 3

_LIFECYCLE_STATES = (
    "REGISTERED",
    "READY",
    "RUNNING",
    "PAUSED",
    "STOPPING",
    "STOPPED",
    "QUARANTINED",
    "ERROR_RECOVERY",
    "DECOMMISSIONED",
)

_EXECUTION_STATES = (
    "RESERVING",
    "RESERVED",
    "DISPATCHED",
    "RESPONSE_RECEIVED",
    "COMMITTED",
    "RELEASED",
    "DENIED",
    "FAILED",
)

_RESERVATION_STATES = (
    "RESERVED",
    "COMMITTED",
    "RELEASED",
)

_REQUIRED_TABLES = (
    "agents",
    "pids",
    "events",
    "executions",
    "reservations",
    "event_log",
    "tool_plugins",
    "rate_windows",
)

_TABLE_DDL = {
    "agents": f"""
        CREATE TABLE IF NOT EXISTS agents (
            name TEXT PRIMARY KEY,
            api_token TEXT UNIQUE NOT NULL,
            budget_micro INTEGER NOT NULL,
            spent_micro INTEGER NOT NULL DEFAULT 0,
            reserved_micro INTEGER NOT NULL DEFAULT 0,
            rpm_limit INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_activity TEXT,
            allowed_models TEXT,
            max_input_tokens INTEGER,
            max_output_tokens INTEGER,
            max_tokens_per_request INTEGER,
            max_tokens_per_minute INTEGER,
            tokens_used_prompt INTEGER NOT NULL DEFAULT 0,
            tokens_used_completion INTEGER NOT NULL DEFAULT 0,
            allow_streaming INTEGER NOT NULL DEFAULT 1,
            allow_tools INTEGER NOT NULL DEFAULT 1,
            allowed_tool_names TEXT,
            allow_function_calling INTEGER NOT NULL DEFAULT 1,
            allow_vision INTEGER NOT NULL DEFAULT 0,
            strict_mode INTEGER NOT NULL DEFAULT 0,
            token_hash TEXT,
            token_expires_at TEXT,
            token_scope TEXT NOT NULL DEFAULT 'execution',
            allow_passthrough INTEGER NOT NULL DEFAULT 0,
            lifecycle_state TEXT NOT NULL DEFAULT 'READY',
            lifecycle_reason TEXT,
            CHECK (budget_micro >= 0),
            CHECK (spent_micro >= 0),
            CHECK (reserved_micro >= 0),
            CHECK (spent_micro <= budget_micro),
            CHECK (allow_streaming IN (0, 1)),
            CHECK (allow_tools IN (0, 1)),
            CHECK (allow_function_calling IN (0, 1)),
            CHECK (allow_vision IN (0, 1)),
            CHECK (strict_mode IN (0, 1)),
            CHECK (allow_passthrough IN (0, 1)),
            CHECK (token_scope IN ('execution', 'read-only')),
            CHECK (lifecycle_state IN {tuple(_LIFECYCLE_STATES)!r})
        )
    """,
    "pids": """
        CREATE TABLE IF NOT EXISTS pids (
            agent TEXT PRIMARY KEY,
            pid INTEGER,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(agent) REFERENCES agents(name) ON DELETE CASCADE
        )
    """,
    "events": """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT,
            action TEXT NOT NULL,
            cost_micro INTEGER NOT NULL DEFAULT 0,
            timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT,
            CHECK (cost_micro >= 0)
        )
    """,
    "executions": f"""
        CREATE TABLE IF NOT EXISTS executions (
            execution_id TEXT PRIMARY KEY,
            agent TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            policy_hash TEXT,
            route_hash TEXT,
            state TEXT NOT NULL,
            status_code INTEGER,
            response_body TEXT,
            error_body TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            provider_receipt INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            terminal_at TEXT,
            FOREIGN KEY(agent) REFERENCES agents(name) ON DELETE CASCADE,
            CHECK (retry_count >= 0),
            CHECK (provider_receipt >= 0),
            CHECK (state IN {tuple(_EXECUTION_STATES)!r})
        )
    """,
    "reservations": f"""
        CREATE TABLE IF NOT EXISTS reservations (
            execution_id TEXT PRIMARY KEY,
            agent TEXT NOT NULL,
            estimated_micro INTEGER NOT NULL,
            actual_micro INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL,
            reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            settled_at TEXT,
            expiry_at TEXT,
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id) ON DELETE CASCADE,
            FOREIGN KEY(agent) REFERENCES agents(name) ON DELETE CASCADE,
            CHECK (estimated_micro >= 0),
            CHECK (actual_micro >= 0),
            CHECK (state IN {tuple(_RESERVATION_STATES)!r})
        )
    """,
    "event_log": """
        CREATE TABLE IF NOT EXISTS event_log (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            execution_id TEXT,
            agent TEXT,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL,
            ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id) ON DELETE SET NULL
        )
    """,
    "tool_plugins": """
        CREATE TABLE IF NOT EXISTS tool_plugins (
            name TEXT PRIMARY KEY,
            version TEXT NOT NULL,
            entrypoint TEXT NOT NULL,
            package_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            manifest_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (enabled IN (0, 1))
        )
    """,
    "rate_windows": """
        CREATE TABLE IF NOT EXISTS rate_windows (
            agent TEXT PRIMARY KEY,
            window_start TEXT,
            request_count INTEGER NOT NULL DEFAULT 0,
            tokens_count INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(agent) REFERENCES agents(name) ON DELETE CASCADE,
            CHECK (request_count >= 0),
            CHECK (tokens_count >= 0)
        )
    """,
}

# Safe ALTER TABLE ADD COLUMN migrations for partially-upgraded databases.
_TABLE_COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "agents": [
        ("created_at", "TEXT"),
        ("last_activity", "TEXT"),
        ("allowed_models", "TEXT"),
        ("max_input_tokens", "INTEGER"),
        ("max_output_tokens", "INTEGER"),
        ("allow_streaming", "INTEGER DEFAULT 1"),
        ("allow_tools", "INTEGER DEFAULT 1"),
        ("allowed_tool_names", "TEXT"),
        ("allow_function_calling", "INTEGER DEFAULT 1"),
        ("allow_vision", "INTEGER DEFAULT 0"),
        ("strict_mode", "INTEGER DEFAULT 0"),
        ("token_hash", "TEXT"),
        ("token_expires_at", "TEXT"),
        ("token_scope", "TEXT DEFAULT 'execution'"),
        ("allow_passthrough", "INTEGER DEFAULT 0"),
        ("max_tokens_per_request", "INTEGER"),
        ("max_tokens_per_minute", "INTEGER"),
        ("tokens_used_prompt", "INTEGER DEFAULT 0"),
        ("tokens_used_completion", "INTEGER DEFAULT 0"),
        ("lifecycle_state", "TEXT DEFAULT 'READY'"),
        ("lifecycle_reason", "TEXT"),
    ],
    "events": [
        ("agent", "TEXT"),
        ("action", "TEXT"),
        ("cost_micro", "INTEGER DEFAULT 0"),
        ("metadata", "TEXT"),
        ("timestamp", "TEXT"),
    ],
    "executions": [
        ("agent", "TEXT"),
        ("endpoint", "TEXT"),
        ("request_hash", "TEXT"),
        ("state", "TEXT DEFAULT 'RESERVING'"),
        ("policy_hash", "TEXT"),
        ("route_hash", "TEXT"),
        ("status_code", "INTEGER"),
        ("response_body", "TEXT"),
        ("error_body", "TEXT"),
        ("retry_count", "INTEGER DEFAULT 0"),
        ("provider_receipt", "INTEGER DEFAULT 0"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
        ("terminal_at", "TEXT"),
    ],
    "reservations": [
        ("agent", "TEXT"),
        ("estimated_micro", "INTEGER DEFAULT 0"),
        ("state", "TEXT DEFAULT 'RESERVED'"),
        ("actual_micro", "INTEGER DEFAULT 0"),
        ("reserved_at", "TEXT"),
        ("settled_at", "TEXT"),
        ("expiry_at", "TEXT"),
    ],
    "event_log": [
        ("execution_id", "TEXT"),
        ("event_type", "TEXT DEFAULT ''"),
        ("payload_json", "TEXT DEFAULT '{}'"),
        ("prev_hash", "TEXT DEFAULT 'GENESIS'"),
        ("event_hash", "TEXT DEFAULT ''"),
        ("agent", "TEXT"),
        ("ts", "TEXT"),
    ],
    "tool_plugins": [
        ("version", "TEXT DEFAULT ''"),
        ("entrypoint", "TEXT DEFAULT ''"),
        ("package_path", "TEXT DEFAULT ''"),
        ("sha256", "TEXT DEFAULT ''"),
        ("manifest_json", "TEXT DEFAULT '{}'"),
        ("enabled", "INTEGER DEFAULT 0"),
        ("created_at", "TEXT"),
    ],
    "rate_windows": [
        ("window_start", "TEXT"),
        ("request_count", "INTEGER DEFAULT 0"),
        ("tokens_count", "INTEGER DEFAULT 0"),
    ],
}

_INDEX_DDL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_token_hash_unique ON agents(token_hash) WHERE token_hash IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_agents_lifecycle_state ON agents(lifecycle_state)",
    "CREATE INDEX IF NOT EXISTS idx_events_action_timestamp ON events(action, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_events_agent_action_timestamp ON events(agent, action, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_executions_agent_state_updated ON executions(agent, state, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_executions_updated_at ON executions(updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_reservations_agent_state ON reservations(agent, state)",
    "CREATE INDEX IF NOT EXISTS idx_reservations_state_expiry ON reservations(state, expiry_at)",
    "CREATE INDEX IF NOT EXISTS idx_event_log_execution ON event_log(execution_id)",
    "CREATE INDEX IF NOT EXISTS idx_event_log_event_type_ts ON event_log(event_type, ts)",
    "CREATE INDEX IF NOT EXISTS idx_tool_plugins_enabled_name ON tool_plugins(enabled, name)",
)


def _table_exists(cursor: sqlite3.Cursor, table_name: str) -> bool:
    row = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _existing_columns(cursor: sqlite3.Cursor, table_name: str) -> set[str]:
    rows = cursor.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _ensure_tables(cursor: sqlite3.Cursor) -> None:
    for ddl in _TABLE_DDL.values():
        cursor.execute(ddl)


def _apply_column_migrations(cursor: sqlite3.Cursor) -> None:
    for table_name, migrations in _TABLE_COLUMN_MIGRATIONS.items():
        if not _table_exists(cursor, table_name):
            continue
        existing = _existing_columns(cursor, table_name)
        for col_name, col_decl in migrations:
            if col_name in existing:
                continue
            try:
                cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_decl}")
                logger.info("Schema migration: added column", table=table_name, column=col_name)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise


def _normalize_agent_defaults(cursor: sqlite3.Cursor) -> None:
    lifecycle_tuple = ",".join(f"'{s}'" for s in _LIFECYCLE_STATES)
    cursor.execute(
        f"""
        UPDATE agents
        SET lifecycle_state = CASE
            WHEN lifecycle_state IN ({lifecycle_tuple}) THEN lifecycle_state
            ELSE 'READY'
        END,
            token_scope = CASE
                WHEN token_scope IN ('execution', 'read-only') THEN token_scope
                ELSE 'execution'
            END,
            allow_streaming = CASE WHEN allow_streaming IN (0, 1) THEN allow_streaming ELSE 1 END,
            allow_tools = CASE WHEN allow_tools IN (0, 1) THEN allow_tools ELSE 1 END,
            allow_function_calling = CASE WHEN allow_function_calling IN (0, 1) THEN allow_function_calling ELSE 1 END,
            allow_vision = CASE WHEN allow_vision IN (0, 1) THEN allow_vision ELSE 0 END,
            strict_mode = CASE WHEN strict_mode IN (0, 1) THEN strict_mode ELSE 0 END,
            allow_passthrough = CASE WHEN allow_passthrough IN (0, 1) THEN allow_passthrough ELSE 0 END,
            tokens_used_prompt = COALESCE(tokens_used_prompt, 0),
            tokens_used_completion = COALESCE(tokens_used_completion, 0),
            created_at = COALESCE(created_at, CURRENT_TIMESTAMP),
            reserved_micro = COALESCE(reserved_micro, 0),
            spent_micro = COALESCE(spent_micro, 0)
        """
    )


def _normalize_execution_defaults(cursor: sqlite3.Cursor) -> None:
    execution_tuple = ",".join(f"'{s}'" for s in _EXECUTION_STATES)
    reservation_tuple = ",".join(f"'{s}'" for s in _RESERVATION_STATES)
    cursor.execute(
        f"""
        UPDATE executions
        SET state = CASE
            WHEN state IN ({execution_tuple}) THEN state
            ELSE 'FAILED'
        END,
            retry_count = COALESCE(retry_count, 0),
            provider_receipt = COALESCE(provider_receipt, 0),
            created_at = COALESCE(created_at, CURRENT_TIMESTAMP),
            updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP)
        """
    )
    cursor.execute(
        f"""
        UPDATE reservations
        SET state = CASE
            WHEN state IN ({reservation_tuple}) THEN state
            ELSE 'RELEASED'
        END,
            actual_micro = COALESCE(actual_micro, 0),
            reserved_at = COALESCE(reserved_at, CURRENT_TIMESTAMP)
        """
    )


def _normalize_misc_defaults(cursor: sqlite3.Cursor) -> None:
    cursor.execute(
        """
        UPDATE events
        SET action = COALESCE(action, 'UNKNOWN'),
            cost_micro = COALESCE(cost_micro, 0),
            timestamp = COALESCE(timestamp, CURRENT_TIMESTAMP)
        """
    )
    cursor.execute(
        """
        UPDATE event_log
        SET event_type = COALESCE(event_type, ''),
            payload_json = COALESCE(payload_json, '{}'),
            prev_hash = COALESCE(prev_hash, 'GENESIS'),
            event_hash = COALESCE(event_hash, ''),
            ts = COALESCE(ts, CURRENT_TIMESTAMP)
        """
    )
    cursor.execute(
        """
        UPDATE tool_plugins
        SET enabled = CASE WHEN enabled IN (0, 1) THEN enabled ELSE 0 END,
            manifest_json = COALESCE(manifest_json, '{}'),
            created_at = COALESCE(created_at, CURRENT_TIMESTAMP)
        """
    )
    cursor.execute(
        """
        UPDATE rate_windows
        SET request_count = COALESCE(request_count, 0),
            tokens_count = COALESCE(tokens_count, 0)
        """
    )


def _create_indexes(cursor: sqlite3.Cursor) -> None:
    for ddl in _INDEX_DDL:
        cursor.execute(ddl)


def _validate_tables(cursor: sqlite3.Cursor, required_tables: Iterable[str]) -> None:
    existing = {
        row[0]
        for row in cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    missing = [table for table in required_tables if table not in existing]
    if missing:
        raise RuntimeError(f"Database schema incomplete. Missing tables: {', '.join(missing)}")


def init_db():
    """Initialize database schema and apply idempotent migrations."""
    logger.info("Initializing database", path=get_db_path(), target_schema_version=SCHEMA_VERSION)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        _ensure_tables(cursor)
        _apply_column_migrations(cursor)
        _normalize_agent_defaults(cursor)
        _normalize_execution_defaults(cursor)
        _normalize_misc_defaults(cursor)
        _create_indexes(cursor)
        _validate_tables(cursor, _REQUIRED_TABLES)
        cursor.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()

    logger.info("Database initialized successfully", schema_version=SCHEMA_VERSION)
