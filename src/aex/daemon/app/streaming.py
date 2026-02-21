"""AEX streaming proxy handler — SSE relay with cost settlement."""

import json

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from ..utils.logging_config import StructuredLogger
from ..ledger import commit_execution_usage, release_execution_reservation
from ..ledger.budget import mark_execution_dispatched
from .lifecycle import get_http_client

logger = StructuredLogger(__name__)


async def handle_streaming(
    *,
    agent,
    execution_id: str,
    model_name,
    model_config,
    estimated_cost_micro,
    target_url,
    headers,
    upstream_body,
):
    """
    Handle streaming proxy request.

    Protocol:
    - Pre-flight reserve estimated cost (already done by caller)
    - Forward SSE chunks transparently, replacing model name
    - On final chunk: extract usage, compute actual cost, settle
    - On abort/error: release full reservation
    """
    client = await get_http_client()
    mark_execution_dispatched(execution_id)

    try:
        upstream_req = client.build_request("POST", target_url, json=upstream_body, headers=headers)
        response = await client.send(upstream_req, stream=True)

        if response.status_code != 200:
            await response.aread()
            release_execution_reservation(
                agent=agent,
                execution_id=execution_id,
                estimated_cost_micro=estimated_cost_micro,
                reason=f"Streaming upstream failed with HTTP {response.status_code}",
                status_code=response.status_code,
            )
            try:
                err_body = response.json()
            except Exception:
                err_body = {"error": response.text}
            from fastapi.responses import JSONResponse
            return JSONResponse(content=err_body, status_code=response.status_code)

        async def stream_generator():
            """Async generator that relays SSE chunks and settles cost at the end."""
            settled = False
            completion_tokens_count = 0
            prompt_tokens_count = 0

            try:
                async for line in response.aiter_lines():
                    if not line:
                        yield "\n"
                        continue

                    if line.startswith("data: "):
                        data_str = line[6:]

                        if data_str.strip() == "[DONE]":
                            yield f"data: [DONE]\n\n"
                            continue

                        try:
                            chunk = json.loads(data_str)

                            # Replace model name — never expose provider_model
                            chunk["model"] = model_name

                            # Track usage if present in chunk
                            if "usage" in chunk and chunk["usage"]:
                                usage = chunk["usage"]
                                pt = usage.get("prompt_tokens", 0)
                                if pt:
                                    prompt_tokens_count = pt
                                ct = usage.get("completion_tokens", 0)
                                if ct:
                                    completion_tokens_count = ct

                            # Count delta tokens from choices
                            for choice in chunk.get("choices", []):
                                delta = choice.get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    completion_tokens_count += max(1, len(content) // 4)

                            yield f"data: {json.dumps(chunk)}\n\n"

                        except json.JSONDecodeError:
                            yield f"{line}\n"
                    else:
                        yield f"{line}\n"

                # Stream completed — settle cost
                actual_cost_micro = (
                    prompt_tokens_count * model_config.pricing.input_micro
                    + completion_tokens_count * model_config.pricing.output_micro
                )
                commit_execution_usage(
                    agent=agent,
                    execution_id=execution_id,
                    estimated_cost_micro=estimated_cost_micro,
                    actual_cost_micro=actual_cost_micro,
                    prompt_tokens=prompt_tokens_count,
                    completion_tokens=completion_tokens_count,
                    model_name=model_name,
                    response_body={"stream": True, "usage": {"prompt_tokens": prompt_tokens_count, "completion_tokens": completion_tokens_count}},
                )
                settled = True

            except Exception as e:
                logger.error("Streaming error", error=str(e), agent=agent)
                if not settled:
                    release_execution_reservation(
                        agent=agent,
                        execution_id=execution_id,
                        estimated_cost_micro=estimated_cost_micro,
                        reason="Streaming relay failed",
                        status_code=502,
                    )
                raise
            finally:
                await response.aclose()
                if not settled:
                    release_execution_reservation(
                        agent=agent,
                        execution_id=execution_id,
                        estimated_cost_micro=estimated_cost_micro,
                        reason="Streaming ended before settlement",
                        status_code=502,
                    )

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Streaming setup error", error=str(e))
        release_execution_reservation(
            agent=agent,
            execution_id=execution_id,
            estimated_cost_micro=estimated_cost_micro,
            reason="Streaming setup error",
            status_code=502,
        )
        raise HTTPException(status_code=502, detail="Upstream provider error")
