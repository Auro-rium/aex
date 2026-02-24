"""Admission pipeline: idempotency, policy, routing, reservation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from typing import Any

from fastapi import HTTPException

from ..db import get_db_connection
from ..ledger import get_execution_cache, reserve_budget_v2
from ..ledger.events import append_hash_event, append_compat_event
from ..policy.engine import PolicyDecision, evaluate_request
from ..utils.config_loader import config_loader
from ..utils.rate_limit import check_rate_limit
from .idempotency import execution_id_for_request
from .lifecycle import ensure_agent_can_execute
from .router import RoutePlan, resolve_route


@dataclass
class AdmissionResult:
    execution_id: str
    request_hash: str
    route_plan: RoutePlan
    policy: PolicyDecision
    request_body: dict[str, Any]
    estimated_cost_micro: int
    tenant_id: str
    project_id: str
    idempotent_replay: bool = False
    cached_status_code: int | None = None
    cached_response_body: dict | None = None
    cached_error_body: dict | None = None


_SUPPORTED_PATCH_KEYS = {
    "max_tokens",
    "temperature",
    "top_p",
    "stream",
    "tool_choice",
}


async def _wait_for_terminal_cache(execution_id: str):
    wait_ms = int(os.getenv("AEX_IDEMPOTENCY_WAIT_MS", "5000"))
    poll_ms = int(os.getenv("AEX_IDEMPOTENCY_POLL_MS", "50"))
    deadline = asyncio.get_running_loop().time() + (wait_ms / 1000.0)
    while asyncio.get_running_loop().time() < deadline:
        cached = get_execution_cache(execution_id)
        if cached and cached.state in {"COMMITTED", "DENIED", "RELEASED", "FAILED"}:
            return cached
        await asyncio.sleep(max(1, poll_ms) / 1000.0)
    return None


def _estimate_chat_cost(body: dict, model_config) -> int:
    messages = body.get("messages", [])
    input_text = "".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))
    est_input_tokens = len(input_text) // 4
    max_tokens = int(body.get("max_tokens") or model_config.limits.max_tokens)
    return (
        est_input_tokens * model_config.pricing.input_micro
        + max_tokens * model_config.pricing.output_micro
    )


def _estimate_responses_cost(body: dict, model_config) -> int:
    input_payload = body.get("input", "")
    if isinstance(input_payload, list):
        input_text = "".join(str(item) for item in input_payload)
    else:
        input_text = str(input_payload)
    est_input_tokens = len(input_text) // 4
    max_tokens = int(body.get("max_output_tokens") or body.get("max_tokens") or model_config.limits.max_tokens)
    return (
        est_input_tokens * model_config.pricing.input_micro
        + max_tokens * model_config.pricing.output_micro
    )


def _estimate_embeddings_cost(body: dict, model_config) -> int:
    input_payload = body.get("input", "")
    if isinstance(input_payload, list):
        input_text = "".join(str(item) for item in input_payload)
    else:
        input_text = str(input_payload)
    est_input_tokens = max(1, len(input_text) // 4)
    return est_input_tokens * model_config.pricing.input_micro


def _estimate_cost(endpoint: str, body: dict, model_config) -> int:
    if endpoint.endswith("/chat/completions"):
        return _estimate_chat_cost(body, model_config)
    if endpoint.endswith("/responses"):
        return _estimate_responses_cost(body, model_config)
    if endpoint.endswith("/embeddings"):
        return _estimate_embeddings_cost(body, model_config)
    raise HTTPException(status_code=400, detail=f"Unsupported endpoint '{endpoint}'")


def _apply_patch(original: dict, patch: dict) -> dict:
    if not patch:
        return original
    body = dict(original)
    for key in sorted(patch.keys()):
        if key in _SUPPORTED_PATCH_KEYS:
            body[key] = patch[key]
    return body


async def admit_request(
    *,
    endpoint: str,
    body: dict[str, Any],
    headers: dict[str, str],
    agent_info: dict,
    explicit_execution_id: str | None = None,
) -> AdmissionResult:
    """Execute full admission pipeline and reserve budget."""
    agent = agent_info["name"]
    ensure_agent_can_execute(agent_info)

    model_name = body.get("model") or config_loader.get_default_model()
    route_plan, route_error = resolve_route(endpoint, model_name)
    if route_error:
        raise HTTPException(status_code=403, detail=route_error)
    model_config = config_loader.get_model(model_name)
    if body.get("tools") and not model_config.capabilities.tools:
        raise HTTPException(status_code=400, detail=f"Model '{model_name}' does not support tools")

    execution_id, request_hash = execution_id_for_request(
        agent=agent,
        endpoint=endpoint,
        body=body,
        idempotency_key=headers.get("idempotency-key"),
        step_id=headers.get("x-aex-step-id"),
        explicit_execution_id=explicit_execution_id,
    )

    tenant_id = (agent_info.get("tenant_id") or "default").strip() or "default"
    project_id = (agent_info.get("project_id") or "default").strip() or "default"

    cached = get_execution_cache(execution_id)
    if cached and cached.request_hash and cached.request_hash != request_hash:
        raise HTTPException(
            status_code=409,
            detail="Idempotency conflict: execution_id is already bound to a different request hash",
        )
    if cached and cached.state in {"COMMITTED", "DENIED", "RELEASED", "FAILED"}:
        return AdmissionResult(
            execution_id=execution_id,
            request_hash=request_hash,
            route_plan=route_plan,
            policy=PolicyDecision(True, None, [], {}, "cache", []),
            request_body=body,
            estimated_cost_micro=0,
            tenant_id=tenant_id,
            project_id=project_id,
            idempotent_replay=True,
            cached_status_code=cached.status_code,
            cached_response_body=cached.response_body,
            cached_error_body=cached.error_body,
        )
    if cached and cached.state not in {"COMMITTED", "DENIED", "RELEASED", "FAILED"}:
        awaited = await _wait_for_terminal_cache(execution_id)
        if awaited:
            return AdmissionResult(
                execution_id=execution_id,
                request_hash=request_hash,
                route_plan=route_plan,
                policy=PolicyDecision(True, None, [], {}, "cache", []),
                request_body=body,
                estimated_cost_micro=0,
                tenant_id=tenant_id,
                project_id=project_id,
                idempotent_replay=True,
                cached_status_code=awaited.status_code,
                cached_response_body=awaited.response_body,
                cached_error_body=awaited.error_body,
            )
        raise HTTPException(
            status_code=409,
            detail=f"Execution already in progress for idempotency key ({execution_id})",
        )

    check_rate_limit(agent, tenant_id=tenant_id, project_id=project_id)

    policy = evaluate_request(
        agent_caps=agent_info,
        payload=body,
        model_name=model_name,
        endpoint=endpoint,
        execution_id=execution_id,
    )
    if not policy.allow:
        with get_db_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            append_hash_event(
                conn,
                execution_id=execution_id,
                agent=agent,
                tenant_id=tenant_id,
                project_id=project_id,
                event_type="policy.violation",
                payload={"reason": policy.reason, "endpoint": endpoint},
            )
            append_compat_event(
                conn,
                agent=agent,
                tenant_id=tenant_id,
                project_id=project_id,
                action="POLICY_VIOLATION",
                cost_micro=0,
                metadata={"reason": policy.reason, "endpoint": endpoint},
            )
            conn.commit()
        raise HTTPException(status_code=403, detail=f"Policy violation: {policy.reason}")

    patched_body = _apply_patch(body, policy.patch)

    estimated_cost = _estimate_cost(endpoint, patched_body, model_config)

    decision = reserve_budget_v2(
        agent=agent,
        tenant_id=tenant_id,
        project_id=project_id,
        execution_id=execution_id,
        endpoint=endpoint,
        request_hash=request_hash,
        estimated_cost_micro=estimated_cost,
        policy_hash=policy.decision_hash,
        route_hash=route_plan.route_hash,
        reservation_ttl_seconds=int(os.getenv("AEX_RESERVATION_TTL_SECONDS", "180")),
    )
    if decision.reused and decision.state == "RESERVED":
        awaited = await _wait_for_terminal_cache(execution_id)
        if awaited:
            return AdmissionResult(
                execution_id=execution_id,
                request_hash=request_hash,
                route_plan=route_plan,
                policy=policy,
                request_body=patched_body,
                estimated_cost_micro=estimated_cost,
                tenant_id=tenant_id,
                project_id=project_id,
                idempotent_replay=True,
                cached_status_code=awaited.status_code,
                cached_response_body=awaited.response_body,
                cached_error_body=awaited.error_body,
            )
        raise HTTPException(
            status_code=409,
            detail=f"Execution already in progress for idempotency key ({execution_id})",
        )
    if decision.reused and decision.state in {"COMMITTED", "DENIED", "RELEASED", "FAILED"}:
        return AdmissionResult(
            execution_id=execution_id,
            request_hash=request_hash,
            route_plan=route_plan,
            policy=policy,
            request_body=patched_body,
            estimated_cost_micro=estimated_cost,
            tenant_id=tenant_id,
            project_id=project_id,
            idempotent_replay=True,
            cached_status_code=decision.status_code,
            cached_response_body=decision.response_body,
            cached_error_body=decision.error_body,
        )

    return AdmissionResult(
        execution_id=execution_id,
        request_hash=request_hash,
        route_plan=route_plan,
        policy=policy,
        request_body=patched_body,
        estimated_cost_micro=estimated_cost,
        tenant_id=tenant_id,
        project_id=project_id,
    )
