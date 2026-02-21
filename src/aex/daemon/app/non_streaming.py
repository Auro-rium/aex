"""AEX non-streaming proxy handler."""

from fastapi.responses import JSONResponse

from ..utils.logging_config import StructuredLogger
from ..utils.budget import commit_usage, release_reservation_on_error
from ..utils.policy_engine import validate_response
from .lifecycle import get_http_client

logger = StructuredLogger(__name__)


async def handle_non_streaming(
    agent, agent_info, model_name, model_config,
    estimated_cost_micro, target_url, headers, upstream_body
):
    """Handle standard (non-streaming) proxy request."""
    client = await get_http_client()
    try:
        response = await client.post(target_url, json=upstream_body, headers=headers)

        if response.status_code == 200:
            resp_json = response.json()
            usage = resp_json.get("usage", {})

            actual_input = usage.get("prompt_tokens", 0)
            actual_output = usage.get("completion_tokens", 0)
            actual_cost_micro = (
                actual_input * model_config.pricing.input_micro
                + actual_output * model_config.pricing.output_micro
            )

            # Post-flight policy validation
            resp_ok, resp_reason = validate_response(agent_info, resp_json)
            if not resp_ok:
                logger.warning("Post-flight policy violation", agent=agent, reason=resp_reason)

            commit_usage(agent, estimated_cost_micro, actual_cost_micro, prompt_tokens=actual_input, completion_tokens=actual_output)

            # Never expose provider_model externally
            resp_json["model"] = model_name
            return JSONResponse(content=resp_json, status_code=200)
        else:
            release_reservation_on_error(agent, estimated_cost_micro)
            return JSONResponse(content=response.json(), status_code=response.status_code)

    except Exception as e:
        from fastapi import HTTPException
        logger.error("Proxy error", error=str(e))
        release_reservation_on_error(agent, estimated_cost_micro)
        raise HTTPException(status_code=502, detail="Upstream provider error")
