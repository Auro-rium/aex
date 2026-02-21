"""
AEX Policy Engine — Pure-function request/response validation.

Properties:
- Deterministic
- Pure functions (no side effects except return values)
- No DB access (receives agent row as dict)
- Logging happens in caller
"""

import json
from typing import Optional


def validate_request(
    agent_caps: dict,
    payload: dict,
    model_name: str,
) -> tuple[bool, Optional[str]]:
    """
    Validate a request against agent capability permissions.

    Args:
        agent_caps: Dict with agent capability fields from DB row.
        payload: The parsed JSON request body.
        model_name: The resolved model name.

    Returns:
        (True, None) if allowed.
        (False, reason) if rejected.
    """

    # --- Model whitelist ---
    allowed_models_raw = agent_caps.get("allowed_models")
    if allowed_models_raw:
        try:
            allowed_models = json.loads(allowed_models_raw)
            if isinstance(allowed_models, list) and model_name not in allowed_models:
                return False, f"Model '{model_name}' not in allowed models: {allowed_models}"
        except (json.JSONDecodeError, TypeError):
            pass  # Malformed JSON treated as no restriction

    # --- Streaming gate ---
    if payload.get("stream") and not agent_caps.get("allow_streaming", 1):
        return False, "Streaming is disabled for this agent"

    # --- Tool gate ---
    if payload.get("tools"):
        if not agent_caps.get("allow_tools", 1):
            return False, "Tool usage is disabled for this agent"

        # Tool name whitelist
        allowed_tool_names_raw = agent_caps.get("allowed_tool_names")
        if allowed_tool_names_raw:
            try:
                allowed_names = json.loads(allowed_tool_names_raw)
                if isinstance(allowed_names, list):
                    for tool in payload["tools"]:
                        tool_name = tool.get("function", {}).get("name", "")
                        if tool_name and tool_name not in allowed_names:
                            return False, f"Tool '{tool_name}' not in allowed tools: {allowed_names}"
            except (json.JSONDecodeError, TypeError):
                pass

    # --- Function calling gate ---
    if payload.get("tool_choice") and not agent_caps.get("allow_function_calling", 1):
        return False, "Function calling is disabled for this agent"

    # --- Vision gate ---
    if not agent_caps.get("allow_vision", 0):
        messages = payload.get("messages", [])
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "image_url":
                            return False, "Vision (image inputs) is disabled for this agent"

    # --- Max input tokens ---
    max_input = agent_caps.get("max_input_tokens")
    if max_input is not None:
        input_text = "".join(str(m.get("content", "")) for m in payload.get("messages", []))
        est_tokens = len(input_text) // 4
        if est_tokens > max_input:
            return False, f"Estimated input tokens ({est_tokens}) exceeds agent limit ({max_input})"

    # --- Max output tokens ---
    max_output = agent_caps.get("max_output_tokens")
    if max_output is not None:
        req_max_out = payload.get("max_tokens")
        if req_max_out and int(req_max_out) > max_output:
            return False, f"Requested max_tokens ({req_max_out}) exceeds agent limit ({max_output})"

    # --- Max tokens per request (Total: Input + Output) ---
    max_total = agent_caps.get("max_tokens_per_request")
    if max_total is not None:
        input_text = "".join(str(m.get("content", "")) for m in payload.get("messages", []))
        est_input_tokens = len(input_text) // 4
        req_out = payload.get("max_tokens", 0)  # If not provided, proxy sets it later to model max, but we check what we can here
        est_total = est_input_tokens + int(req_out)
        if est_total > max_total:
            return False, f"Estimated total tokens ({est_total}) exceeds agent per-request limit ({max_total})"

    # --- Strict mode ---
    if agent_caps.get("strict_mode", 0):
        # In strict mode, everything not explicitly allowed is denied
        if payload.get("stream") and not agent_caps.get("allow_streaming", 0):
            return False, "Strict mode: streaming not explicitly allowed"
        if payload.get("tools") and not agent_caps.get("allow_tools", 0):
            return False, "Strict mode: tools not explicitly allowed"

    return True, None


def validate_response(
    agent_caps: dict,
    response: dict,
) -> tuple[bool, Optional[str]]:
    """
    Validate a response (post-flight check).

    Args:
        agent_caps: Dict with agent capability fields from DB row.
        response: The parsed JSON response body.

    Returns:
        (True, None) if acceptable.
        (False, reason) if flagged.
    """
    # --- Max output tokens post-check ---
    max_output = agent_caps.get("max_output_tokens")
    if max_output is not None:
        usage = response.get("usage", {})
        actual_output = usage.get("completion_tokens", 0)
        if actual_output > max_output:
            return False, f"Response output tokens ({actual_output}) exceeded agent limit ({max_output})"

    return True, None
