"""AEX admin endpoints — health/readiness, metrics, dashboard, config reload."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from ..frontend import activity_snapshot, dashboard_payload
from ..observability import collect_active_alerts, liveness_report, readiness_report, summarize_alerts
from ..utils.config_loader import config_loader
from ..utils.logging_config import StructuredLogger
from ..utils.metrics import get_metrics

logger = StructuredLogger(__name__)
router = APIRouter()


@router.get("/admin/activity")
async def activity_feed_endpoint(limit: int = Query(default=40, ge=10, le=200)):
    """Return recent backend activity for the local dashboard UI."""
    return activity_snapshot(limit=limit)


@router.get("/admin/dashboard/data")
async def dashboard_data_endpoint(limit: int = Query(default=120, ge=20, le=500)):
    """Backend-oriented payload for the dashboard UI."""
    return dashboard_payload(limit=limit)


@router.get("/admin/alerts")
async def alerts_endpoint():
    alerts = collect_active_alerts()
    return {"alerts": alerts, "summary": summarize_alerts(alerts)}


@router.post("/admin/reload_config")
async def reload_config_endpoint():
    try:
        config_loader.load_config()
        return {"status": "ok", "message": "Configuration reloaded"}
    except Exception as e:
        logger.error("Config reload failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/replay")
async def replay_audit_endpoint():
    payload = dashboard_payload(limit=40)
    return payload["replay"]


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_endpoint():
    """Serve lightweight local-only metrics dashboard."""
    dashboard_path = Path(__file__).parent.parent / "frontend" / "dashboard.html"
    if dashboard_path.exists():
        return HTMLResponse(content=dashboard_path.read_text(), status_code=200)
    raise HTTPException(status_code=404, detail="Dashboard not available")


@router.get("/health")
async def health():
    return liveness_report()


@router.get("/ready")
async def ready():
    ready_ok, report = readiness_report()
    status_code = 200 if ready_ok else 503
    return JSONResponse(content=report, status_code=status_code)


@router.get("/metrics")
async def metrics_endpoint():
    return get_metrics()
