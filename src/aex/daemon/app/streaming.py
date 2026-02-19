"""AEX streaming proxy handler — SSE relay with cost settlement."""

import json

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from ..utils.logging_config import StructuredLogger
from ..utils.budget import commit_usage, release_reservation_on_error
from .lifecycle import get_http_client

logger = StructuredLogger(__name__)


async def handle_streaming(
    agent, model_name, model_config,
    estimated_cost_micro, target_url, headers, upstream_body
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

    try:
        upstream_req = client.build_request("POST", target_url, json=upstream_body, headers=headers)
        response = await client.send(upstream_req, stream=True)

        if response.status_code != 200:
            await response.aread()
            release_reservation_on_error(agent, estimated_cost_micro)
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
                commit_usage(agent, estimated_cost_micro, actual_cost_micro)
                settled = True

            except Exception as e:
                logger.error("Streaming error", error=str(e), agent=agent)
                if not settled:
                    release_reservation_on_error(agent, estimated_cost_micro)
                raise
            finally:
                await response.aclose()
                if not settled:
                    release_reservation_on_error(agent, estimated_cost_micro)

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
        release_reservation_on_error(agent, estimated_cost_micro)
        raise HTTPException(status_code=502, detail="Upstream provider error")
