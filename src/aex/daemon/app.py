import os
import re
import json
import asyncio
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, StreamingResponse
import httpx
from dotenv import load_dotenv
from aex import __version__

from .db import init_db, check_db_integrity
from .logging_config import setup_logging, StructuredLogger
from .auth import get_agent_from_token
from .budget import reserve_budget, commit_usage, release_reservation_on_error, clear_all_reservations
from .rate_limit import check_rate_limit
from .supervisor import cleanup_dead_processes
from .metrics import get_metrics
from .config_loader import config_loader

load_dotenv(os.path.expanduser("~/.aex/.env"))

setup_logging(os.getenv("AEX_LOG_LEVEL", "INFO"))
logger = StructuredLogger(__name__)

app = FastAPI(title="AEX Kernel", version=__version__)

# Shared async client for connection pooling / keep-alive
_http_client: httpx.AsyncClient | None = None

async def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=60.0,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
        )
    return _http_client


@app.on_event("startup")
async def startup_event():
    init_db()
    if not check_db_integrity():
        logger.critical("Database integrity check failed on startup. Exiting.")
        os._exit(1)

    config_loader.load_config()

    logger.info("Clearing stale reservations...")
    clear_all_reservations()

    asyncio.create_task(enforcement_loop())


@app.on_event("shutdown")
async def shutdown_event():
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()


async def enforcement_loop():
    logger.info("Enforcement loop started")
    while True:
        try:
            cleanup_dead_processes()
            await asyncio.sleep(2)
        except Exception as e:
            logger.error("Enforcement loop error", error=str(e))
            await asyncio.sleep(5)


# --- Admin Endpoints ---

@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__}

@app.get("/metrics")
async def metrics_endpoint():
    return get_metrics()

@app.post("/admin/reload_config")
async def reload_config_endpoint():
    try:
        config_loader.load_config()
        return {"status": "ok", "message": "Configuration reloaded"}
    except Exception as e:
        logger.error("Config reload failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# --- Core Proxy ---

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


@app.post("/v1/chat/completions")
@app.post("/openai/v1/chat/completions")
async def proxy_chat_completions(request: Request, agent: str = Depends(get_agent_from_token)):
    """OpenAI-compatible chat completions proxy with governance enforcement."""

    # 1. Rate limit
    check_rate_limit(agent)

    # 2. Parse body
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    model_name = body.get("model") or config_loader.get_default_model()

    model_config = config_loader.get_model(model_name)
    if not model_config:
        logger.warning("Unknown model rejected", agent=agent, model=model_name)
        raise HTTPException(status_code=403, detail=f"Model '{model_name}' not allowed")

    # 3. Capability enforcement
    if body.get("tools") and not model_config.capabilities.tools:
        raise HTTPException(status_code=400, detail=f"Model '{model_name}' does not support tools")

    # 4. Build upstream request
    upstream_body = _build_upstream_body(body, model_config)

    # 5. Cost estimation (integer micro-units only)
    estimated_cost_micro = _estimate_cost(upstream_body, model_config)

    # 6. Reserve budget
    reserve_budget(agent, estimated_cost_micro)

    # 7. Resolve provider
    provider_config = config_loader.get_provider(model_config.provider)
    if not provider_config:
        release_reservation_on_error(agent, estimated_cost_micro)
        raise HTTPException(status_code=500, detail="Provider configuration error")

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

    # 8. Dispatch: streaming or non-streaming
    if upstream_body.get("stream"):
        return await _handle_streaming(
            agent, model_name, model_config,
            estimated_cost_micro, target_url, headers, upstream_body
        )
    else:
        return await _handle_non_streaming(
            agent, model_name, model_config,
            estimated_cost_micro, target_url, headers, upstream_body
        )


async def _handle_non_streaming(
    agent, model_name, model_config,
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

            commit_usage(agent, estimated_cost_micro, actual_cost_micro)

            # Never expose provider_model externally
            resp_json["model"] = model_name
            return JSONResponse(content=resp_json, status_code=200)
        else:
            release_reservation_on_error(agent, estimated_cost_micro)
            return JSONResponse(content=response.json(), status_code=response.status_code)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Proxy error", error=str(e))
        release_reservation_on_error(agent, estimated_cost_micro)
        raise HTTPException(status_code=502, detail="Upstream provider error")


async def _handle_streaming(
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
