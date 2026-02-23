"""Tenant/project scope helpers for v2.1 APIs and proxy isolation."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

from ..db.schema import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID


@dataclass(frozen=True)
class ScopeContext:
    tenant_id: str
    project_id: str


def _normalize(value: str | None, fallback: str) -> str:
    raw = (value or "").strip()
    return raw or fallback


def resolve_scope(headers: dict[str, str], agent_info: dict) -> ScopeContext:
    """Resolve effective tenant/project from request headers and agent assignment.

    Rules:
    - agent assignment is authoritative isolation boundary.
    - missing headers inherit agent scope.
    - explicit headers must match agent scope.
    """
    agent_tenant = _normalize(agent_info.get("tenant_id"), DEFAULT_TENANT_ID)
    agent_project = _normalize(agent_info.get("project_id"), DEFAULT_PROJECT_ID)

    tenant_header = _normalize(headers.get("x-aex-tenant-id"), agent_tenant)
    project_header = _normalize(headers.get("x-aex-project-id"), agent_project)

    if tenant_header != agent_tenant:
        raise HTTPException(status_code=403, detail="Tenant scope mismatch for authenticated agent token")
    if project_header != agent_project:
        raise HTTPException(status_code=403, detail="Project scope mismatch for authenticated agent token")

    return ScopeContext(tenant_id=tenant_header, project_id=project_header)
