"""Deterministic replay helpers for audit mode."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..db import get_db_connection
from ..utils.deterministic import stable_hash_hex


@dataclass
class ReplayResult:
    ok: bool
    detail: str
    expected: Any | None = None
    observed: Any | None = None


def verify_hash_chain() -> ReplayResult:
    """Verify event_log hash chain integrity end-to-end."""
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT seq, chain_partition, execution_id, event_type, payload_json, prev_hash, event_hash
            FROM event_log
            ORDER BY chain_partition ASC, seq ASC
            """
        ).fetchall()

    prev_by_partition: dict[str, str] = {}
    for row in rows:
        partition = row["chain_partition"] or "default"
        prev = prev_by_partition.get(partition, "GENESIS")
        expected = stable_hash_hex(prev, row["event_type"], row["execution_id"] or "", row["payload_json"])
        if row["prev_hash"] != prev:
            return ReplayResult(
                ok=False,
                detail=f"prev_hash mismatch at partition={partition} seq={row['seq']}",
                expected=prev,
                observed=row["prev_hash"],
            )
        if row["event_hash"] != expected:
            return ReplayResult(
                ok=False,
                detail=f"event_hash mismatch at partition={partition} seq={row['seq']}",
                expected=expected,
                observed=row["event_hash"],
            )
        prev_by_partition[partition] = row["event_hash"]

    return ReplayResult(ok=True, detail=f"hash chain verified for {len(rows)} events")


def replay_ledger_balances() -> ReplayResult:
    """Replay spend/reservation deltas and compare against materialized agent account counters."""
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT agent, event_type, payload_json FROM event_log ORDER BY seq ASC"
        ).fetchall()
        agents = conn.execute(
            "SELECT name, spent_micro, reserved_micro FROM agents"
        ).fetchall()

    replayed = {}
    for row in rows:
        agent = row["agent"]
        if not agent:
            continue
        state = replayed.setdefault(agent, {"spent_micro": 0, "reserved_micro": 0})

        payload = json.loads(row["payload_json"])
        if row["event_type"] == "budget.reserve":
            state["reserved_micro"] += int(payload.get("estimated_micro", 0))
        elif row["event_type"] == "usage.commit":
            state["spent_micro"] += int(payload.get("cost_micro", 0))
            # On commit reservation is released by estimated amount. If it is missing, clamp to zero.
            est = int(payload.get("estimated_micro", 0))
            if est > 0:
                state["reserved_micro"] = max(0, state["reserved_micro"] - est)
        elif row["event_type"] == "reservation.release":
            est = int(payload.get("estimated_micro", 0))
            if est > 0:
                state["reserved_micro"] = max(0, state["reserved_micro"] - est)

    mismatches = []
    for agent in agents:
        live = {
            "spent_micro": int(agent["spent_micro"]),
            "reserved_micro": int(agent["reserved_micro"]),
        }
        rep = replayed.get(agent["name"], {"spent_micro": 0, "reserved_micro": 0})
        if rep["spent_micro"] != live["spent_micro"]:
            mismatches.append(
                f"{agent['name']}: spent replay={rep['spent_micro']} live={live['spent_micro']}"
            )
        if rep["reserved_micro"] != live["reserved_micro"]:
            mismatches.append(
                f"{agent['name']}: reserved replay={rep['reserved_micro']} live={live['reserved_micro']}"
            )

    if mismatches:
        return ReplayResult(ok=False, detail="; ".join(mismatches[:10]))
    return ReplayResult(ok=True, detail="ledger replay matches spent counters")
