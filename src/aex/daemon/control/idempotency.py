"""Execution id and canonical request hashing."""

from __future__ import annotations

from typing import Any

from ..utils.deterministic import canonical_json, stable_hash_hex


IDEMPOTENCY_HEADER = "idempotency-key"
STEP_HEADER = "x-aex-step-id"


def canonical_request_hash(agent: str, endpoint: str, body: dict[str, Any], step_id: str = "") -> str:
    """Request hash used for deterministic replay and cache identity."""
    body_text = canonical_json(body)
    return stable_hash_hex(agent, endpoint, step_id, body_text)


def execution_id_for_request(
    *,
    agent: str,
    endpoint: str,
    body: dict[str, Any],
    idempotency_key: str | None,
    step_id: str | None,
    explicit_execution_id: str | None = None,
) -> tuple[str, str]:
    """Resolve execution_id and request_hash for an inbound request."""
    normalized_step = (step_id or "").strip()
    req_hash = canonical_request_hash(agent, endpoint, body, normalized_step)

    forced = (explicit_execution_id or "").strip()
    if forced:
        execution_id = forced
    elif idempotency_key:
        execution_id = stable_hash_hex(agent, endpoint, idempotency_key.strip())
    else:
        execution_id = req_hash

    return execution_id, req_hash
