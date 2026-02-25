"""Rate limit checks with optional Redis backend and tenant/project-aware quota overrides."""

from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from ..db import get_db_connection
from .logging_config import StructuredLogger

logger = StructuredLogger(__name__)
_REDIS_CLIENT = None
_REDIS_INIT_ERROR: str | None = None
_REDIS_LOCK = threading.Lock()


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


def _record_rate_limit_event(*, tenant: str, project: str, agent: str, detail: str) -> None:
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO events (tenant_id, project_id, agent, action, cost_micro, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            (tenant, project, agent, "RATE_LIMIT", 0, detail),
        )
        conn.commit()


def _redis_client():
    url = (os.getenv("AEX_REDIS_URL") or "").strip()
    if not url:
        return None

    global _REDIS_CLIENT, _REDIS_INIT_ERROR
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    if _REDIS_INIT_ERROR is not None:
        return None

    with _REDIS_LOCK:
        if _REDIS_CLIENT is not None:
            return _REDIS_CLIENT
        if _REDIS_INIT_ERROR is not None:
            return None

        try:
            import redis

            client = redis.Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=1.5,
                socket_timeout=1.5,
            )
            client.ping()
            _REDIS_CLIENT = client
            logger.info("Redis rate-limit backend enabled")
            return _REDIS_CLIENT
        except Exception as exc:
            _REDIS_INIT_ERROR = str(exc)
            logger.warning(
                "Redis rate-limit backend unavailable; falling back to Postgres",
                error=str(exc),
            )
            return None


def _window_key_suffix(now_utc: datetime) -> str:
    return now_utc.strftime("%Y%m%d%H%M")


def _window_ttl_seconds(now_utc: datetime) -> int:
    next_window = now_utc.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return max(5, int((next_window - now_utc).total_seconds()) + 5)


def _check_rate_limit_redis(
    *,
    agent: str,
    tenant: str,
    project: str,
    rpm_limit: int,
    tpm_limit: int | None,
) -> bool:
    client = _redis_client()
    if client is None:
        return False

    now_utc = datetime.now(timezone.utc)
    suffix = _window_key_suffix(now_utc)
    ttl = _window_ttl_seconds(now_utc)
    req_key = f"aex:rate:req:{tenant}:{project}:{agent}:{suffix}"
    tok_key = f"aex:rate:tok:{tenant}:{project}:{agent}:{suffix}"

    try:
        req_count = int(client.incr(req_key))
        if req_count == 1:
            client.expire(req_key, ttl)

        if req_count > rpm_limit:
            _record_rate_limit_event(
                tenant=tenant,
                project=project,
                agent=agent,
                detail=f"RPM Limit: {rpm_limit} (redis)",
            )
            logger.warning(
                "RPM rate limit exceeded",
                agent=agent,
                tenant_id=tenant,
                project_id=project,
                limit=rpm_limit,
                backend="redis",
            )
            raise HTTPException(status_code=429, detail="RPM rate limit exceeded")

        # TPM uses a separate counter key; tokens are currently incremented at commit time only.
        if tpm_limit is not None:
            tok_count_raw = client.get(tok_key)
            tok_count = int(tok_count_raw or 0)
            if tok_count > int(tpm_limit):
                _record_rate_limit_event(
                    tenant=tenant,
                    project=project,
                    agent=agent,
                    detail=f"TPM Limit: {tpm_limit} (redis)",
                )
                logger.warning(
                    "TPM rate limit exceeded",
                    agent=agent,
                    tenant_id=tenant,
                    project_id=project,
                    limit=tpm_limit,
                    backend="redis",
                )
                raise HTTPException(status_code=429, detail="TPM rate limit exceeded")
        return True
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "Redis rate-limit check failed; falling back to Postgres",
            error=str(exc),
        )
        return False


def _check_rate_limit_postgres(
    *,
    agent: str,
    tenant: str,
    project: str,
    rpm_limit: int,
    tpm_limit: int | None,
) -> None:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

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


def check_rate_limit(agent: str, tenant_id: str | None = None, project_id: str | None = None):
    """Check RPM/TPM limits for an agent and current tenant/project scope."""
    tenant = (tenant_id or "default").strip() or "default"
    project = (project_id or "default").strip() or "default"

    with get_db_connection() as conn:
        cursor = conn.cursor()
        rpm_limit, tpm_limit = _resolve_limits(cursor, agent=agent, tenant_id=tenant, project_id=project)

    # Use Redis when configured; fallback to PostgreSQL semantics on error/unavailability.
    if _check_rate_limit_redis(
        agent=agent,
        tenant=tenant,
        project=project,
        rpm_limit=rpm_limit,
        tpm_limit=tpm_limit,
    ):
        return

    _check_rate_limit_postgres(
        agent=agent,
        tenant=tenant,
        project=project,
        rpm_limit=rpm_limit,
        tpm_limit=tpm_limit,
    )
