"""AEX Daemon Application package.

Creates the FastAPI app, registers routers, and wires up lifecycle events.
Re-exports `app` so consumers can continue using:
    from .app import app
"""

import os
from dotenv import load_dotenv
from fastapi import FastAPI

from aex import __version__
from ..utils.logging_config import setup_logging

load_dotenv(os.path.expanduser("~/.aex/.env"))
setup_logging(os.getenv("AEX_LOG_LEVEL", "INFO"))

app = FastAPI(title="AEX Kernel", version=__version__)

# --- Lifecycle ---
from .lifecycle import startup_event, shutdown_event


@app.on_event("startup")
async def _startup():
    await startup_event(app)


@app.on_event("shutdown")
async def _shutdown():
    await shutdown_event()


# --- Routers ---
from .admin import router as admin_router
from .proxy import router as proxy_router

app.include_router(admin_router)
app.include_router(proxy_router)

__all__ = ["app"]
