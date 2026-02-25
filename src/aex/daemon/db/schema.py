"""Database schema initialization and migrations for AEX (PostgreSQL)."""

from __future__ import annotations

from typing import Iterable

from ..utils.logging_config import StructuredLogger
from .connection import get_db_connection, get_db_path

logger = StructuredLogger(__name__)

SCHEMA_VERSION = 5

DEFAULT_TENANT_ID = "default"
DEFAULT_PROJECT_ID = "default"

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
    "tenants",
    "projects",
    "users",
    "memberships",
    "budgets",
    "quota_limits",
    "webhook_subscriptions",
    "webhook_deliveries",
)

_TABLE_DDL = {
    "agents": f"""
        CREATE TABLE IF NOT EXISTS agents (
            name TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT '{DEFAULT_TENANT_ID}',
            project_id TEXT NOT NULL DEFAULT '{DEFAULT_PROJECT_ID}',
            api_token TEXT UNIQUE NOT NULL,
            budget_micro BIGINT NOT NULL,
            spent_micro BIGINT NOT NULL DEFAULT 0,
            reserved_micro BIGINT NOT NULL DEFAULT 0,
            rpm_limit INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_activity TEXT,
            allowed_models TEXT,
            max_input_tokens INTEGER,
            max_output_tokens INTEGER,
            max_tokens_per_request INTEGER,
            max_tokens_per_minute INTEGER,
            tokens_used_prompt BIGINT NOT NULL DEFAULT 0,
            tokens_used_completion BIGINT NOT NULL DEFAULT 0,
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
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            project_id TEXT NOT NULL DEFAULT 'default',
            agent TEXT,
            action TEXT NOT NULL,
            cost_micro BIGINT NOT NULL DEFAULT 0,
            timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT,
            CHECK (cost_micro >= 0)
        )
    """,
    "executions": f"""
        CREATE TABLE IF NOT EXISTS executions (
            execution_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT '{DEFAULT_TENANT_ID}',
            project_id TEXT NOT NULL DEFAULT '{DEFAULT_PROJECT_ID}',
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
            tenant_id TEXT NOT NULL DEFAULT '{DEFAULT_TENANT_ID}',
            project_id TEXT NOT NULL DEFAULT '{DEFAULT_PROJECT_ID}',
            agent TEXT NOT NULL,
            estimated_micro BIGINT NOT NULL,
            actual_micro BIGINT NOT NULL DEFAULT 0,
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
            seq BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            project_id TEXT NOT NULL DEFAULT 'default',
            execution_id TEXT,
            agent TEXT,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL,
            chain_partition TEXT NOT NULL DEFAULT 'default',
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
            tenant_id TEXT NOT NULL DEFAULT 'default',
            project_id TEXT NOT NULL DEFAULT 'default',
            window_start TEXT,
            request_count INTEGER NOT NULL DEFAULT 0,
            tokens_count INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(agent) REFERENCES agents(name) ON DELETE CASCADE,
            CHECK (request_count >= 0),
            CHECK (tokens_count >= 0)
        )
    """,
    "tenants": """
        CREATE TABLE IF NOT EXISTS tenants (
            tenant_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (status IN ('ACTIVE', 'SUSPENDED', 'READ_ONLY'))
        )
    """,
    "projects": """
        CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            name TEXT NOT NULL,
            slug TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tenant_id, slug),
            FOREIGN KEY(tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            CHECK (status IN ('ACTIVE', 'ARCHIVED'))
        )
    """,
    "users": """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            display_name TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "memberships": """
        CREATE TABLE IF NOT EXISTS memberships (
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(tenant_id, user_id),
            FOREIGN KEY(tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            CHECK (role IN ('OWNER', 'ADMIN', 'DEVELOPER', 'VIEWER'))
        )
    """,
    "budgets": """
        CREATE TABLE IF NOT EXISTS budgets (
            budget_key TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            project_id TEXT,
            agent TEXT,
            scope_type TEXT NOT NULL,
            period TEXT NOT NULL DEFAULT 'TOTAL',
            limit_micro BIGINT NOT NULL,
            spent_micro BIGINT NOT NULL DEFAULT 0,
            reserved_micro BIGINT NOT NULL DEFAULT 0,
            version BIGINT NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            FOREIGN KEY(agent) REFERENCES agents(name) ON DELETE CASCADE,
            CHECK (scope_type IN ('TENANT', 'PROJECT', 'AGENT')),
            CHECK (period IN ('TOTAL', 'DAILY', 'MONTHLY')),
            CHECK (limit_micro >= 0),
            CHECK (spent_micro >= 0),
            CHECK (reserved_micro >= 0)
        )
    """,
    "quota_limits": """
        CREATE TABLE IF NOT EXISTS quota_limits (
            scope_key TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            project_id TEXT,
            agent TEXT,
            rpm_limit INTEGER,
            tpm_limit INTEGER,
            concurrent_limit INTEGER,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            FOREIGN KEY(agent) REFERENCES agents(name) ON DELETE CASCADE,
            CHECK (rpm_limit IS NULL OR rpm_limit >= 0),
            CHECK (tpm_limit IS NULL OR tpm_limit >= 0),
            CHECK (concurrent_limit IS NULL OR concurrent_limit >= 0)
        )
    """,
    "webhook_subscriptions": """
        CREATE TABLE IF NOT EXISTS webhook_subscriptions (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            url TEXT NOT NULL,
            event_types_json TEXT NOT NULL DEFAULT '[]',
            secret TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            CHECK (enabled IN (0, 1))
        )
    """,
    "webhook_deliveries": """
        CREATE TABLE IF NOT EXISTS webhook_deliveries (
            id BIGSERIAL PRIMARY KEY,
            subscription_id BIGINT NOT NULL,
            tenant_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            execution_id TEXT,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            attempts INTEGER NOT NULL DEFAULT 0,
            http_status INTEGER,
            error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            delivered_at TEXT,
            FOREIGN KEY(subscription_id) REFERENCES webhook_subscriptions(id) ON DELETE CASCADE,
            CHECK (status IN ('PENDING', 'DELIVERED', 'FAILED')),
            CHECK (attempts >= 0)
        )
    """,
    "aex_schema_meta": """
        CREATE TABLE IF NOT EXISTS aex_schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
}

# Safe ALTER TABLE ADD COLUMN migrations for partially-upgraded databases.
_TABLE_COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "agents": [
        ("tenant_id", f"TEXT DEFAULT '{DEFAULT_TENANT_ID}'"),
        ("project_id", f"TEXT DEFAULT '{DEFAULT_PROJECT_ID}'"),
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
        ("tokens_used_prompt", "BIGINT DEFAULT 0"),
        ("tokens_used_completion", "BIGINT DEFAULT 0"),
        ("lifecycle_state", "TEXT DEFAULT 'READY'"),
        ("lifecycle_reason", "TEXT"),
    ],
    "events": [
        ("tenant_id", f"TEXT DEFAULT '{DEFAULT_TENANT_ID}'"),
        ("project_id", f"TEXT DEFAULT '{DEFAULT_PROJECT_ID}'"),
        ("agent", "TEXT"),
        ("action", "TEXT"),
        ("cost_micro", "BIGINT DEFAULT 0"),
        ("metadata", "TEXT"),
        ("timestamp", "TEXT"),
    ],
    "executions": [
        ("tenant_id", f"TEXT DEFAULT '{DEFAULT_TENANT_ID}'"),
        ("project_id", f"TEXT DEFAULT '{DEFAULT_PROJECT_ID}'"),
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
        ("tenant_id", f"TEXT DEFAULT '{DEFAULT_TENANT_ID}'"),
        ("project_id", f"TEXT DEFAULT '{DEFAULT_PROJECT_ID}'"),
        ("agent", "TEXT"),
        ("estimated_micro", "BIGINT DEFAULT 0"),
        ("state", "TEXT DEFAULT 'RESERVED'"),
        ("actual_micro", "BIGINT DEFAULT 0"),
        ("reserved_at", "TEXT"),
        ("settled_at", "TEXT"),
        ("expiry_at", "TEXT"),
    ],
    "event_log": [
        ("tenant_id", f"TEXT DEFAULT '{DEFAULT_TENANT_ID}'"),
        ("project_id", f"TEXT DEFAULT '{DEFAULT_PROJECT_ID}'"),
        ("chain_partition", "TEXT DEFAULT 'default'"),
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
        ("tenant_id", f"TEXT DEFAULT '{DEFAULT_TENANT_ID}'"),
        ("project_id", f"TEXT DEFAULT '{DEFAULT_PROJECT_ID}'"),
        ("window_start", "TEXT"),
        ("request_count", "INTEGER DEFAULT 0"),
        ("tokens_count", "INTEGER DEFAULT 0"),
    ],
}

_INDEX_DDL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_token_hash_unique ON agents(token_hash) WHERE token_hash IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_agents_tenant_project ON agents(tenant_id, project_id, name)",
    "CREATE INDEX IF NOT EXISTS idx_agents_lifecycle_state ON agents(lifecycle_state)",
    "CREATE INDEX IF NOT EXISTS idx_events_action_timestamp ON events(action, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_events_agent_action_timestamp ON events(agent, action, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_events_tenant_project_timestamp ON events(tenant_id, project_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_executions_agent_state_updated ON executions(agent, state, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_executions_tenant_state_updated ON executions(tenant_id, state, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_executions_updated_at ON executions(updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_reservations_agent_state ON reservations(agent, state)",
    "CREATE INDEX IF NOT EXISTS idx_reservations_state_expiry ON reservations(state, expiry_at)",
    "CREATE INDEX IF NOT EXISTS idx_reservations_tenant_state_expiry ON reservations(tenant_id, state, expiry_at)",
    "CREATE INDEX IF NOT EXISTS idx_event_log_execution ON event_log(execution_id)",
    "CREATE INDEX IF NOT EXISTS idx_event_log_event_type_ts ON event_log(event_type, ts)",
    "CREATE INDEX IF NOT EXISTS idx_event_log_tenant_seq ON event_log(tenant_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_tool_plugins_enabled_name ON tool_plugins(enabled, name)",
    "CREATE INDEX IF NOT EXISTS idx_budgets_tenant_scope ON budgets(tenant_id, scope_type, period)",
    "CREATE INDEX IF NOT EXISTS idx_quota_limits_tenant ON quota_limits(tenant_id, scope_key)",
    "CREATE INDEX IF NOT EXISTS idx_webhook_subscriptions_tenant_enabled ON webhook_subscriptions(tenant_id, enabled)",
    "CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_tenant_status ON webhook_deliveries(tenant_id, status, created_at)",
)


def _table_exists(cursor, table_name: str) -> bool:
    row = cursor.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = ?
        """,
        (table_name,),
    ).fetchone()
    return bool(row)


def _existing_columns(cursor, table_name: str) -> set[str]:
    rows = cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ?
        """,
        (table_name,),
    ).fetchall()
    return {str(row["column_name"]) for row in rows}


def _ensure_tables(cursor) -> None:
    for ddl in _TABLE_DDL.values():
        cursor.execute(ddl)


def _apply_column_migrations(cursor) -> None:
    for table_name, migrations in _TABLE_COLUMN_MIGRATIONS.items():
        if not _table_exists(cursor, table_name):
            continue
        existing = _existing_columns(cursor, table_name)
        for col_name, col_decl in migrations:
            if col_name in existing:
                continue
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {col_name} {col_decl}")
            logger.info("Schema migration: added column", table=table_name, column=col_name)


def _normalize_agent_defaults(cursor) -> None:
    lifecycle_tuple = ",".join(f"'{s}'" for s in _LIFECYCLE_STATES)
    cursor.execute(
        f"""
        UPDATE agents
        SET lifecycle_state = CASE
            WHEN lifecycle_state IN ({lifecycle_tuple}) THEN lifecycle_state
            ELSE 'READY'
        END,
            tenant_id = COALESCE(NULLIF(tenant_id, ''), '{DEFAULT_TENANT_ID}'),
            project_id = COALESCE(NULLIF(project_id, ''), '{DEFAULT_PROJECT_ID}'),
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
            created_at = COALESCE(created_at, CAST(CURRENT_TIMESTAMP AS TEXT)),
            reserved_micro = COALESCE(reserved_micro, 0),
            spent_micro = COALESCE(spent_micro, 0)
        """
    )


def _normalize_execution_defaults(cursor) -> None:
    execution_tuple = ",".join(f"'{s}'" for s in _EXECUTION_STATES)
    reservation_tuple = ",".join(f"'{s}'" for s in _RESERVATION_STATES)
    cursor.execute(
        f"""
        UPDATE executions
        SET state = CASE
            WHEN state IN ({execution_tuple}) THEN state
            ELSE 'FAILED'
        END,
            tenant_id = COALESCE(NULLIF(tenant_id, ''), '{DEFAULT_TENANT_ID}'),
            project_id = COALESCE(NULLIF(project_id, ''), '{DEFAULT_PROJECT_ID}'),
            retry_count = COALESCE(retry_count, 0),
            provider_receipt = COALESCE(provider_receipt, 0),
            created_at = COALESCE(created_at, CAST(CURRENT_TIMESTAMP AS TEXT)),
            updated_at = COALESCE(updated_at, CAST(CURRENT_TIMESTAMP AS TEXT))
        """
    )
    cursor.execute(
        f"""
        UPDATE reservations
        SET state = CASE
            WHEN state IN ({reservation_tuple}) THEN state
            ELSE 'RELEASED'
        END,
            tenant_id = COALESCE(NULLIF(tenant_id, ''), '{DEFAULT_TENANT_ID}'),
            project_id = COALESCE(NULLIF(project_id, ''), '{DEFAULT_PROJECT_ID}'),
            actual_micro = COALESCE(actual_micro, 0),
            reserved_at = COALESCE(reserved_at, CAST(CURRENT_TIMESTAMP AS TEXT))
        """
    )


def _normalize_misc_defaults(cursor) -> None:
    cursor.execute(
        f"""
        UPDATE events
        SET action = COALESCE(action, 'UNKNOWN'),
            tenant_id = COALESCE(NULLIF(tenant_id, ''), '{DEFAULT_TENANT_ID}'),
            project_id = COALESCE(NULLIF(project_id, ''), '{DEFAULT_PROJECT_ID}'),
            cost_micro = COALESCE(cost_micro, 0),
            timestamp = COALESCE(timestamp, CAST(CURRENT_TIMESTAMP AS TEXT))
        """
    )
    cursor.execute(
        f"""
        UPDATE event_log
        SET event_type = COALESCE(event_type, ''),
            tenant_id = COALESCE(NULLIF(tenant_id, ''), '{DEFAULT_TENANT_ID}'),
            project_id = COALESCE(NULLIF(project_id, ''), '{DEFAULT_PROJECT_ID}'),
            payload_json = COALESCE(payload_json, '{{}}'),
            prev_hash = COALESCE(prev_hash, 'GENESIS'),
            event_hash = COALESCE(event_hash, ''),
            chain_partition = COALESCE(NULLIF(chain_partition, ''), 'default'),
            ts = COALESCE(ts, CAST(CURRENT_TIMESTAMP AS TEXT))
        """
    )
    cursor.execute(
        """
        UPDATE tool_plugins
        SET enabled = CASE WHEN enabled IN (0, 1) THEN enabled ELSE 0 END,
            manifest_json = COALESCE(manifest_json, '{}'),
            created_at = COALESCE(created_at, CAST(CURRENT_TIMESTAMP AS TEXT))
        """
    )
    cursor.execute(
        f"""
        UPDATE rate_windows
        SET request_count = COALESCE(request_count, 0),
            tenant_id = COALESCE(NULLIF(tenant_id, ''), '{DEFAULT_TENANT_ID}'),
            project_id = COALESCE(NULLIF(project_id, ''), '{DEFAULT_PROJECT_ID}'),
            tokens_count = COALESCE(tokens_count, 0)
        """
    )


def _seed_multi_tenant_defaults(cursor) -> None:
    cursor.execute(
        """
        INSERT INTO tenants (tenant_id, name, slug, status)
        VALUES (?, ?, ?, 'ACTIVE')
        ON CONFLICT(tenant_id) DO NOTHING
        """,
        (DEFAULT_TENANT_ID, "Default Tenant", DEFAULT_TENANT_ID),
    )
    cursor.execute(
        """
        INSERT INTO projects (project_id, tenant_id, name, slug, status)
        VALUES (?, ?, ?, ?, 'ACTIVE')
        ON CONFLICT(project_id) DO NOTHING
        """,
        (DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID, "Default Project", DEFAULT_PROJECT_ID),
    )

    cursor.execute(
        """
        UPDATE agents
        SET tenant_id = COALESCE(NULLIF(tenant_id, ''), ?),
            project_id = COALESCE(NULLIF(project_id, ''), ?)
        """,
        (DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID),
    )
    cursor.execute(
        """
        UPDATE executions
        SET tenant_id = COALESCE(NULLIF(tenant_id, ''), ?),
            project_id = COALESCE(NULLIF(project_id, ''), ?)
        """,
        (DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID),
    )
    cursor.execute(
        """
        UPDATE reservations
        SET tenant_id = COALESCE(NULLIF(tenant_id, ''), ?),
            project_id = COALESCE(NULLIF(project_id, ''), ?)
        """,
        (DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID),
    )

    agent_rows = cursor.execute(
        """
        SELECT name,
               COALESCE(NULLIF(tenant_id, ''), ?) AS tenant_id,
               COALESCE(NULLIF(project_id, ''), ?) AS project_id,
               budget_micro,
               spent_micro,
               reserved_micro,
               rpm_limit,
               max_tokens_per_minute
        FROM agents
        """,
        (DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID),
    ).fetchall()
    for row in agent_rows:
        budget_key = f"agent:{row['tenant_id']}:{row['project_id']}:{row['name']}"
        cursor.execute(
            """
            INSERT INTO budgets (
                budget_key, tenant_id, project_id, agent, scope_type, period,
                limit_micro, spent_micro, reserved_micro
            ) VALUES (?, ?, ?, ?, 'AGENT', 'TOTAL', ?, ?, ?)
            ON CONFLICT(budget_key) DO UPDATE SET
                limit_micro = excluded.limit_micro,
                spent_micro = excluded.spent_micro,
                reserved_micro = excluded.reserved_micro,
                version = budgets.version + 1
            """,
            (
                budget_key,
                row["tenant_id"],
                row["project_id"],
                row["name"],
                int(row["budget_micro"] or 0),
                int(row["spent_micro"] or 0),
                int(row["reserved_micro"] or 0),
            ),
        )

        quota_key = f"agent:{row['tenant_id']}:{row['project_id']}:{row['name']}"
        cursor.execute(
            """
            INSERT INTO quota_limits (
                scope_key, tenant_id, project_id, agent, rpm_limit, tpm_limit
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope_key) DO UPDATE SET
                rpm_limit = excluded.rpm_limit,
                tpm_limit = excluded.tpm_limit,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                quota_key,
                row["tenant_id"],
                row["project_id"],
                row["name"],
                int(row["rpm_limit"] or 0),
                row["max_tokens_per_minute"],
            ),
        )


def _create_indexes(cursor) -> None:
    for ddl in _INDEX_DDL:
        cursor.execute(ddl)


def _validate_tables(cursor, required_tables: Iterable[str]) -> None:
    rows = cursor.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        """
    ).fetchall()
    existing = {str(row["table_name"]) for row in rows}
    missing = [table for table in required_tables if table not in existing]
    if missing:
        raise RuntimeError(f"Database schema incomplete. Missing tables: {', '.join(missing)}")


def _mark_schema_version(cursor) -> None:
    cursor.execute(
        """
        INSERT INTO aex_schema_meta (key, value)
        VALUES ('schema_version', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """,
        (str(SCHEMA_VERSION),),
    )


def init_db():
    """Initialize database schema and apply idempotent migrations."""
    logger.info("Initializing database", path=get_db_path(), target_schema_version=SCHEMA_VERSION)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN")
        _ensure_tables(cursor)
        _apply_column_migrations(cursor)
        _normalize_agent_defaults(cursor)
        _normalize_execution_defaults(cursor)
        _normalize_misc_defaults(cursor)
        _seed_multi_tenant_defaults(cursor)
        _create_indexes(cursor)
        _validate_tables(cursor, _REQUIRED_TABLES)
        _mark_schema_version(cursor)
        conn.commit()

    logger.info("Database initialized successfully", schema_version=SCHEMA_VERSION)
