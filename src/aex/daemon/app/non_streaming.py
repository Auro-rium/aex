"""AEX non-streaming proxy handler."""

from fastapi.responses import JSONResponse

from ..utils.logging_config import StructuredLogger
from ..ledger import commit_execution_usage, release_execution_reservation
from ..policy.engine import evaluate_response
from ..ledger.budget import mark_execution_dispatched
from .lifecycle import get_http_client

logger = StructuredLogger(__name__)


async def handle_non_streaming(
    *,
    agent,
    agent_info,
    endpoint: str,
    execution_id: str,
    model_name,
    model_config,
    estimated_cost_micro,
    target_url,
    headers,
    upstream_body,
):
    """Handle standard (non-streaming) proxy request."""
    client = await get_http_client()
    mark_execution_dispatched(execution_id)
    try:
        response = await client.post(target_url, json=upstream_body, headers=headers)

        if response.status_code == 200:
            resp_json = response.json()
            usage = resp_json.get("usage", {}) or {}

            # OpenAI-compatible providers vary naming by endpoint.
            actual_input = int(
                usage.get("prompt_tokens", usage.get("input_tokens", usage.get("total_tokens", 0))) or 0
            )
            actual_output = int(
                usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
            )
            actual_cost_micro = (
                actual_input * model_config.pricing.input_micro
                + actual_output * model_config.pricing.output_micro
            )

            # Post-flight policy validation
            resp_ok, resp_reason = evaluate_response(agent_info, resp_json)
            if not resp_ok:
                logger.warning("Post-flight policy violation", agent=agent, reason=resp_reason)

            commit_execution_usage(
                agent=agent,
                execution_id=execution_id,
                estimated_cost_micro=estimated_cost_micro,
                actual_cost_micro=actual_cost_micro,
                prompt_tokens=actual_input,
                completion_tokens=actual_output,
                model_name=model_name,
                response_body=resp_json,
            )

            # Never expose provider_model externally
            if "model" in resp_json:
                resp_json["model"] = model_name
            return JSONResponse(content=resp_json, status_code=200)
        else:
            try:
                err_json = response.json()
            except Exception:
                err_json = {"error": response.text}
            detail = None
            if isinstance(err_json, dict):
                detail = err_json.get("error") or err_json.get("message") or err_json.get("detail")
            if isinstance(detail, dict):
                detail = detail.get("message") or str(detail)
            if not detail:
                detail = response.text
            detail_text = str(detail).replace("\n", " ")[:240]
            logger.warning(
                "Upstream request failed",
                endpoint=endpoint,
                status_code=response.status_code,
                detail=detail_text,
            )
            release_execution_reservation(
                agent=agent,
                execution_id=execution_id,
                estimated_cost_micro=estimated_cost_micro,
                reason=f"Upstream {endpoint} failed with HTTP {response.status_code}: {detail_text}",
                status_code=response.status_code,
            )
            return JSONResponse(content=err_json, status_code=response.status_code)

    except Exception as e:
        from fastapi import HTTPException
        logger.error("Proxy error", error=str(e))
        release_execution_reservation(
            agent=agent,
            execution_id=execution_id,
            estimated_cost_micro=estimated_cost_micro,
            reason="Upstream provider error",
            status_code=502,
        )
        raise HTTPException(status_code=502, detail="Upstream provider error")
