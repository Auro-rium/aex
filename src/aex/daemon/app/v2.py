"""AEX v2.1 APIs: admission, settlement, webhooks, tenancy-aware admin surfaces."""

from __future__ import annotations

from typing import Any
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..auth import get_agent_from_token
from ..control import admit_request, resolve_scope
from ..db import get_db_connection
from ..ledger import commit_execution_usage, release_execution_reservation

router = APIRouter(prefix="/api/v2", tags=["v2.1"])


class AdmissionCheckRequest(BaseModel):
    execution_id: str = Field(..., min_length=8, max_length=256)
    endpoint: str = Field(default="/v1/chat/completions")
    model: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class SettlementCommitRequest(BaseModel):
    execution_id: str = Field(..., min_length=8, max_length=256)
    actual_micro_usd: int = Field(..., ge=0)
    usage: dict[str, Any] = Field(default_factory=dict)
    provider_receipt_id: str | None = None


class SettlementReleaseRequest(BaseModel):
    execution_id: str = Field(..., min_length=8, max_length=256)
    reason: str = Field(..., min_length=3, max_length=500)


class WebhookSubscriptionRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=1024)
    event_types: list[str] = Field(default_factory=lambda: ["budget.reserved", "budget.committed", "budget.released", "execution.denied"])
    secret: str | None = Field(default=None, max_length=256)
    enabled: bool = True


@router.post("/admission/check")
async def admission_check(
    body: AdmissionCheckRequest,
    request: Request,
    agent_info: dict = Depends(get_agent_from_token),
):
    headers = {k.lower(): v for k, v in request.headers.items()}
    scope = resolve_scope(headers, agent_info)

    payload = dict(body.payload)
    if body.model and "model" not in payload:
        payload["model"] = body.model

    try:
        result = await admit_request(
            endpoint=body.endpoint,
            body=payload,
            headers=headers,
            agent_info=agent_info,
            explicit_execution_id=body.execution_id,
        )
    except HTTPException as exc:
        if exc.status_code in {402, 403, 409, 423, 429}:
            detail = exc.detail
            reason_code = str(detail) if isinstance(detail, str) else str((detail or {}).get("detail", "DENIED"))
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "execution_id": body.execution_id,
                    "decision": "DENY",
                    "reservation_id": body.execution_id,
                    "reserved_micro_usd": 0,
                    "tenant_id": scope.tenant_id,
                    "project_id": scope.project_id,
                    "reason_code": reason_code,
                    "idempotent_replay": False,
                },
            )
        raise

    if result.cached_status_code is not None:
        decision = "ADMIT" if result.cached_status_code < 400 else "DENY"
        reason_code = None
        if decision == "DENY":
            detail = result.cached_error_body or {}
            reason_code = str(detail.get("detail", "DENIED")) if isinstance(detail, dict) else "DENIED"
        return {
            "execution_id": result.execution_id,
            "decision": decision,
            "reservation_id": result.execution_id,
            "reserved_micro_usd": 0,
            "tenant_id": scope.tenant_id,
            "project_id": scope.project_id,
            "reason_code": reason_code,
            "idempotent_replay": True,
        }

    expires_at = None
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT expiry_at FROM reservations WHERE execution_id = ?",
            (result.execution_id,),
        ).fetchone()
        if row:
            expires_at = row["expiry_at"]

    return {
        "execution_id": result.execution_id,
        "decision": "ADMIT",
        "reservation_id": result.execution_id,
        "reserved_micro_usd": result.estimated_cost_micro,
        "expires_at": expires_at,
        "tenant_id": scope.tenant_id,
        "project_id": scope.project_id,
        "idempotent_replay": bool(result.idempotent_replay),
    }


@router.post("/settlement/commit")
async def settlement_commit(
    body: SettlementCommitRequest,
    request: Request,
    agent_info: dict = Depends(get_agent_from_token),
):
    headers = {k.lower(): v for k, v in request.headers.items()}
    scope = resolve_scope(headers, agent_info)

    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT e.agent, e.state AS execution_state,
                   r.state AS reservation_state, r.estimated_micro
            FROM executions e
            LEFT JOIN reservations r ON r.execution_id = e.execution_id
            WHERE e.execution_id = ?
            """,
            (body.execution_id,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="execution_id not found")
    if row["agent"] != agent_info["name"]:
        raise HTTPException(status_code=403, detail="Execution does not belong to authenticated agent")

    idempotent = (row["reservation_state"] == "COMMITTED") or (row["execution_state"] == "COMMITTED")
    if not idempotent:
        usage = body.usage or {}
        prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        commit_execution_usage(
            agent=row["agent"],
            execution_id=body.execution_id,
            estimated_cost_micro=int(row["estimated_micro"] or 0),
            actual_cost_micro=int(body.actual_micro_usd),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model_name=str(usage.get("model") or "v2.settlement"),
            response_body={
                "usage": usage,
                "provider_receipt_id": body.provider_receipt_id,
                "settled_via": "api.v2",
            },
            status_code=200,
        )

    return {
        "status": "COMMITTED",
        "execution_id": body.execution_id,
        "tenant_id": scope.tenant_id,
        "project_id": scope.project_id,
        "idempotent_replay": idempotent,
    }


@router.post("/settlement/release")
async def settlement_release(
    body: SettlementReleaseRequest,
    request: Request,
    agent_info: dict = Depends(get_agent_from_token),
):
    headers = {k.lower(): v for k, v in request.headers.items()}
    scope = resolve_scope(headers, agent_info)

    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT e.agent, e.state AS execution_state,
                   r.state AS reservation_state, r.estimated_micro
            FROM executions e
            LEFT JOIN reservations r ON r.execution_id = e.execution_id
            WHERE e.execution_id = ?
            """,
            (body.execution_id,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="execution_id not found")
    if row["agent"] != agent_info["name"]:
        raise HTTPException(status_code=403, detail="Execution does not belong to authenticated agent")

    idempotent = (row["reservation_state"] == "RELEASED") or (row["execution_state"] == "RELEASED")
    if not idempotent:
        release_execution_reservation(
            agent=row["agent"],
            execution_id=body.execution_id,
            estimated_cost_micro=int(row["estimated_micro"] or 0),
            reason=body.reason,
            status_code=409,
        )

    return {
        "status": "RELEASED",
        "execution_id": body.execution_id,
        "tenant_id": scope.tenant_id,
        "project_id": scope.project_id,
        "idempotent_replay": idempotent,
    }


@router.post("/webhooks/subscriptions")
async def create_webhook_subscription(
    body: WebhookSubscriptionRequest,
    request: Request,
    agent_info: dict = Depends(get_agent_from_token),
):
    headers = {k.lower(): v for k, v in request.headers.items()}
    scope = resolve_scope(headers, agent_info)

    event_types = sorted({str(v).strip() for v in body.event_types if str(v).strip()})
    if not event_types:
        raise HTTPException(status_code=400, detail="event_types cannot be empty")

    with get_db_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO webhook_subscriptions (tenant_id, url, event_types_json, secret, enabled)
            VALUES (?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                scope.tenant_id,
                body.url,
                json.dumps(event_types, ensure_ascii=True),
                body.secret,
                1 if body.enabled else 0,
            ),
        )
        row = cursor.fetchone()
        conn.commit()

    return {
        "subscription_id": int(row["id"]),
        "tenant_id": scope.tenant_id,
        "url": body.url,
        "event_types": event_types,
        "enabled": body.enabled,
    }


@router.get("/webhooks/subscriptions")
async def list_webhook_subscriptions(
    request: Request,
    agent_info: dict = Depends(get_agent_from_token),
):
    headers = {k.lower(): v for k, v in request.headers.items()}
    scope = resolve_scope(headers, agent_info)

    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, url, event_types_json, enabled, created_at
            FROM webhook_subscriptions
            WHERE tenant_id = ?
            ORDER BY id DESC
            """,
            (scope.tenant_id,),
        ).fetchall()

    items = []
    for row in rows:
        try:
            event_types = json.loads(row["event_types_json"] or "[]")
        except Exception:
            event_types = []
        items.append(
            {
                "subscription_id": int(row["id"]),
                "url": row["url"],
                "event_types": event_types,
                "enabled": bool(row["enabled"]),
                "created_at": row["created_at"],
            }
        )

    return {"tenant_id": scope.tenant_id, "items": items}


@router.get("/webhooks/deliveries")
async def list_webhook_deliveries(
    request: Request,
    agent_info: dict = Depends(get_agent_from_token),
    limit: int = 50,
):
    headers = {k.lower(): v for k, v in request.headers.items()}
    scope = resolve_scope(headers, agent_info)

    safe_limit = max(1, min(limit, 200))
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, subscription_id, event_type, execution_id, status, attempts,
                   http_status, error, created_at, delivered_at
            FROM webhook_deliveries
            WHERE tenant_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (scope.tenant_id, safe_limit),
        ).fetchall()

    return {
        "tenant_id": scope.tenant_id,
        "items": [dict(r) for r in rows],
    }
