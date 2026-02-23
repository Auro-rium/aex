"""Concurrency-safe reservation and settlement ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from enum import StrEnum
import json

from fastapi import HTTPException

from ..db import get_db_connection
from ..db.schema import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from ..observability import dispatch_budget_webhooks
from ..utils.logging_config import StructuredLogger
from .events import append_hash_event, append_compat_event

logger = StructuredLogger(__name__)


class ExecutionState(StrEnum):
    RESERVING = "RESERVING"
    RESERVED = "RESERVED"
    DISPATCHED = "DISPATCHED"
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
    COMMITTED = "COMMITTED"
    RELEASED = "RELEASED"
    DENIED = "DENIED"
    FAILED = "FAILED"


_TERMINAL_STATES = {
    ExecutionState.COMMITTED,
    ExecutionState.RELEASED,
    ExecutionState.DENIED,
    ExecutionState.FAILED,
}


@dataclass
class ReservationDecision:
    execution_id: str
    reserved: bool
    estimated_micro: int
    reused: bool = False
    state: str | None = None
    status_code: int | None = None
    response_body: dict | None = None
    error_body: dict | None = None


@dataclass
class CachedExecutionResult:
    state: str
    status_code: int | None
    response_body: dict | None
    error_body: dict | None


def _json_or_none(text: str | None):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _scope(tenant_id: str | None, project_id: str | None) -> tuple[str, str]:
    tenant = (tenant_id or DEFAULT_TENANT_ID).strip() or DEFAULT_TENANT_ID
    project = (project_id or DEFAULT_PROJECT_ID).strip() or DEFAULT_PROJECT_ID
    return tenant, project


def _sync_agent_budget_scope(conn, *, agent: str, tenant_id: str, project_id: str) -> None:
    """Materialize agent-level budget counters into normalized budgets/quota tables."""
    row = conn.execute(
        """
        SELECT budget_micro, spent_micro, reserved_micro, rpm_limit, max_tokens_per_minute
        FROM agents
        WHERE name = ?
        """,
        (agent,),
    ).fetchone()
    if not row:
        return

    budget_key = f"agent:{tenant_id}:{project_id}:{agent}"
    conn.execute(
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
            tenant_id,
            project_id,
            agent,
            int(row["budget_micro"] or 0),
            int(row["spent_micro"] or 0),
            int(row["reserved_micro"] or 0),
        ),
    )

    quota_key = f"agent:{tenant_id}:{project_id}:{agent}"
    conn.execute(
        """
        INSERT INTO quota_limits (scope_key, tenant_id, project_id, agent, rpm_limit, tpm_limit)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope_key) DO UPDATE SET
            rpm_limit = excluded.rpm_limit,
            tpm_limit = excluded.tpm_limit,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            quota_key,
            tenant_id,
            project_id,
            agent,
            int(row["rpm_limit"] or 0),
            row["max_tokens_per_minute"],
        ),
    )


def get_execution_cache(execution_id: str) -> CachedExecutionResult | None:
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT state, status_code, response_body, error_body FROM executions WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
        if not row:
            return None
        return CachedExecutionResult(
            state=row["state"],
            status_code=row["status_code"],
            response_body=_json_or_none(row["response_body"]),
            error_body=_json_or_none(row["error_body"]),
        )


def reserve_budget_v2(
    *,
    agent: str,
    tenant_id: str | None = None,
    project_id: str | None = None,
    execution_id: str,
    endpoint: str,
    request_hash: str,
    estimated_cost_micro: int,
    policy_hash: str | None = None,
    route_hash: str | None = None,
    reservation_ttl_seconds: int = 180,
) -> ReservationDecision:
    """Reserve budget in an idempotent transaction.

    Exactly one of these outcomes is returned:
    - reservation created (reserved=True)
    - prior terminal result reused (reused=True)
    - denied (raises HTTPException)
    """
    now = _utc_now_iso()
    expiry = (datetime.now(UTC) + timedelta(seconds=reservation_ttl_seconds)).replace(microsecond=0).isoformat()

    tenant_scope, project_scope = _scope(tenant_id, project_id)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")

            agent_row = cursor.execute(
                """
                SELECT budget_micro, spent_micro, reserved_micro, lifecycle_state,
                       COALESCE(NULLIF(tenant_id, ''), ?) AS tenant_id,
                       COALESCE(NULLIF(project_id, ''), ?) AS project_id
                FROM agents
                WHERE name = ?
                """,
                (DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID, agent),
            ).fetchone()
            if not agent_row:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Agent not found")

            tenant_scope, project_scope = _scope(agent_row["tenant_id"], agent_row["project_id"])
            if tenant_id and tenant_scope != tenant_id:
                conn.rollback()
                raise HTTPException(status_code=403, detail="Agent is not mapped to requested tenant")
            if project_id and project_scope != project_id:
                conn.rollback()
                raise HTTPException(status_code=403, detail="Agent is not mapped to requested project")

            if (agent_row["lifecycle_state"] or "READY") != "READY":
                conn.rollback()
                raise HTTPException(status_code=423, detail=f"Agent state is {agent_row['lifecycle_state']}; execution blocked")

            existing = cursor.execute(
                "SELECT state, status_code, response_body, error_body, request_hash FROM executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            existing_reservation = cursor.execute(
                "SELECT state, estimated_micro FROM reservations WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()

            if existing and existing["request_hash"] and existing["request_hash"] != request_hash:
                conn.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency conflict: execution_id is already bound to a different request hash",
                )

            if existing and existing["state"] in _TERMINAL_STATES:
                conn.commit()
                return ReservationDecision(
                    execution_id=execution_id,
                    reserved=False,
                    estimated_micro=estimated_cost_micro,
                    reused=True,
                    state=existing["state"],
                    status_code=existing["status_code"],
                    response_body=_json_or_none(existing["response_body"]),
                    error_body=_json_or_none(existing["error_body"]),
                )

            if existing_reservation and existing_reservation["state"] == "RESERVED":
                conn.commit()
                return ReservationDecision(
                    execution_id=execution_id,
                    reserved=False,
                    estimated_micro=int(existing_reservation["estimated_micro"] or estimated_cost_micro),
                    reused=True,
                    state=ExecutionState.RESERVED,
                )

            if not existing:
                cursor.execute(
                    """
                    INSERT INTO executions (
                        execution_id, tenant_id, project_id, agent, endpoint,
                        request_hash, policy_hash, route_hash, state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        execution_id,
                        tenant_scope,
                        project_scope,
                        agent,
                        endpoint,
                        request_hash,
                        policy_hash,
                        route_hash,
                        ExecutionState.RESERVING,
                        now,
                        now,
                    ),
                )
            else:
                cursor.execute(
                    """
                    UPDATE executions
                    SET tenant_id = ?, project_id = ?, endpoint = ?, request_hash = ?,
                        policy_hash = ?, route_hash = ?, updated_at = ?
                    WHERE execution_id = ?
                    """,
                    (
                        tenant_scope,
                        project_scope,
                        endpoint,
                        request_hash,
                        policy_hash,
                        route_hash,
                        now,
                        execution_id,
                    ),
                )

            remaining = int(agent_row["budget_micro"] or 0) - int(agent_row["spent_micro"] or 0) - int(agent_row["reserved_micro"] or 0)
            if estimated_cost_micro > remaining:
                error_payload = {
                    "detail": "Insufficient budget",
                    "estimated_micro": estimated_cost_micro,
                    "remaining_micro": remaining,
                }
                cursor.execute(
                    """
                    UPDATE executions
                    SET state = ?, status_code = 402, error_body = ?, updated_at = ?, terminal_at = ?
                    WHERE execution_id = ?
                    """,
                    (ExecutionState.DENIED, json.dumps(error_payload, ensure_ascii=True), now, now, execution_id),
                )
                append_hash_event(
                    conn,
                    execution_id=execution_id,
                    agent=agent,
                    tenant_id=tenant_scope,
                    project_id=project_scope,
                    event_type="budget.deny",
                    payload=error_payload,
                )
                append_compat_event(
                    conn,
                    agent=agent,
                    tenant_id=tenant_scope,
                    project_id=project_scope,
                    action="budget.deny",
                    cost_micro=0,
                    metadata=error_payload,
                )
                _sync_agent_budget_scope(conn, agent=agent, tenant_id=tenant_scope, project_id=project_scope)
                conn.commit()
                try:
                    dispatch_budget_webhooks(
                        tenant_id=tenant_scope,
                        event_type="execution.denied",
                        execution_id=execution_id,
                        payload={"agent": agent, "endpoint": endpoint, **error_payload},
                    )
                except Exception as exc:
                    logger.warning("Webhook dispatch failed for deny", execution_id=execution_id, error=str(exc))
                raise HTTPException(status_code=402, detail="Insufficient budget")

            cursor.execute(
                """
                INSERT INTO reservations (
                    execution_id, tenant_id, project_id, agent, estimated_micro,
                    actual_micro, state, reserved_at, expiry_at
                ) VALUES (?, ?, ?, ?, ?, 0, 'RESERVED', ?, ?)
                ON CONFLICT(execution_id) DO NOTHING
                """,
                (execution_id, tenant_scope, project_scope, agent, estimated_cost_micro, now, expiry),
            )

            if cursor.rowcount == 0:
                conn.commit()
                return ReservationDecision(
                    execution_id=execution_id,
                    reserved=False,
                    estimated_micro=estimated_cost_micro,
                    reused=True,
                    state=ExecutionState.RESERVED,
                )

            cursor.execute(
                "UPDATE agents SET reserved_micro = reserved_micro + ? WHERE name = ?",
                (estimated_cost_micro, agent),
            )
            cursor.execute(
                "UPDATE executions SET state = ?, updated_at = ? WHERE execution_id = ?",
                (ExecutionState.RESERVED, now, execution_id),
            )

            append_hash_event(
                conn,
                execution_id=execution_id,
                agent=agent,
                tenant_id=tenant_scope,
                project_id=project_scope,
                event_type="budget.reserve",
                payload={"estimated_micro": estimated_cost_micro, "expiry_at": expiry},
            )
            append_compat_event(
                conn,
                agent=agent,
                tenant_id=tenant_scope,
                project_id=project_scope,
                action="budget.reserve",
                cost_micro=0,
                metadata={"estimated_micro": estimated_cost_micro, "execution_id": execution_id},
            )
            _sync_agent_budget_scope(conn, agent=agent, tenant_id=tenant_scope, project_id=project_scope)
            conn.commit()

            try:
                dispatch_budget_webhooks(
                    tenant_id=tenant_scope,
                    event_type="budget.reserved",
                    execution_id=execution_id,
                    payload={
                        "agent": agent,
                        "execution_id": execution_id,
                        "estimated_micro": estimated_cost_micro,
                        "expiry_at": expiry,
                    },
                )
            except Exception as exc:
                logger.warning("Webhook dispatch failed after reserve", execution_id=execution_id, error=str(exc))

            return ReservationDecision(execution_id=execution_id, reserved=True, estimated_micro=estimated_cost_micro)

        except HTTPException:
            raise
        except Exception as exc:
            conn.rollback()
            logger.error("Failed budget reservation", agent=agent, execution_id=execution_id, error=str(exc))
            raise HTTPException(status_code=500, detail="Internal accounting error")


def mark_execution_dispatched(execution_id: str) -> None:
    with get_db_connection() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT state, agent,
                       COALESCE(NULLIF(tenant_id, ''), ?) AS tenant_id,
                       COALESCE(NULLIF(project_id, ''), ?) AS project_id
                FROM executions
                WHERE execution_id = ?
                """,
                (DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID, execution_id),
            ).fetchone()
            if not row:
                conn.rollback()
                return
            if row["state"] in _TERMINAL_STATES:
                conn.commit()
                return

            now = _utc_now_iso()
            conn.execute(
                "UPDATE executions SET state = ?, updated_at = ? WHERE execution_id = ?",
                (ExecutionState.DISPATCHED, now, execution_id),
            )
            append_hash_event(
                conn,
                execution_id=execution_id,
                agent=row["agent"],
                tenant_id=row["tenant_id"],
                project_id=row["project_id"],
                event_type="execution.dispatched",
                payload={"state": ExecutionState.DISPATCHED},
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.warning("Unable to mark dispatched", execution_id=execution_id, error=str(exc))


def commit_execution_usage(
    *,
    agent: str,
    execution_id: str,
    estimated_cost_micro: int,
    actual_cost_micro: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    model_name: str | None = None,
    response_body: dict | None = None,
    status_code: int = 200,
) -> None:
    """Commit usage exactly once using reservation state CAS."""
    now = _utc_now_iso()
    tenant_scope = DEFAULT_TENANT_ID
    project_scope = DEFAULT_PROJECT_ID

    with get_db_connection() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")

            execution_row = conn.execute(
                """
                SELECT state,
                       COALESCE(NULLIF(tenant_id, ''), ?) AS tenant_id,
                       COALESCE(NULLIF(project_id, ''), ?) AS project_id
                FROM executions
                WHERE execution_id = ?
                """,
                (DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID, execution_id),
            ).fetchone()
            if not execution_row:
                conn.rollback()
                raise RuntimeError(f"Execution {execution_id} missing")

            tenant_scope, project_scope = _scope(execution_row["tenant_id"], execution_row["project_id"])

            if execution_row["state"] == ExecutionState.COMMITTED:
                conn.commit()
                return

            cas = conn.execute(
                """
                UPDATE reservations
                SET state = 'COMMITTED', actual_micro = ?, settled_at = ?
                WHERE execution_id = ? AND state = 'RESERVED'
                """,
                (actual_cost_micro, now, execution_id),
            )

            if cas.rowcount == 0:
                existing = conn.execute(
                    "SELECT state FROM reservations WHERE execution_id = ?",
                    (execution_id,),
                ).fetchone()
                if existing and existing["state"] == "COMMITTED":
                    conn.commit()
                    return
                conn.rollback()
                raise RuntimeError("Reservation CAS failed; refusing duplicate settlement")

            conn.execute(
                """
                UPDATE agents
                SET reserved_micro = GREATEST(0::bigint, reserved_micro - (?::bigint)),
                    spent_micro = spent_micro + ?,
                    tokens_used_prompt = tokens_used_prompt + ?,
                    tokens_used_completion = tokens_used_completion + ?,
                    last_activity = CURRENT_TIMESTAMP
                WHERE name = ?
                """,
                (estimated_cost_micro, actual_cost_micro, prompt_tokens, completion_tokens, agent),
            )

            total_tokens = prompt_tokens + completion_tokens
            if total_tokens > 0:
                conn.execute(
                    """
                    UPDATE rate_windows
                    SET tokens_count = tokens_count + ?,
                        tenant_id = COALESCE(NULLIF(tenant_id, ''), ?),
                        project_id = COALESCE(NULLIF(project_id, ''), ?)
                    WHERE agent = ?
                    """,
                    (total_tokens, tenant_scope, project_scope, agent),
                )

            response_text = json.dumps(response_body, ensure_ascii=True) if response_body is not None else None
            conn.execute(
                """
                UPDATE executions
                SET state = ?, status_code = ?, response_body = ?, error_body = NULL,
                    updated_at = ?, terminal_at = ?
                WHERE execution_id = ?
                """,
                (ExecutionState.COMMITTED, status_code, response_text, now, now, execution_id),
            )

            payload = {
                "cost_micro": actual_cost_micro,
                "estimated_micro": estimated_cost_micro,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "model": model_name,
            }
            append_hash_event(
                conn,
                execution_id=execution_id,
                agent=agent,
                tenant_id=tenant_scope,
                project_id=project_scope,
                event_type="usage.commit",
                payload=payload,
            )
            append_compat_event(
                conn,
                agent=agent,
                tenant_id=tenant_scope,
                project_id=project_scope,
                action="usage.commit",
                cost_micro=actual_cost_micro,
                metadata=model_name,
            )
            _sync_agent_budget_scope(conn, agent=agent, tenant_id=tenant_scope, project_id=project_scope)
            conn.commit()

        except Exception as exc:
            conn.rollback()
            logger.critical(
                "Accounting integrity failure during commit",
                agent=agent,
                execution_id=execution_id,
                error=str(exc),
            )
            raise

    try:
        dispatch_budget_webhooks(
            tenant_id=tenant_scope,
            event_type="budget.committed",
            execution_id=execution_id,
            payload={
                "agent": agent,
                "estimated_micro": estimated_cost_micro,
                "actual_micro": actual_cost_micro,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "model": model_name,
            },
        )
    except Exception as exc:
        logger.warning("Webhook dispatch failed after commit", execution_id=execution_id, error=str(exc))


def release_execution_reservation(
    *,
    agent: str,
    execution_id: str,
    estimated_cost_micro: int,
    reason: str,
    status_code: int | None = None,
) -> None:
    """Release reservation for failed dispatch paths (idempotent)."""
    now = _utc_now_iso()
    status = status_code or 502
    error_payload = {"detail": reason}
    tenant_scope = DEFAULT_TENANT_ID
    project_scope = DEFAULT_PROJECT_ID

    with get_db_connection() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")

            execution_row = conn.execute(
                """
                SELECT state,
                       COALESCE(NULLIF(tenant_id, ''), ?) AS tenant_id,
                       COALESCE(NULLIF(project_id, ''), ?) AS project_id
                FROM executions
                WHERE execution_id = ?
                """,
                (DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID, execution_id),
            ).fetchone()
            if not execution_row:
                conn.rollback()
                return

            tenant_scope, project_scope = _scope(execution_row["tenant_id"], execution_row["project_id"])

            if execution_row["state"] in (ExecutionState.COMMITTED, ExecutionState.RELEASED):
                conn.commit()
                return

            cas = conn.execute(
                """
                UPDATE reservations
                SET state = 'RELEASED', settled_at = ?
                WHERE execution_id = ? AND state = 'RESERVED'
                """,
                (now, execution_id),
            )

            if cas.rowcount > 0:
                conn.execute(
                    "UPDATE agents SET reserved_micro = GREATEST(0::bigint, reserved_micro - (?::bigint)) WHERE name = ?",
                    (estimated_cost_micro, agent),
                )

            conn.execute(
                """
                UPDATE executions
                SET state = ?, status_code = ?, error_body = ?, updated_at = ?, terminal_at = ?
                WHERE execution_id = ?
                """,
                (ExecutionState.RELEASED, status, json.dumps(error_payload, ensure_ascii=True), now, now, execution_id),
            )

            append_hash_event(
                conn,
                execution_id=execution_id,
                agent=agent,
                tenant_id=tenant_scope,
                project_id=project_scope,
                event_type="reservation.release",
                payload={"reason": reason, "estimated_micro": estimated_cost_micro},
            )
            append_compat_event(
                conn,
                agent=agent,
                tenant_id=tenant_scope,
                project_id=project_scope,
                action="reservation.release",
                metadata={"reason": reason, "execution_id": execution_id},
            )
            _sync_agent_budget_scope(conn, agent=agent, tenant_id=tenant_scope, project_id=project_scope)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.error("Failed to release reservation", agent=agent, execution_id=execution_id, error=str(exc))
            return

    try:
        dispatch_budget_webhooks(
            tenant_id=tenant_scope,
            event_type="budget.released",
            execution_id=execution_id,
            payload={
                "agent": agent,
                "reason": reason,
                "estimated_micro": estimated_cost_micro,
                "status_code": status,
            },
        )
    except Exception as exc:
        logger.warning("Webhook dispatch failed after release", execution_id=execution_id, error=str(exc))


def mark_execution_failed(execution_id: str, *, reason: str, status_code: int = 500) -> None:
    """Transition execution to FAILED when no reservation exists to release."""
    now = _utc_now_iso()
    tenant_scope = DEFAULT_TENANT_ID
    project_scope = DEFAULT_PROJECT_ID

    with get_db_connection() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT agent, state,
                       COALESCE(NULLIF(tenant_id, ''), ?) AS tenant_id,
                       COALESCE(NULLIF(project_id, ''), ?) AS project_id
                FROM executions
                WHERE execution_id = ?
                """,
                (DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID, execution_id),
            ).fetchone()
            if not row or row["state"] in _TERMINAL_STATES:
                conn.commit()
                return

            tenant_scope, project_scope = _scope(row["tenant_id"], row["project_id"])

            payload = {"detail": reason}
            conn.execute(
                """
                UPDATE executions
                SET state = ?, status_code = ?, error_body = ?, updated_at = ?, terminal_at = ?
                WHERE execution_id = ?
                """,
                (ExecutionState.FAILED, status_code, json.dumps(payload, ensure_ascii=True), now, now, execution_id),
            )
            append_hash_event(
                conn,
                execution_id=execution_id,
                agent=row["agent"],
                tenant_id=tenant_scope,
                project_id=project_scope,
                event_type="execution.failed",
                payload={"reason": reason, "status_code": status_code},
            )
            append_compat_event(
                conn,
                agent=row["agent"],
                tenant_id=tenant_scope,
                project_id=project_scope,
                action="execution.failed",
                metadata={"reason": reason, "status_code": status_code},
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.error("Failed to mark execution failed", execution_id=execution_id, error=str(exc))

    try:
        dispatch_budget_webhooks(
            tenant_id=tenant_scope,
            event_type="execution.failed",
            execution_id=execution_id,
            payload={"reason": reason, "status_code": status_code},
        )
    except Exception:
        # Non-critical path.
        pass
