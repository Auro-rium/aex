"""Public smoke checks."""

from __future__ import annotations

from ..client import ProdCheckClient
from ..models import CheckResult


def _health_validator(response) -> tuple[bool, str]:
    payload = response.json()
    status = str(payload.get("status", ""))
    if status.lower() != "ok":
        return False, f"Unexpected health status: {status}"
    return True, "health=ok"


def _ready_validator(response) -> tuple[bool, str]:
    payload = response.json()
    if not bool(payload.get("ready")):
        return False, f"ready=false; checks={payload.get('checks')}"
    return True, "ready=true"


def _alerts_validator(response) -> tuple[bool, str]:
    payload = response.json()
    summary = payload.get("summary", {})
    if not isinstance(summary, dict):
        return False, "Missing alert summary object"
    total = int(summary.get("total", 0))
    critical = int(summary.get("critical", 0))
    return True, f"alerts total={total} critical={critical}"


def _dashboard_validator(response) -> tuple[bool, str]:
    payload = response.json()
    if "summary" not in payload:
        return False, "Missing summary field"
    return True, f"dashboard_ok={payload.get('dashboard_ok')}"


def run(client: ProdCheckClient) -> list[CheckResult]:
    return [
        client.run_check(
            name="health_endpoint",
            category="smoke",
            method="GET",
            path="/health",
            auth=False,
            validator=_health_validator,
        ),
        client.run_check(
            name="ready_endpoint",
            category="smoke",
            method="GET",
            path="/ready",
            auth=False,
            expect_status={200, 503},
            validator=_ready_validator,
        ),
        client.run_check(
            name="admin_alerts",
            category="smoke",
            method="GET",
            path="/admin/alerts",
            auth=False,
            validator=_alerts_validator,
        ),
        client.run_check(
            name="admin_dashboard_data",
            category="smoke",
            method="GET",
            path="/admin/dashboard/data?limit=20",
            auth=False,
            validator=_dashboard_validator,
        ),
    ]
