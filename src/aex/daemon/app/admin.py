"""AEX admin endpoints — health, metrics, dashboard, config reload."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from aex import __version__
from ..utils.logging_config import StructuredLogger
from ..utils.metrics import get_metrics
from ..utils.config_loader import config_loader

logger = StructuredLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "version": __version__}


@router.get("/metrics")
async def metrics_endpoint():
    return get_metrics()


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_endpoint():
    """Serve lightweight local-only metrics dashboard."""
    dashboard_path = Path(__file__).parent.parent / "frontend" / "dashboard.html"
    if dashboard_path.exists():
        return HTMLResponse(content=dashboard_path.read_text(), status_code=200)
    raise HTTPException(status_code=404, detail="Dashboard not available")


@router.post("/admin/reload_config")
async def reload_config_endpoint():
    try:
        config_loader.load_config()
        return {"status": "ok", "message": "Configuration reloaded"}
    except Exception as e:
        logger.error("Config reload failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
