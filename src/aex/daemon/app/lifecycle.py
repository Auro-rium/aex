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
    init_db()
    if not check_db_integrity():
        logger.critical("Database integrity check failed on startup. Exiting.")
        os._exit(1)

    config_loader.load_config()

    logger.info("Running crash recovery sweep...")
    recovery_summary = reconcile_incomplete_executions()
    logger.info("Recovery summary", **recovery_summary)

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
