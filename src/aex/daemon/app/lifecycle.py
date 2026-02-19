"""AEX daemon lifecycle — startup, shutdown, enforcement loop, HTTP client."""

import os
import asyncio

import httpx

from ..db import init_db, check_db_integrity
from ..utils.logging_config import StructuredLogger
from ..utils.budget import clear_all_reservations
from ..utils.supervisor import cleanup_dead_processes
from ..utils.config_loader import config_loader

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

    logger.info("Clearing stale reservations...")
    clear_all_reservations()

    asyncio.create_task(enforcement_loop())


async def shutdown_event():
    """Called on FastAPI shutdown."""
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
