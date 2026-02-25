"""AEX core proxy endpoints with v2 admission and idempotent ledger accounting."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..auth import get_agent_from_token
from ..control import admit_request, resolve_scope
from ..control.lifecycle import ensure_agent_can_execute
from ..db import get_db_connection
from ..ledger import (
    commit_execution_usage,
    mark_execution_dispatched,
    release_execution_reservation,
    reserve_budget_v2,
)
from ..ledger.events import append_compat_event, append_hash_event
from ..observability import end_span, start_span
from ..sandbox import CapabilityToken, get_enabled_plugin, mint_token, run_plugin_tool
from ..sandbox.plugins import PluginError
from ..utils.deterministic import canonical_json, stable_hash_hex
from ..utils.config_loader import config_loader
from ..utils.logging_config import StructuredLogger
from .non_streaming import handle_non_streaming
from .streaming import handle_streaming

logger = StructuredLogger(__name__)
router = APIRouter()


class ToolExecuteRequest(BaseModel):
    tool_name: str = Field(..., min_length=1, max_length=128)
    arguments: dict[str, Any] | list[Any] | str | int | float | bool | None = None
    execution_id: str | None = None


def _sanitize_provider_key(provider: str) -> str:
    """Sanitize provider name to env var format: MY_CUSTOM_PROVIDER_API_KEY."""
    return re.sub(r"[^A-Z0-9_]", "", provider.upper().replace("-", "_"))


def _build_chat_upstream(body: dict, model_config) -> dict:
    upstream = {
        "model": model_config.provider_model,
        "messages": body.get("messages", []),
        "temperature": body.get("temperature", 1.0),
        "top_p": body.get("top_p", 1.0),
        "stream": body.get("stream", False),
        "stop": body.get("stop"),
    }

    if body.get("tools"):
        upstream["tools"] = body["tools"]
        if body.get("tool_choice"):
            upstream["tool_choice"] = body["tool_choice"]

    if body.get("response_format"):
        upstream["response_format"] = body["response_format"]

    req_max = body.get("max_tokens")
    if req_max:
        req_max = int(req_max)
        if req_max > model_config.limits.max_tokens:
            raise HTTPException(
                status_code=400,
                detail=f"max_tokens {req_max} exceeds limit {model_config.limits.max_tokens}",
            )
        upstream["max_tokens"] = req_max
    else:
        upstream["max_tokens"] = model_config.limits.max_tokens

    return upstream


def _build_responses_upstream(body: dict, model_config) -> dict:
    upstream = {
        "model": model_config.provider_model,
        "input": body.get("input", body.get("messages", [])),
        "instructions": body.get("instructions"),
        "temperature": body.get("temperature", 1.0),
        "top_p": body.get("top_p", 1.0),
    }

    if body.get("max_output_tokens"):
        upstream["max_output_tokens"] = int(body["max_output_tokens"])
    elif body.get("max_tokens"):
        upstream["max_output_tokens"] = int(body["max_tokens"])
    else:
        upstream["max_output_tokens"] = model_config.limits.max_tokens

    if body.get("tools"):
        upstream["tools"] = body["tools"]
    if body.get("metadata"):
        upstream["metadata"] = body["metadata"]

    return upstream


def _build_embeddings_upstream(body: dict, model_config) -> dict:
    upstream = {
        "model": model_config.provider_model,
        "input": body.get("input"),
    }
    provider_name = str(getattr(model_config, "provider", "") or "").strip().lower()
    unsupported_dims_raw = os.getenv("AEX_EMBEDDINGS_DIMENSIONS_UNSUPPORTED_PROVIDERS", "groq")
    unsupported_dims_providers = {
        p.strip().lower()
        for p in unsupported_dims_raw.split(",")
        if p.strip()
    }
    if body.get("encoding_format") is not None:
        upstream["encoding_format"] = body["encoding_format"]
    if body.get("dimensions") is not None and provider_name not in unsupported_dims_providers:
        upstream["dimensions"] = body["dimensions"]
    if body.get("user") is not None:
        upstream["user"] = body["user"]
    return upstream


def _resolve_provider_api_key(agent_info: dict, request: Request, provider_name: str) -> str:
    passthrough_key = request.headers.get("x-aex-provider-key")
    if passthrough_key:
        if not agent_info.get("allow_passthrough", 0):
            raise HTTPException(status_code=403, detail="Passthrough mode not enabled for this agent")
        logger.info("Using passthrough provider key", agent=agent_info["name"], provider=provider_name)
        return passthrough_key

    env_key = f"{_sanitize_provider_key(provider_name)}_API_KEY"
    api_key = os.getenv(env_key, "")
    if not api_key:
        raise HTTPException(status_code=500, detail=f"API key not configured for provider '{provider_name}'")
    return api_key


def _is_tool_allowed(agent_info: dict, tool_name: str) -> bool:
    if not agent_info.get("allow_tools", 1):
        return False
    raw = agent_info.get("allowed_tool_names")
    if not raw:
        return True
    try:
        allowed = json.loads(raw)
        if isinstance(allowed, list):
            return tool_name in allowed
    except Exception:
        return True
    return True


def _tool_exec_id(agent: str, tool_name: str, arguments: Any, explicit_id: str | None, idem_key: str | None) -> str:
    if explicit_id:
        return explicit_id
    payload = canonical_json(arguments if arguments is not None else {})
    if idem_key:
        return stable_hash_hex(agent, "tool.execute", tool_name, idem_key)
    return stable_hash_hex(agent, "tool.execute", tool_name, payload)


def _tool_cost_micro(manifest: dict[str, Any]) -> int:
    raw = manifest.get("cost_micro")
    if raw is None:
        raw = manifest.get("estimated_cost_micro", 500)
    try:
        cost = int(raw)
    except Exception:
        cost = 500
    return max(0, min(cost, 10_000_000))


async def _proxy_endpoint(request: Request, agent_info: dict, endpoint: str):
    if agent_info.get("token_scope") == "read-only":
        raise HTTPException(status_code=403, detail="Read-only token cannot execute model requests")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    resolve_scope({k.lower(): v for k, v in request.headers.items()}, agent_info)

    if endpoint.endswith("/chat/completions") and not isinstance(body.get("messages"), list):
        raise HTTPException(status_code=400, detail="'messages' must be a non-empty list")

    span = start_span("", "admission")
    admission = await admit_request(
        endpoint=endpoint,
        body=body,
        headers={k.lower(): v for k, v in request.headers.items()},
        agent_info=agent_info,
    )
    end_span(span, execution_id=admission.execution_id, endpoint=endpoint)

    if admission.cached_status_code is not None:
        if admission.cached_status_code >= 400:
            return JSONResponse(content=admission.cached_error_body or {"detail": "cached error"}, status_code=admission.cached_status_code)
        return JSONResponse(content=admission.cached_response_body or {}, status_code=admission.cached_status_code)

    execution_id = admission.execution_id
    model_name = admission.request_body.get("model") or config_loader.get_default_model()
    model_config = config_loader.get_model(model_name)
    provider_name = admission.route_plan.provider_name

    try:
        api_key = _resolve_provider_api_key(agent_info, request, provider_name)
    except HTTPException as exc:
        release_execution_reservation(
            agent=agent_info["name"],
            execution_id=execution_id,
            estimated_cost_micro=admission.estimated_cost_micro,
            reason=str(exc.detail),
            status_code=exc.status_code,
        )
        raise

    target_url = f"{admission.route_plan.base_url}{admission.route_plan.upstream_path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    if endpoint.endswith("/chat/completions"):
        try:
            upstream_body = _build_chat_upstream(admission.request_body, model_config)
        except HTTPException as exc:
            release_execution_reservation(
                agent=agent_info["name"],
                execution_id=execution_id,
                estimated_cost_micro=admission.estimated_cost_micro,
                reason=str(exc.detail),
                status_code=exc.status_code,
            )
            raise
        if upstream_body.get("stream"):
            return await handle_streaming(
                agent=agent_info["name"],
                execution_id=execution_id,
                model_name=model_name,
                model_config=model_config,
                estimated_cost_micro=admission.estimated_cost_micro,
                target_url=target_url,
                headers=headers,
                upstream_body=upstream_body,
            )
        return await handle_non_streaming(
            agent=agent_info["name"],
            agent_info=agent_info,
            endpoint=endpoint,
            execution_id=execution_id,
            model_name=model_name,
            model_config=model_config,
            estimated_cost_micro=admission.estimated_cost_micro,
            target_url=target_url,
            headers=headers,
            upstream_body=upstream_body,
        )

    if endpoint.endswith("/responses"):
        if admission.request_body.get("stream"):
            release_execution_reservation(
                agent=agent_info["name"],
                execution_id=execution_id,
                estimated_cost_micro=admission.estimated_cost_micro,
                reason="Streaming responses endpoint not yet supported",
                status_code=400,
            )
            raise HTTPException(status_code=400, detail="Streaming responses endpoint not yet supported")

        try:
            upstream_body = _build_responses_upstream(admission.request_body, model_config)
        except HTTPException as exc:
            release_execution_reservation(
                agent=agent_info["name"],
                execution_id=execution_id,
                estimated_cost_micro=admission.estimated_cost_micro,
                reason=str(exc.detail),
                status_code=exc.status_code,
            )
            raise
        return await handle_non_streaming(
            agent=agent_info["name"],
            agent_info=agent_info,
            endpoint=endpoint,
            execution_id=execution_id,
            model_name=model_name,
            model_config=model_config,
            estimated_cost_micro=admission.estimated_cost_micro,
            target_url=target_url,
            headers=headers,
            upstream_body=upstream_body,
        )

    if endpoint.endswith("/embeddings"):
        try:
            upstream_body = _build_embeddings_upstream(admission.request_body, model_config)
        except HTTPException as exc:
            release_execution_reservation(
                agent=agent_info["name"],
                execution_id=execution_id,
                estimated_cost_micro=admission.estimated_cost_micro,
                reason=str(exc.detail),
                status_code=exc.status_code,
            )
            raise
        return await handle_non_streaming(
            agent=agent_info["name"],
            agent_info=agent_info,
            endpoint=endpoint,
            execution_id=execution_id,
            model_name=model_name,
            model_config=model_config,
            estimated_cost_micro=admission.estimated_cost_micro,
            target_url=target_url,
            headers=headers,
            upstream_body=upstream_body,
        )

    raise HTTPException(status_code=404, detail="Unsupported proxy endpoint")


@router.post("/v1/tools/execute")
@router.post("/openai/v1/tools/execute")
async def proxy_tool_execute(
    body: ToolExecuteRequest,
    request: Request,
    agent_info: dict = Depends(get_agent_from_token),
):
    """Execute a registered tool plugin through AEX sandbox with capability checks."""
    if agent_info.get("token_scope") == "read-only":
        raise HTTPException(status_code=403, detail="Read-only token cannot execute tools")

    ensure_agent_can_execute(agent_info)
    scope = resolve_scope({k.lower(): v for k, v in request.headers.items()}, agent_info)
    agent = agent_info["name"]
    tool_name = body.tool_name.strip()
    if not tool_name:
        raise HTTPException(status_code=400, detail="tool_name is required")

    if not _is_tool_allowed(agent_info, tool_name):
        raise HTTPException(status_code=403, detail=f"Tool '{tool_name}' is not allowed for this agent")

    idem_key = request.headers.get("idempotency-key")
    execution_id = _tool_exec_id(agent, tool_name, body.arguments, body.execution_id, idem_key)
    request_hash = stable_hash_hex(agent, "tool.execute.request", tool_name, canonical_json(body.arguments))

    try:
        plugin = get_enabled_plugin(tool_name)
    except PluginError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    try:
        manifest = json.loads(plugin.get("manifest_json") or "{}")
    except Exception:
        manifest = {}

    allowed_fs = manifest.get("allowed_fs") if isinstance(manifest.get("allowed_fs"), list) else []
    package_path = str(plugin.get("package_path", ""))
    if package_path and package_path not in allowed_fs:
        allowed_fs = [*allowed_fs, package_path]

    net_policy = str(manifest.get("net_policy", "deny"))
    ttl_ms = int(manifest.get("ttl_ms", 3000))
    ttl_ms = max(100, min(ttl_ms, 60_000))
    max_output_bytes = int(manifest.get("max_output_bytes", 65536))
    max_output_bytes = max(1024, min(max_output_bytes, 1_000_000))
    tool_cost_micro = _tool_cost_micro(manifest)

    reservation = reserve_budget_v2(
        agent=agent,
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        execution_id=execution_id,
        endpoint="/v1/tools/execute",
        request_hash=request_hash,
        estimated_cost_micro=tool_cost_micro,
        policy_hash=stable_hash_hex("tool.exec.policy", agent, tool_name),
        route_hash=stable_hash_hex("tool.exec.route", tool_name),
    )
    if reservation.reused and reservation.state in {"COMMITTED", "DENIED", "RELEASED", "FAILED"}:
        status_code = reservation.status_code or 200
        if status_code >= 400:
            return JSONResponse(content=reservation.error_body or {"detail": "cached tool execution error"}, status_code=status_code)
        return JSONResponse(
            content={
                "execution_id": execution_id,
                "tool_name": tool_name,
                "result": (reservation.response_body or {}).get("result"),
                "stdout": (reservation.response_body or {}).get("stdout"),
                "stderr": (reservation.response_body or {}).get("stderr"),
            },
            status_code=status_code,
        )
    if reservation.reused and reservation.state == "RESERVED":
        raise HTTPException(status_code=409, detail=f"Tool execution already in progress ({execution_id})")

    if isinstance(body.arguments, dict):
        input_payload = body.arguments
    elif isinstance(body.arguments, list):
        input_payload = {"items": body.arguments}
    else:
        input_payload = {"value": body.arguments}

    cap_token = mint_token(
        CapabilityToken(
            execution_id=execution_id,
            agent=agent,
            tool_name=tool_name,
            allowed_fs=[str(p) for p in allowed_fs],
            net_policy=net_policy,
            ttl_ms=ttl_ms,
            max_output_bytes=max_output_bytes,
        )
    )
    mark_execution_dispatched(execution_id)

    try:
        result = await asyncio.to_thread(
            run_plugin_tool,
            plugin_name=tool_name,
            capability_token=cap_token,
            input_payload=input_payload,
        )
    except Exception as exc:
        release_execution_reservation(
            agent=agent,
            execution_id=execution_id,
            estimated_cost_micro=tool_cost_micro,
            reason=f"Tool execution failed: {exc}",
            status_code=400,
        )
        with get_db_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            append_hash_event(
                conn,
                execution_id=execution_id,
                agent=agent,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                event_type="tool.exec.denied",
                payload={"tool_name": tool_name, "error": str(exc)},
            )
            append_compat_event(
                conn,
                agent=agent,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                action="TOOL_EXEC_DENIED",
                metadata={"tool_name": tool_name, "error": str(exc)},
            )
            conn.commit()
        raise HTTPException(status_code=400, detail=f"Tool execution failed: {exc}")

    commit_execution_usage(
        agent=agent,
        execution_id=execution_id,
        estimated_cost_micro=tool_cost_micro,
        actual_cost_micro=tool_cost_micro,
        prompt_tokens=0,
        completion_tokens=0,
        model_name=f"tool:{tool_name}",
        response_body={
            "tool_name": tool_name,
            "result": result.get("result"),
            "stdout": result.get("stdout"),
            "stderr": result.get("stderr"),
        },
        status_code=200,
    )

    with get_db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        append_hash_event(
            conn,
            execution_id=execution_id,
            agent=agent,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            event_type="tool.exec",
            payload={"tool_name": tool_name, "cost_micro": tool_cost_micro},
        )
        append_compat_event(
            conn,
            agent=agent,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            action="TOOL_EXEC",
            metadata={"tool_name": tool_name, "execution_id": execution_id, "cost_micro": tool_cost_micro},
        )
        conn.commit()

    return JSONResponse(
        content={
            "execution_id": execution_id,
            "tool_name": tool_name,
            "result": result.get("result"),
            "stdout": result.get("stdout"),
            "stderr": result.get("stderr"),
        },
        status_code=200,
    )


@router.post("/v1/chat/completions")
@router.post("/openai/v1/chat/completions")
async def proxy_chat_completions(request: Request, agent_info: dict = Depends(get_agent_from_token)):
    return await _proxy_endpoint(request, agent_info, request.url.path)


@router.post("/v1/embeddings")
@router.post("/openai/v1/embeddings")
async def proxy_embeddings(request: Request, agent_info: dict = Depends(get_agent_from_token)):
    return await _proxy_endpoint(request, agent_info, request.url.path)


@router.post("/v1/responses")
@router.post("/openai/v1/responses")
async def proxy_responses(request: Request, agent_info: dict = Depends(get_agent_from_token)):
    return await _proxy_endpoint(request, agent_info, request.url.path)
