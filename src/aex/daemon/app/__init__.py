"""AEX Daemon Application package.

Creates the FastAPI app, registers routers, and wires up lifecycle events.
Re-exports `app` so consumers can continue using:
    from .app import app
"""

import os
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from aex import __version__
from ..utils.logging_config import setup_logging

load_dotenv()
setup_logging(os.getenv("AEX_LOG_LEVEL", "INFO"))

app = FastAPI(title="AEX Kernel", version=__version__)


def _split_csv_env(name: str) -> list[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


cors_origins = _split_csv_env("AEX_CORS_ORIGINS")
if cors_origins:
    allow_credentials = "*" not in cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

trusted_hosts = _split_csv_env("AEX_ALLOWED_HOSTS")
if trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

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
from .v2 import router as v2_router

app.include_router(admin_router)
app.include_router(proxy_router)
app.include_router(v2_router)

__all__ = ["app"]
