"""AEX admin endpoints — health/readiness, metrics, dashboard, config reload."""

import os
import json
import re
import secrets
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.concurrency import run_in_threadpool
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from ... import __version__
from ..auth import hash_token
from ..db import check_db_integrity, get_db_connection, get_db_path, init_db
from ..control.lifecycle import transition_agent_state
from ..frontend import activity_snapshot, dashboard_payload
from ..ledger.events import append_compat_event, append_hash_event
from ..ledger.replay import replay_ledger_balances, verify_hash_chain
from ..observability import collect_active_alerts, liveness_report, readiness_report, summarize_alerts
from ..policies import create_policy, delete_policy, list_policies, load_policy
from ..sandbox.plugins import install_plugin, list_plugins, set_plugin_enabled
from ..utils.invariants import run_all_checks
from ..utils.config_loader import config_loader
from ..utils.logging_config import StructuredLogger
from ..utils.metrics import get_metrics

logger = StructuredLogger(__name__)
router = APIRouter()


class OperatorControlRequest(BaseModel):
    reason: str = Field(default="operator request", min_length=3, max_length=240)


class QuickstartCreateRequest(BaseModel):
    tenant_id: str = Field(default="default", min_length=1, max_length=120)
    project_id: str = Field(default="default", min_length=1, max_length=120)
    agent_name: str | None = Field(default=None, max_length=120)
    budget_usd: float = Field(default=20.0, ge=0.01, le=1_000_000.0)
    rpm_limit: int = Field(default=240, ge=1, le=100_000)
    allow_passthrough: bool = Field(default=False)
    allowed_models: list[str] | None = Field(default=None)


class TenantUpsertRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1, max_length=120)
    name: str = Field(default="", max_length=200)


class ProjectUpsertRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1, max_length=120)
    project_id: str = Field(..., min_length=1, max_length=120)
    name: str = Field(default="", max_length=200)


class AgentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    budget_usd: float = Field(default=20.0, ge=0.01, le=1_000_000.0)
    rpm_limit: int = Field(default=240, ge=1, le=100_000)
    tenant_id: str = Field(default="default", min_length=1, max_length=120)
    project_id: str = Field(default="default", min_length=1, max_length=120)
    allow_passthrough: bool = Field(default=False)
    allow_streaming: bool = Field(default=True)
    allow_tools: bool = Field(default=True)
    allow_function_calling: bool = Field(default=True)
    allow_vision: bool = Field(default=False)
    strict_mode: bool = Field(default=False)
    allowed_models: list[str] | None = Field(default=None)
    allowed_tool_names: list[str] | None = Field(default=None)
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_tokens_per_request: int | None = None
    max_tokens_per_minute: int | None = None
    token_scope: str = Field(default="execution")
    token_ttl_hours: float | None = Field(default=None, ge=0.001, le=24_000.0)


class AgentRotateTokenRequest(BaseModel):
    token_ttl_hours: float | None = Field(default=None, ge=0.001, le=24_000.0)


class AgentStateRequest(BaseModel):
    to_state: str = Field(..., min_length=3, max_length=40)
    reason: str = Field(default="operator transition", min_length=3, max_length=240)


class PolicyUpsertRequest(BaseModel):
    policy_id: str = Field(..., min_length=1, max_length=64)
    budget_usd: float = Field(default=50.0, ge=0.0, le=1_000_000.0)
    allow_tools: list[str] | None = Field(default=None)
    deny_tools: list[str] | None = Field(default=None)
    max_steps: int = Field(default=100, ge=1, le=100_000)
    dangerous_ops: bool = Field(default=False)
    require_approval_for_destructive_ops: bool = Field(default=True)


class DbTestRequest(BaseModel):
    dsn: str = Field(..., min_length=12, max_length=4096)


class DbSetRequest(BaseModel):
    dsn: str = Field(..., min_length=12, max_length=4096)
    verify_connection: bool = Field(default=True)


class MigrateSnapshotRequest(BaseModel):
    tag: str | None = Field(default=None, max_length=64)


class MigrateApplyRequest(BaseModel):
    snapshot_first: bool = Field(default=True)
    tag: str | None = Field(default=None, max_length=64)


class MigrateRollbackRequest(BaseModel):
    tag: str = Field(..., min_length=3, max_length=64)


class PluginInstallRequest(BaseModel):
    manifest_path: str = Field(..., min_length=1, max_length=1024)
    package_path: str = Field(..., min_length=1, max_length=1024)


_SNAPSHOT_SCHEMA = "aex_backup"
_MIGRATION_TABLES = (
    "webhook_deliveries",
    "webhook_subscriptions",
    "memberships",
    "users",
    "quota_limits",
    "budgets",
    "rate_windows",
    "pids",
    "event_log",
    "reservations",
    "executions",
    "events",
    "tool_plugins",
    "agents",
    "projects",
    "tenants",
)


def _safe_tag(value: str) -> str:
    tag = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "")).strip("_").lower()
    if not tag:
        raise HTTPException(status_code=400, detail="snapshot tag cannot be empty")
    if not re.match(r"^[a-z][a-z0-9_]{2,62}$", tag):
        raise HTTPException(status_code=400, detail="tag must match: ^[a-z][a-z0-9_]{2,62}$")
    return tag


def _default_tag(prefix: str = "snap") -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return _safe_tag(f"{prefix}_{ts}")


def _table_exists(conn, schema: str, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = ? AND table_name = ?
        """,
        (schema, table),
    ).fetchone()
    return bool(row)


def _snapshot_table_name(table: str, tag: str) -> str:
    return f"{table}__{tag}"


def _reset_sequences(conn) -> None:
    for table in _MIGRATION_TABLES:
        seq_rows = conn.execute(
            f"""
            SELECT
              a.attname AS column_name,
              pg_get_serial_sequence('public."{table}"', a.attname) AS seq_name
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = ?
              AND a.attnum > 0
              AND NOT a.attisdropped
            """,
            (table,),
        ).fetchall()
        for row in seq_rows:
            seq_name = row["seq_name"]
            col = row["column_name"]
            if not seq_name:
                continue
            max_row = conn.execute(
                f'SELECT COALESCE(MAX("{col}"), 0) AS v FROM public."{table}"'
            ).fetchone()
            next_val = int(max_row["v"] or 0) + 1
            conn.execute("SELECT setval(?::regclass, ?, false)", (seq_name, next_val))


def _create_snapshot(conn, tag: str) -> None:
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_SNAPSHOT_SCHEMA}")
    for table in _MIGRATION_TABLES:
        snap_table = _snapshot_table_name(table, tag)
        conn.execute(f'DROP TABLE IF EXISTS {_SNAPSHOT_SCHEMA}."{snap_table}"')
        conn.execute(
            f'CREATE TABLE {_SNAPSHOT_SCHEMA}."{snap_table}" AS TABLE public."{table}"'
        )


def _mask_dsn(dsn: str) -> str:
    raw = (dsn or "").strip()
    if "://" in raw and "@" in raw:
        scheme, rest = raw.split("://", 1)
        _, host = rest.rsplit("@", 1)
        return f"{scheme}://***@{host}"
    return "***"


def _sanitize_slug(value: str, fallback: str) -> str:
    raw = (value or "").strip().lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-")
    return slug or fallback


def _external_base_url(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or "").strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").strip()
    if proto and host:
        return f"{proto}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def _require_control_key(request: Request) -> None:
    expected = (os.getenv("AEX_ADMIN_CONTROL_KEY") or "").strip()
    if not expected:
        return
    provided = (request.headers.get("x-aex-admin-key") or "").strip()
    if not provided or provided != expected:
        raise HTTPException(status_code=403, detail="Admin control key is required")


def _bulk_set_agent_state(target_state: str, reason: str, event_type: str) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        rows = cursor.execute(
            """
            SELECT name, tenant_id, project_id, lifecycle_state
            FROM agents
            WHERE lifecycle_state <> 'DECOMMISSIONED'
            ORDER BY name ASC
            """
        ).fetchall()

        updated = 0
        skipped = 0
        for row in rows:
            current = str(row["lifecycle_state"] or "READY").upper()
            if current == target_state:
                skipped += 1
                continue

            cursor.execute(
                """
                UPDATE agents
                SET lifecycle_state = ?, lifecycle_reason = ?, last_activity = CURRENT_TIMESTAMP
                WHERE name = ?
                """,
                (target_state, reason, row["name"]),
            )
            append_hash_event(
                conn,
                execution_id=None,
                agent=row["name"],
                tenant_id=row["tenant_id"],
                project_id=row["project_id"],
                event_type=event_type,
                payload={"from": current, "to": target_state, "reason": reason},
            )
            append_compat_event(
                conn,
                agent=row["name"],
                tenant_id=row["tenant_id"],
                project_id=row["project_id"],
                action="AGENT_STATE",
                metadata={"from": current, "to": target_state, "reason": reason, "source": "admin.control"},
            )
            updated += 1

        conn.commit()
    return {"target_state": target_state, "updated_agents": updated, "already_in_state": skipped}


def _kill_all_agents(reason: str) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        rows = cursor.execute(
            """
            SELECT a.name, a.tenant_id, a.project_id, a.lifecycle_state, p.pid
            FROM agents a
            LEFT JOIN pids p ON p.agent = a.name
            WHERE a.lifecycle_state <> 'DECOMMISSIONED'
            ORDER BY a.name ASC
            """
        ).fetchall()

        signaled = 0
        stale = 0
        stopped = 0
        failures: list[dict] = []

        for row in rows:
            agent = row["name"]
            tenant_id = row["tenant_id"]
            project_id = row["project_id"]
            previous_state = str(row["lifecycle_state"] or "READY").upper()
            pid = row["pid"]

            result = "no_pid"
            if pid is not None:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                    signaled += 1
                    result = "signaled"
                except ProcessLookupError:
                    stale += 1
                    result = "stale"
                except Exception as exc:
                    result = "error"
                    failures.append({"agent": agent, "pid": int(pid), "error": str(exc)})
                cursor.execute("DELETE FROM pids WHERE agent = ?", (agent,))
                append_compat_event(
                    conn,
                    agent=agent,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    action="PROCESS_KILLED",
                    metadata={"pid": int(pid), "result": result, "reason": reason},
                )

            if previous_state != "STOPPED":
                cursor.execute(
                    """
                    UPDATE agents
                    SET lifecycle_state = 'STOPPED', lifecycle_reason = ?, last_activity = CURRENT_TIMESTAMP
                    WHERE name = ?
                    """,
                    (reason, agent),
                )
                stopped += 1

            append_hash_event(
                conn,
                execution_id=None,
                agent=agent,
                tenant_id=tenant_id,
                project_id=project_id,
                event_type="agent.kill_all",
                payload={
                    "from": previous_state,
                    "to": "STOPPED",
                    "reason": reason,
                    "pid": pid,
                    "result": result,
                },
            )
            append_compat_event(
                conn,
                agent=agent,
                tenant_id=tenant_id,
                project_id=project_id,
                action="AGENT_STATE",
                metadata={
                    "from": previous_state,
                    "to": "STOPPED",
                    "reason": reason,
                    "source": "admin.control",
                },
            )

        conn.commit()
    return {
        "target_state": "STOPPED",
        "updated_agents": stopped,
        "pids_signaled": signaled,
        "pids_stale": stale,
        "errors": failures,
    }


def _create_quickstart_bundle(payload: QuickstartCreateRequest, request: Request) -> dict:
    tenant_id = _sanitize_slug(payload.tenant_id, "default")
    project_id = _sanitize_slug(payload.project_id, "default")
    suffix = secrets.token_hex(3)
    agent_name = _sanitize_slug(
        payload.agent_name or f"{tenant_id}-{project_id}-{suffix}",
        f"agent-{suffix}",
    )

    budget_micro = int(payload.budget_usd * 1_000_000)
    api_token = secrets.token_hex(16)
    token_sha = hash_token(api_token)
    allowed_models_json = json.dumps(payload.allowed_models) if payload.allowed_models else None

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN")
        cursor.execute(
            """
            INSERT INTO tenants (tenant_id, name, slug, status)
            VALUES (?, ?, ?, 'ACTIVE')
            ON CONFLICT(tenant_id) DO NOTHING
            """,
            (tenant_id, f"Tenant {tenant_id}", tenant_id),
        )
        cursor.execute(
            """
            INSERT INTO projects (project_id, tenant_id, name, slug, status)
            VALUES (?, ?, ?, ?, 'ACTIVE')
            ON CONFLICT(project_id) DO NOTHING
            """,
            (project_id, tenant_id, f"Project {project_id}", project_id),
        )
        existing = cursor.execute("SELECT 1 FROM agents WHERE name = ?", (agent_name,)).fetchone()
        if existing:
            conn.rollback()
            raise HTTPException(status_code=409, detail=f"Agent '{agent_name}' already exists")

        cursor.execute(
            """
            INSERT INTO agents (
                name, tenant_id, project_id, api_token, token_hash,
                budget_micro, rpm_limit, allowed_models, allow_passthrough
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent_name,
                tenant_id,
                project_id,
                api_token,
                token_sha,
                budget_micro,
                int(payload.rpm_limit),
                allowed_models_json,
                1 if payload.allow_passthrough else 0,
            ),
        )
        conn.commit()

    base_url = _external_base_url(request)
    env_block = "\n".join(
        [
            f"AEX_ENABLE=1",
            f"AEX_MODE=proxy",
            f"AEX_BASE_URL={base_url}",
            f"AEX_API_KEY={api_token}",
            f"AEX_TENANT={tenant_id}",
            f"AEX_PROJECT={project_id}",
            f"OPENAI_BASE_URL={base_url}/v1",
            f"OPENAI_API_KEY={api_token}",
        ]
    )
    smoke_curl = (
        f"curl -sS -X POST '{base_url}/v1/chat/completions' "
        f"-H 'Authorization: Bearer {api_token}' "
        f"-H 'Content-Type: application/json' "
        f"-d '{{\"model\":\"gpt-oss-20b\",\"messages\":[{{\"role\":\"user\",\"content\":\"reply with ok\"}}],\"max_tokens\":8}}'"
    )
    return {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "agent_name": agent_name,
        "api_token": api_token,
        "base_url": base_url,
        "env_block": env_block,
        "smoke_curl": smoke_curl,
        "framework_mode": "openai_compatible_env",
    }


def _parse_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
    except Exception:
        pass
    return []


def _serialize_agent_row(row: dict) -> dict:
    payload = dict(row)
    payload["allowed_models"] = _parse_json_list(payload.get("allowed_models"))
    payload["allowed_tool_names"] = _parse_json_list(payload.get("allowed_tool_names"))
    if payload.get("api_token"):
        payload["api_token_masked"] = f"{payload['api_token'][:6]}...{payload['api_token'][-4:]}"
        payload.pop("api_token", None)
    return payload


def _create_agent(payload: AgentCreateRequest) -> dict:
    tenant_id = _sanitize_slug(payload.tenant_id, "default")
    project_id = _sanitize_slug(payload.project_id, "default")
    name = _sanitize_slug(payload.name, "agent")
    if payload.token_scope not in ("execution", "read-only"):
        raise HTTPException(status_code=400, detail="token_scope must be 'execution' or 'read-only'")

    token = secrets.token_hex(16)
    token_sha = hash_token(token)
    budget_micro = int(payload.budget_usd * 1_000_000)
    expires_at = None
    if payload.token_ttl_hours is not None:
        expires_at = (datetime.now(UTC) + timedelta(hours=float(payload.token_ttl_hours))).isoformat()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN")
        cursor.execute(
            """
            INSERT INTO tenants (tenant_id, name, slug, status)
            VALUES (?, ?, ?, 'ACTIVE')
            ON CONFLICT(tenant_id) DO NOTHING
            """,
            (tenant_id, f"Tenant {tenant_id}", tenant_id),
        )
        cursor.execute(
            """
            INSERT INTO projects (project_id, tenant_id, name, slug, status)
            VALUES (?, ?, ?, ?, 'ACTIVE')
            ON CONFLICT(project_id) DO NOTHING
            """,
            (project_id, tenant_id, f"Project {project_id}", project_id),
        )
        exists = cursor.execute("SELECT 1 FROM agents WHERE name = ?", (name,)).fetchone()
        if exists:
            conn.rollback()
            raise HTTPException(status_code=409, detail=f"Agent '{name}' already exists")

        cursor.execute(
            """
            INSERT INTO agents (
                name, tenant_id, project_id, api_token, token_hash, token_expires_at, token_scope,
                budget_micro, rpm_limit,
                allowed_models, max_input_tokens, max_output_tokens,
                max_tokens_per_request, max_tokens_per_minute,
                allow_streaming, allow_tools, allowed_tool_names,
                allow_function_calling, allow_vision, strict_mode, allow_passthrough
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                tenant_id,
                project_id,
                token,
                token_sha,
                expires_at,
                payload.token_scope,
                budget_micro,
                int(payload.rpm_limit),
                json.dumps(payload.allowed_models) if payload.allowed_models else None,
                payload.max_input_tokens,
                payload.max_output_tokens,
                payload.max_tokens_per_request,
                payload.max_tokens_per_minute,
                1 if payload.allow_streaming else 0,
                1 if payload.allow_tools else 0,
                json.dumps(payload.allowed_tool_names) if payload.allowed_tool_names else None,
                1 if payload.allow_function_calling else 0,
                1 if payload.allow_vision else 0,
                1 if payload.strict_mode else 0,
                1 if payload.allow_passthrough else 0,
            ),
        )
        conn.commit()

    return {
        "name": name,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "api_token": token,
        "token_expires_at": expires_at,
        "token_scope": payload.token_scope,
        "budget_usd": payload.budget_usd,
        "rpm_limit": payload.rpm_limit,
    }


def _rotate_agent_token(name: str, ttl_hours: float | None) -> dict:
    token = secrets.token_hex(16)
    token_sha = hash_token(token)
    expires_at = None
    if ttl_hours is not None:
        expires_at = (datetime.now(UTC) + timedelta(hours=float(ttl_hours))).isoformat()

    with get_db_connection() as conn:
        cursor = conn.execute(
            "UPDATE agents SET api_token = ?, token_hash = ?, token_expires_at = ? WHERE name = ?",
            (token, token_sha, expires_at, name),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
        conn.commit()
    return {"name": name, "api_token": token, "token_expires_at": expires_at}


def _delete_agent(name: str) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN")
        row = cursor.execute("SELECT name FROM agents WHERE name = ?", (name,)).fetchone()
        if not row:
            conn.rollback()
            raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
        cursor.execute("DELETE FROM pids WHERE agent = ?", (name,))
        cursor.execute("DELETE FROM rate_windows WHERE agent = ?", (name,))
        cursor.execute("DELETE FROM agents WHERE name = ?", (name,))
        cursor.execute(
            "INSERT INTO events (agent, action, cost_micro, metadata) VALUES (?, ?, ?, ?)",
            (name, "AGENT_DELETED", 0, "Deleted by UI operator"),
        )
        conn.commit()
    return {"deleted": True, "name": name}


def _list_snapshot_tags() -> list[str]:
    with get_db_connection() as conn:
        if not _table_exists(conn, "information_schema", "tables"):
            return []
        rows = conn.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = ?
              AND table_name LIKE '%__%'
            ORDER BY table_name
            """,
            (_SNAPSHOT_SCHEMA,),
        ).fetchall()
    tags: set[str] = set()
    for row in rows:
        table_name = str(row["table_name"])
        if "__" not in table_name:
            continue
        tags.add(table_name.rsplit("__", 1)[-1])
    return sorted(tags)


def _create_snapshot_tag(tag: str | None) -> dict:
    final_tag = _safe_tag(tag) if tag else _default_tag("snap")
    with get_db_connection() as conn:
        conn.execute("BEGIN")
        _create_snapshot(conn, final_tag)
        conn.commit()
    return {"ok": True, "tag": final_tag}


def _apply_migrations(snapshot_first: bool, tag: str | None) -> dict:
    snap_tag = _safe_tag(tag) if tag else _default_tag("pre_migrate")
    if snapshot_first:
        with get_db_connection() as conn:
            conn.execute("BEGIN")
            _create_snapshot(conn, snap_tag)
            conn.commit()
    init_db()
    return {"ok": True, "snapshot_first": snapshot_first, "snapshot_tag": snap_tag if snapshot_first else None}


def _rollback_snapshot(tag: str) -> dict:
    final_tag = _safe_tag(tag)
    with get_db_connection() as conn:
        for table in _MIGRATION_TABLES:
            snap_table = _snapshot_table_name(table, final_tag)
            if not _table_exists(conn, _SNAPSHOT_SCHEMA, snap_table):
                raise HTTPException(status_code=404, detail=f"Snapshot not found: {_SNAPSHOT_SCHEMA}.{snap_table}")

        conn.execute("BEGIN")
        table_list = ", ".join(f'public."{t}"' for t in _MIGRATION_TABLES)
        conn.execute(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE")
        for table in reversed(_MIGRATION_TABLES):
            snap_table = _snapshot_table_name(table, final_tag)
            conn.execute(
                f'INSERT INTO public."{table}" SELECT * FROM {_SNAPSHOT_SCHEMA}."{snap_table}"'
            )
        _reset_sequences(conn)
        conn.commit()
    return {"ok": True, "tag": final_tag}


def _db_test(dsn: str) -> dict:
    raw = (dsn or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="dsn is required")
    try:
        import psycopg
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"psycopg is not installed: {exc}")

    try:
        conn = psycopg.connect(raw, connect_timeout=5)
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user, now()")
            row = cur.fetchone()
        conn.close()
        return {
            "ok": True,
            "dsn": _mask_dsn(raw),
            "database": str(row[0]),
            "user": str(row[1]),
            "time": str(row[2]),
        }
    except Exception as exc:
        return {"ok": False, "dsn": _mask_dsn(raw), "error": str(exc)}


@router.get("/admin/activity")
async def activity_feed_endpoint(limit: int = Query(default=40, ge=10, le=200)):
    """Return recent backend activity for the local dashboard UI."""
    return await run_in_threadpool(activity_snapshot, limit)


@router.get("/admin/dashboard/data")
async def dashboard_data_endpoint(
    limit: int = Query(default=120, ge=20, le=500),
    include_replay: bool = Query(default=False),
):
    """Backend-oriented payload for the dashboard UI."""
    return await run_in_threadpool(
        lambda: dashboard_payload(limit=limit, include_deep_replay=include_replay)
    )


@router.get("/admin/alerts")
async def alerts_endpoint():
    alerts = await run_in_threadpool(collect_active_alerts)
    return {"alerts": alerts, "summary": summarize_alerts(alerts)}


@router.post("/admin/reload_config")
async def reload_config_endpoint():
    try:
        config_loader.load_config()
        return {"status": "ok", "message": "Configuration reloaded"}
    except Exception as e:
        logger.error("Config reload failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/replay")
async def replay_audit_endpoint():
    payload = await run_in_threadpool(
        lambda: dashboard_payload(limit=40, include_deep_replay=True)
    )
    return payload["replay"]


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_endpoint():
    """Serve lightweight local-only metrics dashboard."""
    dashboard_path = Path(__file__).parent.parent / "frontend" / "dashboard.html"
    if dashboard_path.exists():
        return HTMLResponse(content=dashboard_path.read_text(), status_code=200)
    raise HTTPException(status_code=404, detail="Dashboard not available")


@router.get("/health")
async def health():
    return liveness_report()


@router.get("/ready")
async def ready():
    ready_ok, report = await run_in_threadpool(readiness_report)
    status_code = 200 if ready_ok else 503
    return JSONResponse(content=report, status_code=status_code)


@router.get("/metrics")
async def metrics_endpoint():
    return await run_in_threadpool(get_metrics)


@router.post("/admin/control/pause_all")
async def pause_all_agents(body: OperatorControlRequest, request: Request):
    _require_control_key(request)
    return await run_in_threadpool(
        lambda: _bulk_set_agent_state(
            target_state="PAUSED",
            reason=body.reason,
            event_type="agent.pause_all",
        )
    )


@router.post("/admin/control/sandbox_all")
async def sandbox_all_agents(body: OperatorControlRequest, request: Request):
    _require_control_key(request)
    return await run_in_threadpool(
        lambda: _bulk_set_agent_state(
            target_state="QUARANTINED",
            reason=body.reason,
            event_type="agent.sandbox_all",
        )
    )


@router.post("/admin/control/kill_all")
async def kill_all_agents(body: OperatorControlRequest, request: Request):
    _require_control_key(request)
    return await run_in_threadpool(lambda: _kill_all_agents(reason=body.reason))


@router.post("/admin/onboarding/quickstart")
async def onboarding_quickstart(body: QuickstartCreateRequest, request: Request):
    """UI-first onboarding: create tenant/project/agent and return one-copy env pack."""
    _require_control_key(request)
    return await run_in_threadpool(lambda: _create_quickstart_bundle(body, request))


@router.get("/admin/console", response_class=HTMLResponse)
async def command_center_endpoint():
    """Serve UI command center that replaces CLI-heavy operations."""
    page_path = Path(__file__).parent.parent / "frontend" / "command_center.html"
    if page_path.exists():
        return HTMLResponse(content=page_path.read_text(), status_code=200)
    raise HTTPException(status_code=404, detail="Command center UI not available")


@router.get("/admin/ui/system/info")
async def ui_system_info(request: Request):
    _require_control_key(request)

    def _run():
        return {
            "version": __version__,
            "db_dsn": get_db_path(),
            "db_integrity_ok": check_db_integrity(),
            "unsupported_in_web": [
                "daemon.start",
                "daemon.stop",
                "daemon.status",
                "run (spawn subprocess as agent)",
            ],
        }

    return await run_in_threadpool(_run)


@router.post("/admin/ui/db/test")
async def ui_db_test_connection(body: DbTestRequest, request: Request):
    _require_control_key(request)
    return await run_in_threadpool(lambda: _db_test(body.dsn))


@router.post("/admin/ui/system/set-db-dsn")
async def ui_set_runtime_db_dsn(body: DbSetRequest, request: Request):
    _require_control_key(request)

    def _run():
        result = _db_test(body.dsn) if body.verify_connection else {"ok": True}
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error"), "dsn": result.get("dsn")}
        os.environ["AEX_PG_DSN"] = body.dsn.strip()
        return {"ok": True, "dsn": _mask_dsn(body.dsn), "message": "AEX_PG_DSN updated for current process"}

    return await run_in_threadpool(_run)


@router.get("/admin/ui/tenants")
async def ui_list_tenants(request: Request):
    _require_control_key(request)

    def _run():
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT tenant_id, name, slug, status, created_at FROM tenants ORDER BY created_at ASC"
            ).fetchall()
        return {"items": [dict(r) for r in rows]}

    return await run_in_threadpool(_run)


@router.post("/admin/ui/tenants")
async def ui_upsert_tenant(body: TenantUpsertRequest, request: Request):
    _require_control_key(request)

    def _run():
        tenant = _sanitize_slug(body.tenant_id, "default")
        tenant_name = (body.name or "").strip() or f"Tenant {tenant}"
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO tenants (tenant_id, name, slug, status)
                VALUES (?, ?, ?, 'ACTIVE')
                ON CONFLICT(tenant_id) DO UPDATE SET name = excluded.name, slug = excluded.slug
                """,
                (tenant, tenant_name, tenant),
            )
            conn.commit()
        return {"ok": True, "tenant_id": tenant, "name": tenant_name}

    return await run_in_threadpool(_run)


@router.get("/admin/ui/projects")
async def ui_list_projects(request: Request, tenant_id: str = Query(default="")):
    _require_control_key(request)

    def _run():
        with get_db_connection() as conn:
            if tenant_id.strip():
                rows = conn.execute(
                    """
                    SELECT project_id, tenant_id, name, slug, status, created_at
                    FROM projects
                    WHERE tenant_id = ?
                    ORDER BY created_at ASC
                    """,
                    (tenant_id.strip(),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT project_id, tenant_id, name, slug, status, created_at FROM projects ORDER BY created_at ASC"
                ).fetchall()
        return {"items": [dict(r) for r in rows]}

    return await run_in_threadpool(_run)


@router.post("/admin/ui/projects")
async def ui_upsert_project(body: ProjectUpsertRequest, request: Request):
    _require_control_key(request)

    def _run():
        tenant = _sanitize_slug(body.tenant_id, "default")
        project = _sanitize_slug(body.project_id, "default")
        project_name = (body.name or "").strip() or f"Project {project}"
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO tenants (tenant_id, name, slug, status)
                VALUES (?, ?, ?, 'ACTIVE')
                ON CONFLICT(tenant_id) DO NOTHING
                """,
                (tenant, f"Tenant {tenant}", tenant),
            )
            conn.execute(
                """
                INSERT INTO projects (project_id, tenant_id, name, slug, status)
                VALUES (?, ?, ?, ?, 'ACTIVE')
                ON CONFLICT(project_id) DO UPDATE SET
                  tenant_id = excluded.tenant_id,
                  name = excluded.name,
                  slug = excluded.slug
                """,
                (project, tenant, project_name, project),
            )
            conn.commit()
        return {"ok": True, "tenant_id": tenant, "project_id": project, "name": project_name}

    return await run_in_threadpool(_run)


@router.get("/admin/ui/agents")
async def ui_list_agents(request: Request):
    _require_control_key(request)

    def _run():
        with get_db_connection() as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY name ASC").fetchall()
        return {"items": [_serialize_agent_row(dict(r)) for r in rows]}

    return await run_in_threadpool(_run)


@router.post("/admin/ui/agents")
async def ui_create_agent(body: AgentCreateRequest, request: Request):
    _require_control_key(request)
    return await run_in_threadpool(lambda: _create_agent(body))


@router.get("/admin/ui/agents/{agent_name}")
async def ui_get_agent(agent_name: str, request: Request, include_token: bool = Query(default=False)):
    _require_control_key(request)

    def _run():
        with get_db_connection() as conn:
            row = conn.execute("SELECT * FROM agents WHERE name = ?", (agent_name,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")
        payload = dict(row)
        if not include_token and payload.get("api_token"):
            payload["api_token_masked"] = f"{payload['api_token'][:6]}...{payload['api_token'][-4:]}"
            payload.pop("api_token", None)
        payload["allowed_models"] = _parse_json_list(payload.get("allowed_models"))
        payload["allowed_tool_names"] = _parse_json_list(payload.get("allowed_tool_names"))
        return payload

    return await run_in_threadpool(_run)


@router.delete("/admin/ui/agents/{agent_name}")
async def ui_delete_agent(agent_name: str, request: Request):
    _require_control_key(request)
    return await run_in_threadpool(lambda: _delete_agent(agent_name))


@router.post("/admin/ui/agents/{agent_name}/rotate-token")
async def ui_rotate_agent_token(agent_name: str, body: AgentRotateTokenRequest, request: Request):
    _require_control_key(request)
    return await run_in_threadpool(lambda: _rotate_agent_token(agent_name, body.token_ttl_hours))


@router.post("/admin/ui/agents/{agent_name}/state")
async def ui_transition_agent_state(agent_name: str, body: AgentStateRequest, request: Request):
    _require_control_key(request)

    def _run():
        transition = transition_agent_state(agent_name, body.to_state, body.reason)
        return {
            "agent": transition.agent,
            "from_state": transition.from_state,
            "to_state": transition.to_state,
            "reason": transition.reason,
        }

    return await run_in_threadpool(_run)


@router.get("/admin/ui/policies")
async def ui_list_policies(request: Request):
    _require_control_key(request)
    return await run_in_threadpool(lambda: {"items": [p.to_dict() for p in list_policies()]})


@router.get("/admin/ui/policies/{policy_id}")
async def ui_get_policy(policy_id: str, request: Request):
    _require_control_key(request)

    def _run():
        try:
            policy = load_policy(policy_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Policy '{policy_id}' not found")
        return policy.to_dict()

    return await run_in_threadpool(_run)


@router.post("/admin/ui/policies")
async def ui_upsert_policy(body: PolicyUpsertRequest, request: Request):
    _require_control_key(request)

    def _run():
        policy = create_policy(
            body.policy_id,
            {
                "budget_usd": body.budget_usd,
                "allow_tools": body.allow_tools or [],
                "deny_tools": body.deny_tools or [],
                "max_steps": body.max_steps,
                "dangerous_ops": body.dangerous_ops,
                "require_approval_for_destructive_ops": body.require_approval_for_destructive_ops,
            },
        )
        return policy.to_dict()

    return await run_in_threadpool(_run)


@router.delete("/admin/ui/policies/{policy_id}")
async def ui_delete_policy(policy_id: str, request: Request):
    _require_control_key(request)

    def _run():
        deleted = delete_policy(policy_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Policy '{policy_id}' not found")
        return {"deleted": True, "policy_id": policy_id}

    return await run_in_threadpool(_run)


@router.post("/admin/ui/audit")
async def ui_run_audit(request: Request):
    _require_control_key(request)

    def _run():
        with get_db_connection() as conn:
            results = run_all_checks(conn, include_event_hash_chain=True)
        serialized = [{"name": r.name, "passed": bool(r.passed), "detail": r.detail} for r in results]
        return {"ok": all(item["passed"] for item in serialized), "results": serialized}

    return await run_in_threadpool(_run)


@router.get("/admin/ui/replay")
async def ui_run_replay(request: Request):
    _require_control_key(request)

    def _run():
        chain = verify_hash_chain()
        replay = replay_ledger_balances()
        return {
            "hash_chain_ok": chain.ok,
            "hash_chain_detail": chain.detail,
            "balance_replay_ok": replay.ok,
            "balance_replay_detail": replay.detail,
        }

    return await run_in_threadpool(_run)


@router.get("/admin/ui/migrate/tags")
async def ui_list_migration_tags(request: Request):
    _require_control_key(request)
    return await run_in_threadpool(lambda: {"tags": _list_snapshot_tags()})


@router.post("/admin/ui/migrate/snapshot")
async def ui_create_migration_snapshot(body: MigrateSnapshotRequest, request: Request):
    _require_control_key(request)
    return await run_in_threadpool(lambda: _create_snapshot_tag(body.tag))


@router.post("/admin/ui/migrate/apply")
async def ui_apply_migration(body: MigrateApplyRequest, request: Request):
    _require_control_key(request)
    return await run_in_threadpool(lambda: _apply_migrations(body.snapshot_first, body.tag))


@router.post("/admin/ui/migrate/rollback")
async def ui_rollback_migration(body: MigrateRollbackRequest, request: Request):
    _require_control_key(request)
    return await run_in_threadpool(lambda: _rollback_snapshot(body.tag))


@router.get("/admin/ui/plugins")
async def ui_list_plugins(request: Request):
    _require_control_key(request)
    return await run_in_threadpool(lambda: {"items": list_plugins()})


@router.post("/admin/ui/plugins/install")
async def ui_install_plugin(body: PluginInstallRequest, request: Request):
    _require_control_key(request)
    return await run_in_threadpool(lambda: install_plugin(body.manifest_path, body.package_path))


@router.post("/admin/ui/plugins/{plugin_name}/enable")
async def ui_enable_plugin(plugin_name: str, request: Request):
    _require_control_key(request)

    def _run():
        set_plugin_enabled(plugin_name, True)
        return {"ok": True, "name": plugin_name, "enabled": True}

    return await run_in_threadpool(_run)


@router.post("/admin/ui/plugins/{plugin_name}/disable")
async def ui_disable_plugin(plugin_name: str, request: Request):
    _require_control_key(request)

    def _run():
        set_plugin_enabled(plugin_name, False)
        return {"ok": True, "name": plugin_name, "enabled": False}

    return await run_in_threadpool(_run)
