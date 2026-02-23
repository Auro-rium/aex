#!/usr/bin/env python3
"""Local stress/recovery/invariant verification for AEX Postgres settlement."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, UTC
import os
import random
import secrets
import traceback

from aex.daemon.db import get_db_connection, init_db
from aex.daemon.ledger import (
    commit_execution_usage,
    mark_execution_dispatched,
    release_execution_reservation,
    reserve_budget_v2,
)
from aex.daemon.runtime import reconcile_incomplete_executions
from aex.daemon.utils.invariants import run_all_checks


def _ensure_seed_agents(prefix: str, count: int, budget_micro: int, rpm: int) -> list[str]:
    agents = [f"{prefix}-{i+1}" for i in range(count)]
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO tenants (tenant_id, name, slug, status)
            VALUES ('default', 'Default Tenant', 'default', 'ACTIVE')
            ON CONFLICT(tenant_id) DO NOTHING
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, tenant_id, name, slug, status)
            VALUES ('default', 'default', 'Default Project', 'default', 'ACTIVE')
            ON CONFLICT(project_id) DO NOTHING
            """
        )
        for agent in agents:
            token = secrets.token_hex(16)
            conn.execute(
                """
                INSERT INTO agents (name, tenant_id, project_id, api_token, budget_micro, rpm_limit)
                VALUES (?, 'default', 'default', ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    budget_micro = excluded.budget_micro,
                    rpm_limit = excluded.rpm_limit,
                    spent_micro = 0,
                    reserved_micro = 0,
                    tokens_used_prompt = 0,
                    tokens_used_completion = 0,
                    last_activity = CURRENT_TIMESTAMP
                """,
                (agent, token, budget_micro, rpm),
            )
        conn.commit()
    return agents


def _reserve_and_settle(agent: str, idx: int, force_release_ratio: float) -> tuple[bool, str]:
    execution_id = f"stress-{agent}-{idx}-{secrets.token_hex(8)}"
    estimated = random.randint(500, 2500)
    try:
        reserve_budget_v2(
            agent=agent,
            execution_id=execution_id,
            endpoint="/v1/chat/completions",
            request_hash=execution_id,
            estimated_cost_micro=estimated,
            reservation_ttl_seconds=2,
        )
        mark_execution_dispatched(execution_id)
        if random.random() < force_release_ratio:
            release_execution_reservation(
                agent=agent,
                execution_id=execution_id,
                estimated_cost_micro=estimated,
                reason="stress synthetic release",
                status_code=502,
            )
        else:
            actual = random.randint(max(1, estimated // 3), estimated + 1200)
            commit_execution_usage(
                agent=agent,
                execution_id=execution_id,
                estimated_cost_micro=estimated,
                actual_cost_micro=actual,
                prompt_tokens=random.randint(5, 70),
                completion_tokens=random.randint(5, 120),
                model_name="gpt-oss-20b",
                response_body={"id": execution_id, "ok": True},
                status_code=200,
            )
        return True, execution_id
    except Exception:
        return False, traceback.format_exc(limit=1)


def _inject_stale_reservations(agent: str, count: int) -> int:
    created = 0
    for i in range(count):
        execution_id = f"stale-{agent}-{i}-{secrets.token_hex(6)}"
        estimated = random.randint(1000, 2000)
        try:
            reserve_budget_v2(
                agent=agent,
                execution_id=execution_id,
                endpoint="/v1/chat/completions",
                request_hash=execution_id,
                estimated_cost_micro=estimated,
                reservation_ttl_seconds=1,
            )
            created += 1
        except Exception:
            pass
    return created


def _summarize(prefix: str) -> dict:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT
              COUNT(*) FILTER (WHERE state='COMMITTED') AS committed,
              COUNT(*) FILTER (WHERE state='RELEASED') AS released,
              COUNT(*) FILTER (WHERE state='DENIED') AS denied,
              COUNT(*) FILTER (WHERE state NOT IN ('COMMITTED','DENIED','RELEASED','FAILED')) AS non_terminal
            FROM executions
            WHERE agent LIKE ?
            """,
            (f"{prefix}%",),
        ).fetchone()
        agent_reserved = conn.execute(
            "SELECT COALESCE(SUM(reserved_micro),0) AS v FROM agents WHERE name LIKE ?",
            (f"{prefix}%",),
        ).fetchone()
        checks = run_all_checks(conn)
    return {
        "committed": int(row["committed"] or 0),
        "released": int(row["released"] or 0),
        "denied": int(row["denied"] or 0),
        "non_terminal": int(row["non_terminal"] or 0),
        "reserved_micro_sum": int(agent_reserved["v"] or 0),
        "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Stress-check settlement + recovery + invariants.")
    parser.add_argument("--agent-prefix", default="stressv21")
    parser.add_argument("--agents", type=int, default=8)
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--release-ratio", type=float, default=0.35)
    parser.add_argument("--stale", type=int, default=20, help="Extra stale reservations to inject.")
    parser.add_argument("--budget-usd", type=float, default=50.0)
    parser.add_argument("--rpm", type=int, default=1200)
    args = parser.parse_args()

    if not (os.getenv("AEX_PG_DSN") or "").strip():
        raise SystemExit("AEX_PG_DSN is required.")

    random.seed(21)
    init_db()
    agents = _ensure_seed_agents(
        args.agent_prefix,
        args.agents,
        budget_micro=int(args.budget_usd * 1_000_000),
        rpm=args.rpm,
    )

    failures = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = []
        for i in range(args.iterations):
            agent = agents[i % len(agents)]
            futures.append(ex.submit(_reserve_and_settle, agent, i, args.release_ratio))
        for fut in as_completed(futures):
            ok, _ = fut.result()
            if not ok:
                failures += 1

    injected = _inject_stale_reservations(agents[0], args.stale)

    # Let stale reservations expire, then recover.
    import time

    time.sleep(2.2)
    recovery = reconcile_incomplete_executions()

    summary = _summarize(args.agent_prefix)
    failed_checks = [c for c in summary["checks"] if not c["passed"]]

    print("stress_summary", summary)
    print("recovery_summary", recovery)
    print("injected_stale", injected)
    print("failed_operations", failures)

    if failures > 0:
        print("RESULT=FAIL (operation failures)")
        return 1
    if summary["non_terminal"] > 0:
        print("RESULT=FAIL (non-terminal executions remain)")
        return 1
    if summary["reserved_micro_sum"] != 0:
        print("RESULT=FAIL (reserved_micro leak)")
        return 1
    if failed_checks:
        print("RESULT=FAIL (invariant failures)")
        return 1

    print(f"RESULT=PASS ts={datetime.now(UTC).isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
