"""Rate limit checks with optional tenant/project-aware quota overrides."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import HTTPException

from ..db import get_db_connection
from .logging_config import StructuredLogger

logger = StructuredLogger(__name__)


def _resolve_limits(cursor, *, agent: str, tenant_id: str, project_id: str) -> tuple[int, int | None]:
    agent_row = cursor.execute(
        """
        SELECT rpm_limit, max_tokens_per_minute
        FROM agents
        WHERE name = ?
        """,
        (agent,),
    ).fetchone()
    if not agent_row:
        raise HTTPException(status_code=404, detail="Agent not found")

    rpm_limit = int(agent_row["rpm_limit"] or 0)
    tpm_limit = agent_row["max_tokens_per_minute"]

    # Quota override precedence: agent scope key only for now.
    scope_key = f"agent:{tenant_id}:{project_id}:{agent}"
    quota_row = cursor.execute(
        """
        SELECT rpm_limit, tpm_limit
        FROM quota_limits
        WHERE scope_key = ?
        """,
        (scope_key,),
    ).fetchone()
    if quota_row:
        if quota_row["rpm_limit"] is not None:
            rpm_limit = int(quota_row["rpm_limit"])
        if quota_row["tpm_limit"] is not None:
            tpm_limit = int(quota_row["tpm_limit"])

    return rpm_limit, tpm_limit


def check_rate_limit(agent: str, tenant_id: str | None = None, project_id: str | None = None):
    """Check RPM/TPM limits for an agent and current tenant/project scope."""
    tenant = (tenant_id or "default").strip() or "default"
    project = (project_id or "default").strip() or "default"

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        rpm_limit, tpm_limit = _resolve_limits(cursor, agent=agent, tenant_id=tenant, project_id=project)

        window_row = cursor.execute(
            "SELECT window_start, request_count, tokens_count FROM rate_windows WHERE agent = ?",
            (agent,),
        ).fetchone()

        now = datetime.utcnow()
        if window_row:
            window_start = datetime.fromisoformat(window_row["window_start"])
            if now - window_start > timedelta(minutes=1):
                cursor.execute(
                    """
                    UPDATE rate_windows
                    SET tenant_id = ?, project_id = ?, window_start = ?, request_count = 1, tokens_count = 0
                    WHERE agent = ?
                    """,
                    (tenant, project, now.isoformat(), agent),
                )
            else:
                if window_row["request_count"] >= rpm_limit:
                    cursor.execute(
                        "INSERT INTO events (tenant_id, project_id, agent, action, cost_micro, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                        (tenant, project, agent, "RATE_LIMIT", 0, f"RPM Limit: {rpm_limit}"),
                    )
                    conn.commit()
                    logger.warning("RPM rate limit exceeded", agent=agent, tenant_id=tenant, project_id=project, limit=rpm_limit)
                    raise HTTPException(status_code=429, detail="RPM rate limit exceeded")

                if tpm_limit is not None and window_row["tokens_count"] >= int(tpm_limit):
                    cursor.execute(
                        "INSERT INTO events (tenant_id, project_id, agent, action, cost_micro, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                        (tenant, project, agent, "RATE_LIMIT", 0, f"TPM Limit: {tpm_limit}"),
                    )
                    conn.commit()
                    logger.warning("TPM rate limit exceeded", agent=agent, tenant_id=tenant, project_id=project, limit=tpm_limit)
                    raise HTTPException(status_code=429, detail="TPM rate limit exceeded")

                cursor.execute(
                    """
                    UPDATE rate_windows
                    SET tenant_id = ?, project_id = ?, request_count = request_count + 1
                    WHERE agent = ?
                    """,
                    (tenant, project, agent),
                )
        else:
            cursor.execute(
                """
                INSERT INTO rate_windows (agent, tenant_id, project_id, window_start, request_count, tokens_count)
                VALUES (?, ?, ?, ?, 1, 0)
                """,
                (agent, tenant, project, now.isoformat()),
            )

        conn.commit()
