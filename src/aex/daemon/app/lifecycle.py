"""AEX daemon lifecycle — startup, shutdown, enforcement loop, HTTP client."""

import os
import asyncio
import time

import httpx

from ..db import init_db, check_db_integrity
from ..utils.logging_config import StructuredLogger
from ..utils.supervisor import cleanup_dead_processes
from ..utils.config_loader import config_loader
from ..runtime import reconcile_incomplete_executions

logger = StructuredLogger(__name__)

# Shared async client for connection pooling / keep-alive
_http_client: httpx.AsyncClient | None = None


async def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=60.0,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=50)
        )
    return _http_client


async def startup_event(app):
    """Called on FastAPI startup."""
    strict_startup = (os.getenv("AEX_STARTUP_STRICT", "0").strip() == "1")
    init_timeout_sec = max(5, int(os.getenv("AEX_STARTUP_INIT_TIMEOUT_SECONDS", "30")))
    recovery_timeout_sec = max(5, int(os.getenv("AEX_STARTUP_RECOVERY_TIMEOUT_SECONDS", "30")))

    try:
        await asyncio.wait_for(asyncio.to_thread(init_db), timeout=init_timeout_sec)
    except Exception as exc:
        logger.error("Startup database init failed", error=str(exc), strict=strict_startup)
        if strict_startup:
            os._exit(1)

    try:
        ok = await asyncio.wait_for(asyncio.to_thread(check_db_integrity), timeout=init_timeout_sec)
        if not ok:
            logger.error("Startup database integrity failed", strict=strict_startup)
            if strict_startup:
                os._exit(1)
    except Exception as exc:
        logger.error("Startup database integrity error", error=str(exc), strict=strict_startup)
        if strict_startup:
            os._exit(1)

    try:
        config_loader.load_config()
    except Exception as exc:
        logger.error("Config load failed on startup", error=str(exc), strict=strict_startup)
        if strict_startup:
            os._exit(1)

    try:
        logger.info("Running crash recovery sweep...")
        recovery_summary = await asyncio.wait_for(
            asyncio.to_thread(reconcile_incomplete_executions),
            timeout=recovery_timeout_sec,
        )
        logger.info("Recovery summary", **recovery_summary)
    except Exception as exc:
        logger.error("Startup recovery sweep failed", error=str(exc), strict=strict_startup)
        if strict_startup:
            os._exit(1)

    asyncio.create_task(enforcement_loop())


async def shutdown_event():
    """Called on FastAPI shutdown."""
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()


async def enforcement_loop():
    logger.info("Enforcement loop started")
    recovery_interval_sec = max(5, int(os.getenv("AEX_RECOVERY_SWEEP_SECONDS", "15")))
    last_recovery = 0.0
    while True:
        try:
            cleanup_dead_processes()
            now = time.monotonic()
            if (now - last_recovery) >= recovery_interval_sec:
                summary = reconcile_incomplete_executions()
                if summary.get("released", 0) or summary.get("failed", 0):
                    logger.info(
                        "Recovery sweep applied",
                        released=summary.get("released", 0),
                        failed=summary.get("failed", 0),
                        scanned=summary.get("scanned", 0),
                    )
                last_recovery = now
            await asyncio.sleep(2)
        except Exception as e:
            logger.error("Enforcement loop error", error=str(e))
            await asyncio.sleep(5)
