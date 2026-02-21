"""Hash-chained ledger event appends."""

from __future__ import annotations

import json
from typing import Any

from ..utils.deterministic import canonical_json, stable_hash_hex

GENESIS_HASH = "GENESIS"


def _payload_text(payload: dict[str, Any]) -> str:
    return canonical_json(payload)


def append_hash_event(
    conn,
    *,
    execution_id: str | None,
    agent: str | None,
    event_type: str,
    payload: dict[str, Any],
):
    """Append an event to hash-chained event_log.

    Must be called inside an existing transaction.
    """
    payload_json = _payload_text(payload)
    last = conn.execute(
        "SELECT event_hash FROM event_log ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    prev_hash = last["event_hash"] if last else GENESIS_HASH
    event_hash = stable_hash_hex(prev_hash, event_type, execution_id or "", payload_json)

    conn.execute(
        """
        INSERT INTO event_log (execution_id, agent, event_type, payload_json, prev_hash, event_hash)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (execution_id, agent, event_type, payload_json, prev_hash, event_hash),
    )


def append_compat_event(
    conn,
    *,
    agent: str | None,
    action: str,
    cost_micro: int = 0,
    metadata: Any = None,
):
    """Append legacy event row for backward-compatible CLI metrics."""
    metadata_text = None
    if metadata is not None:
        metadata_text = metadata if isinstance(metadata, str) else json.dumps(metadata, ensure_ascii=True)

    conn.execute(
        "INSERT INTO events (agent, action, cost_micro, metadata) VALUES (?, ?, ?, ?)",
        (agent, action, cost_micro, metadata_text),
    )
