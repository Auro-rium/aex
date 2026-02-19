"""AEX core proxy — /v1/chat/completions endpoint with governance enforcement."""

import os
import re

from fastapi import APIRouter, Request, HTTPException, Depends

from ..auth import get_agent_from_token
from ..utils.budget import reserve_budget, release_reservation_on_error
from ..utils.rate_limit import check_rate_limit
from ..utils.config_loader import config_loader
from ..utils.policy_engine import validate_request
from ..utils.logging_config import StructuredLogger
from .streaming import handle_streaming
from .non_streaming import handle_non_streaming

logger = StructuredLogger(__name__)

router = APIRouter()


def _sanitize_provider_key(provider: str) -> str:
    """Sanitize provider name to env var format: MY_CUSTOM_PROVIDER_API_KEY"""
    return re.sub(r'[^A-Z0-9_]', '', provider.upper().replace('-', '_'))


def _build_upstream_body(body: dict, model_config) -> dict:
    """Build the upstream request body from the client request."""
    upstream_body = {
        "model": model_config.provider_model,
        "messages": body.get("messages", []),
        "temperature": body.get("temperature", 1.0),
        "top_p": body.get("top_p", 1.0),
        "stream": body.get("stream", False),
        "stop": body.get("stop"),
    }

    if body.get("tools"):
        upstream_body["tools"] = body["tools"]
        if body.get("tool_choice"):
            upstream_body["tool_choice"] = body["tool_choice"]

    # Structured output passthrough
    if body.get("response_format"):
        upstream_body["response_format"] = body["response_format"]

    if body.get("max_tokens"):
        req_max = int(body["max_tokens"])
        if req_max > model_config.limits.max_tokens:
            raise HTTPException(
                status_code=400,
                detail=f"max_tokens {req_max} exceeds limit {model_config.limits.max_tokens}"
            )
        upstream_body["max_tokens"] = req_max
    else:
        upstream_body["max_tokens"] = model_config.limits.max_tokens

    return upstream_body


def _estimate_cost(upstream_body: dict, model_config) -> int:
    """Estimate cost in micro-units (integer only)."""
    input_text = "".join(str(m.get("content", "")) for m in upstream_body["messages"])
    est_input_tokens = len(input_text) // 4
    est_input_cost = est_input_tokens * model_config.pricing.input_micro
    est_output_cost = upstream_body["max_tokens"] * model_config.pricing.output_micro
    return est_input_cost + est_output_cost


@router.post("/v1/chat/completions")
@router.post("/openai/v1/chat/completions")
async def proxy_chat_completions(request: Request, agent_info: dict = Depends(get_agent_from_token)):
    """OpenAI-compatible chat completions proxy with governance enforcement."""
    agent = agent_info["name"]

    # 0. Scope enforcement — read-only tokens cannot execute
    if agent_info.get("token_scope") == "read-only":
        raise HTTPException(status_code=403, detail="Read-only token cannot execute chat completions")

    # 1. Rate limit
    check_rate_limit(agent)

    # 2. Parse body
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Validate messages field
    messages = body.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="'messages' must be a non-empty list")

    model_name = body.get("model") or config_loader.get_default_model()

    model_config = config_loader.get_model(model_name)
    if not model_config:
        logger.warning("Unknown model rejected", agent=agent, model=model_name)
        raise HTTPException(status_code=403, detail=f"Model '{model_name}' not allowed")

    # 3. Model capability enforcement
    if body.get("tools") and not model_config.capabilities.tools:
        raise HTTPException(status_code=400, detail=f"Model '{model_name}' does not support tools")

    # 4. Policy engine — agent capability enforcement
    policy_ok, rejection_reason = validate_request(agent_info, body, model_name)
    if not policy_ok:
        from ..db import get_db_connection
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO events (agent, action, cost_micro, metadata) VALUES (?, ?, ?, ?)",
                (agent, "POLICY_VIOLATION", 0, rejection_reason)
            )
            conn.commit()
        logger.warning("Policy violation", agent=agent, reason=rejection_reason)
        raise HTTPException(status_code=403, detail=f"Policy violation: {rejection_reason}")

    # 5. Build upstream request
    upstream_body = _build_upstream_body(body, model_config)

    # 6. Cost estimation (integer micro-units only)
    estimated_cost_micro = _estimate_cost(upstream_body, model_config)

    # 7. Reserve budget
    reserve_budget(agent, estimated_cost_micro)

    # 8. Resolve provider + API key
    provider_config = config_loader.get_provider(model_config.provider)
    if not provider_config:
        release_reservation_on_error(agent, estimated_cost_micro)
        raise HTTPException(status_code=500, detail="Provider configuration error")

    # 8b. Provider key resolution: passthrough or daemon-owned
    passthrough_key = request.headers.get("x-aex-provider-key")
    if passthrough_key:
        # Agent must be explicitly allowed to use passthrough
        if not agent_info.get("allow_passthrough", 0):
            release_reservation_on_error(agent, estimated_cost_micro)
            raise HTTPException(
                status_code=403,
                detail="Passthrough mode not enabled for this agent"
            )
        api_key = passthrough_key
        logger.info("Using passthrough provider key", agent=agent)
    else:
        # Standard path: daemon-owned provider key
        env_key = f"{_sanitize_provider_key(model_config.provider)}_API_KEY"
        api_key = os.getenv(env_key, "")
        if not api_key:
            release_reservation_on_error(agent, estimated_cost_micro)
            raise HTTPException(status_code=500, detail=f"API key not configured for provider '{model_config.provider}'")

    target_url = f"{provider_config.base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # 9. Dispatch: streaming or non-streaming
    if upstream_body.get("stream"):
        return await handle_streaming(
            agent, model_name, model_config,
            estimated_cost_micro, target_url, headers, upstream_body
        )
    else:
        return await handle_non_streaming(
            agent, agent_info, model_name, model_config,
            estimated_cost_micro, target_url, headers, upstream_body
        )
