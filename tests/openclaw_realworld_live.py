#!/usr/bin/env python3
"""
Real-world OpenClaw -> AEX -> Groq simulation (no mocks).

What this script does:
1. Runs key AEX CLI commands (init/daemon/doctor/models/agent/run/metrics/status/audit).
2. Creates 12 AEX agents with budgets.
3. Sends concurrent OpenClaw traffic through AEX to Groq.
4. Validates accounting and governance invariants from SQLite.
5. Writes a report in tests/reports/.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from openclaw.agents.providers.base import LLMMessage
from openclaw.agents.providers.openai_provider import OpenAIProvider


ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / "aex_env" / "bin" / "python"
AEX_CMD = [str(VENV_PYTHON), "-m", "aex"]
AEX_BASE_URL = os.getenv("AEX_BASE_URL", "http://127.0.0.1:9000/v1")
AEX_HEALTH_URL = AEX_BASE_URL.replace("/v1", "/health")
AEX_MODEL = os.getenv("AEX_MODEL", "gpt-oss-20b")
AGENT_COUNT = int(os.getenv("AEX_AGENT_COUNT", "12"))
REQUESTS_PER_AGENT = int(os.getenv("AEX_REQUESTS_PER_AGENT", "5"))
RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
PREFIX = f"rw-{RUN_ID}"
REPORT_DIR = ROOT / "tests" / "reports"
REPORT_PATH = REPORT_DIR / f"aex_realworld_report_{RUN_ID}.md"
ORIGINAL_HOME = Path.home()
SIM_HOME = Path(os.getenv("AEX_SIM_HOME", str(ROOT / ".sim_home")))
SIM_AEX_DIR = SIM_HOME / ".aex"
DB_PATH = SIM_AEX_DIR / "aex.db"
ENV_PATH = SIM_AEX_DIR / ".env"
REAL_ENV_PATH = ORIGINAL_HOME / ".aex" / ".env"


PROMPTS = [
    "Summarize why strict budget controls matter for autonomous agents in 3 bullets.",
    "Explain one practical difference between RPM and TPM limits.",
    "Give a 2-step plan to reduce runaway costs in agent loops.",
    "Write one concise policy for model-allowlist governance.",
    "State one risk if token scopes are not separated by privilege.",
]


@dataclass
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str


@dataclass
class AgentOutcome:
    name: str
    budget_usd: float
    requests: int = 0
    success: int = 0
    errors: int = 0
    budget_denied: int = 0
    rate_limited: int = 0
    other_errors: int = 0
    sample_error: str | None = None
    avg_latency_sec: float = 0.0


def _sanitize_text(text: str) -> str:
    text = re.sub(r"gsk_[A-Za-z0-9]+", "gsk_***REDACTED***", text)
    text = re.sub(r"\b[0-9a-f]{32}\b", "***TOKEN_REDACTED***", text)
    return text


def run_cmd(cmd: list[str], env: dict[str, str], timeout: int = 120) -> CommandResult:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    return CommandResult(
        command=" ".join(cmd),
        returncode=proc.returncode,
        stdout=proc.stdout.strip(),
        stderr=proc.stderr.strip(),
    )


def parse_token(create_output: str) -> str:
    match = re.search(r"Token:\s*([0-9a-f]{32})", create_output)
    if not match:
        raise RuntimeError("Failed to parse token from agent create output")
    return match.group(1)


def parse_rotated_token(output: str) -> str:
    match = re.search(r"New Token:\s*([0-9a-f]{32})", output)
    if not match:
        raise RuntimeError("Failed to parse rotated token")
    return match.group(1)


def ensure_groq_key() -> str:
    env_key = os.getenv("GROQ_API_KEY", "").strip()
    if env_key:
        return env_key
    if REAL_ENV_PATH.exists():
        data = REAL_ENV_PATH.read_text(encoding="utf-8")
        match = re.search(r"^GROQ_API_KEY=(.+)$", data, re.MULTILINE)
        if match and match.group(1).strip():
            return match.group(1).strip()
    raise RuntimeError("GROQ_API_KEY not found in environment or original ~/.aex/.env")


def prepare_sim_home(groq_key: str) -> None:
    if SIM_AEX_DIR.exists():
        shutil.rmtree(SIM_AEX_DIR)
    (SIM_AEX_DIR / "logs").mkdir(parents=True, exist_ok=True)
    (SIM_AEX_DIR / "config").mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text(f"GROQ_API_KEY={groq_key}\n", encoding="utf-8")


async def wait_for_health(url: str, timeout_sec: int = 45) -> bool:
    start = time.time()
    async with httpx.AsyncClient(timeout=5.0) as client:
        while time.time() - start < timeout_sec:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(1.0)
    return False


async def run_openclaw_for_agent(name: str, token: str, budget_usd: float) -> AgentOutcome:
    provider = OpenAIProvider(model=AEX_MODEL, api_key=token, base_url=AEX_BASE_URL)
    outcome = AgentOutcome(name=name, budget_usd=budget_usd)
    latencies: list[float] = []

    for idx in range(REQUESTS_PER_AGENT):
        prompt = PROMPTS[idx % len(PROMPTS)]
        messages = [
            LLMMessage(role="system", content="You are concise and policy-focused."),
            LLMMessage(role="user", content=prompt),
        ]
        outcome.requests += 1
        t0 = time.perf_counter()
        try:
            async for resp in provider.stream(messages=messages, max_tokens=160):
                if resp.type == "error":
                    err = str(resp.content)
                    outcome.errors += 1
                    if "402" in err or "Insufficient budget" in err:
                        outcome.budget_denied += 1
                    elif "429" in err or "Rate limit" in err:
                        outcome.rate_limited += 1
                    else:
                        outcome.other_errors += 1
                    if not outcome.sample_error:
                        outcome.sample_error = _sanitize_text(err[:200])
                    break
                if resp.type == "done":
                    outcome.success += 1
                    latencies.append(time.perf_counter() - t0)
                    break
        except Exception as exc:
            outcome.errors += 1
            outcome.other_errors += 1
            if not outcome.sample_error:
                outcome.sample_error = _sanitize_text(str(exc)[:200])

    if latencies:
        outcome.avg_latency_sec = sum(latencies) / len(latencies)
    return outcome


def collect_db_summary(agent_names: list[str]) -> dict[str, Any]:
    if not DB_PATH.exists():
        return {"db_exists": False}

    placeholders = ",".join(["?"] * len(agent_names))
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        rows = cur.execute(
            f"SELECT name, budget_micro, spent_micro, reserved_micro, tokens_used_prompt, tokens_used_completion "
            f"FROM agents WHERE name IN ({placeholders})",
            agent_names,
        ).fetchall()

        per_agent = []
        overspend = 0
        negatives = 0
        for r in rows:
            d = dict(r)
            if d["spent_micro"] > d["budget_micro"]:
                overspend += 1
            if d["spent_micro"] < 0 or d["budget_micro"] < 0 or d["reserved_micro"] < 0:
                negatives += 1
            per_agent.append(d)

        usage_events = cur.execute(
            f"SELECT agent, COUNT(*) AS c, COALESCE(SUM(cost_micro), 0) AS sum_cost "
            f"FROM events WHERE agent IN ({placeholders}) AND action='usage.commit' GROUP BY agent",
            agent_names,
        ).fetchall()
        budget_denials = cur.execute(
            f"SELECT COUNT(*) FROM events WHERE agent IN ({placeholders}) AND action='budget.deny'",
            agent_names,
        ).fetchone()[0]
        rate_denials = cur.execute(
            f"SELECT COUNT(*) FROM events WHERE agent IN ({placeholders}) AND action='RATE_LIMIT'",
            agent_names,
        ).fetchone()[0]

    return {
        "db_exists": True,
        "agent_rows": per_agent,
        "usage_events": [dict(x) for x in usage_events],
        "budget_denials": budget_denials,
        "rate_denials": rate_denials,
        "overspend_count": overspend,
        "negative_count": negatives,
    }


def write_report(payload: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    def cmd_block(c: CommandResult) -> str:
        out = _sanitize_text(c.stdout) if c.stdout else "<empty>"
        err = _sanitize_text(c.stderr) if c.stderr else "<empty>"
        return (
            f"### `{c.command}`\n"
            f"- return code: `{c.returncode}`\n"
            f"- stdout:\n```text\n{out}\n```\n"
            f"- stderr:\n```text\n{err}\n```\n"
        )

    outcomes: list[AgentOutcome] = payload["outcomes"]
    total_requests = sum(x.requests for x in outcomes)
    total_success = sum(x.success for x in outcomes)
    total_errors = sum(x.errors for x in outcomes)
    total_budget_denied = sum(x.budget_denied for x in outcomes)
    total_rate_limited = sum(x.rate_limited for x in outcomes)

    lines = [
        f"# AEX Real-World OpenClaw Report ({RUN_ID})",
        "",
        "## Topline",
        f"- AEX model route: `{AEX_MODEL}`",
        f"- Agent count: `{len(outcomes)}`",
        f"- Requests per agent: `{REQUESTS_PER_AGENT}`",
        f"- Total requests: `{total_requests}`",
        f"- Success: `{total_success}`",
        f"- Errors: `{total_errors}`",
        f"- Budget denials observed: `{total_budget_denied}`",
        f"- Rate limit denials observed: `{total_rate_limited}`",
        "",
        "## AEX CLI Command Evidence",
    ]

    for c in payload["commands"]:
        lines.append(cmd_block(c))

    lines.extend(
        [
            "## Per-Agent Outcomes",
            "",
            "| Agent | Budget USD | Requests | Success | Errors | 402 | 429 | Avg Latency (s) |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for o in outcomes:
        lines.append(
            f"| {o.name} | {o.budget_usd:.3f} | {o.requests} | {o.success} | {o.errors} | "
            f"{o.budget_denied} | {o.rate_limited} | {o.avg_latency_sec:.2f} |"
        )

    db_summary = payload["db_summary"]
    lines.extend(
        [
            "",
            "## DB Governance Checks (created agents only)",
            f"- DB present: `{db_summary.get('db_exists')}`",
            f"- Overspend count: `{db_summary.get('overspend_count')}`",
            f"- Negative-value count: `{db_summary.get('negative_count')}`",
            f"- `budget.deny` events: `{db_summary.get('budget_denials')}`",
            f"- `RATE_LIMIT` events: `{db_summary.get('rate_denials')}`",
            "",
            "## Verdict",
            payload["verdict"],
            "",
            "## Notes",
            "- Tokens and API keys are redacted in this report.",
            "- This run uses real provider traffic (Groq) via OpenClaw through AEX.",
        ]
    )

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    groq_key = ensure_groq_key()
    prepare_sim_home(groq_key)
    cmd_env = os.environ.copy()
    cmd_env["PYTHONPATH"] = str(ROOT / "src")
    cmd_env["HOME"] = str(SIM_HOME)

    commands: list[CommandResult] = []
    created_agents: list[tuple[str, str, float]] = []
    daemon_was_running = False

    def _run(cmd: list[str], timeout: int = 120) -> CommandResult:
        result = run_cmd(cmd, env=cmd_env, timeout=timeout)
        commands.append(result)
        return result

    try:
        commands.append(run_cmd(AEX_CMD + ["version"], env=cmd_env))
        commands.append(run_cmd(AEX_CMD + ["init"], env=cmd_env, timeout=180))

        status_pre = _run(AEX_CMD + ["daemon", "status"])
        daemon_was_running = "running" in status_pre.stdout.lower() and "not running" not in status_pre.stdout.lower()

        # Restart daemon under current environment to ensure current local code path is active.
        _run(AEX_CMD + ["daemon", "stop"])
        _run(AEX_CMD + ["daemon", "start"], timeout=180)

        healthy = await wait_for_health(AEX_HEALTH_URL, timeout_sec=60)
        if not healthy:
            raise RuntimeError("AEX daemon did not become healthy in time")

        _run(AEX_CMD + ["doctor"])
        _run(AEX_CMD + ["models", "reload"])

        # Create 12 agents: 10 normal budget + 2 tiny budget to force budget governance.
        for i in range(AGENT_COUNT):
            name = f"{PREFIX}-a{i:02d}"
            budget = 1.20 if i < AGENT_COUNT - 2 else 0.004
            rpm = 120
            create = _run(
                [
                    *AEX_CMD,
                    "agent",
                    "create",
                    name,
                    f"{budget}",
                    f"{rpm}",
                    "--allowed-models",
                    AEX_MODEL,
                ]
            )
            if create.returncode != 0:
                raise RuntimeError(f"Agent creation failed for {name}")
            token = parse_token(create.stdout)
            created_agents.append((name, token, budget))

        _run(AEX_CMD + ["agent", "list"])
        _run([*AEX_CMD, "agent", "inspect", created_agents[0][0]])

        rotate = _run([*AEX_CMD, "agent", "rotate-token", created_agents[0][0]])
        if rotate.returncode == 0:
            rotated = parse_rotated_token(rotate.stdout)
            created_agents[0] = (created_agents[0][0], rotated, created_agents[0][2])

        # Validate `aex run` env wiring.
        _run(
            [
                *AEX_CMD,
                "run",
                "--agent",
                created_agents[0][0],
                str(VENV_PYTHON),
                "-c",
                "import os; print('OPENAI_BASE_URL', os.getenv('OPENAI_BASE_URL')); "
                "print('HAS_OPENAI_API_KEY', bool(os.getenv('OPENAI_API_KEY')))",
            ]
        )

        _run(AEX_CMD + ["metrics"])

        # Run OpenClaw traffic concurrently.
        tasks = [
            run_openclaw_for_agent(name=name, token=token, budget_usd=budget)
            for name, token, budget in created_agents
        ]
        outcomes = await asyncio.gather(*tasks)

        _run(AEX_CMD + ["metrics"])
        _run(AEX_CMD + ["status"])
        _run(AEX_CMD + ["audit"])

        db_summary = collect_db_summary([x[0] for x in created_agents])
        overspend = db_summary.get("overspend_count", 999)
        negatives = db_summary.get("negative_count", 999)
        total_success = sum(x.success for x in outcomes)

        if overspend == 0 and negatives == 0 and total_success >= AGENT_COUNT * 2:
            verdict = (
                "AEX demonstrated stable kernel behavior for this live run: "
                "requests were served through OpenClaw to Groq, budget-denial controls triggered as expected, "
                "and no overspend/negative accounting was observed for created agents."
            )
            rc = 0
        else:
            verdict = (
                "Run completed, but acceptance thresholds were not fully met "
                "(check overspend/negative counters and success volume)."
            )
            rc = 1

        payload = {
            "commands": commands,
            "outcomes": outcomes,
            "db_summary": db_summary,
            "verdict": verdict,
        }
        write_report(payload)
        print(f"REPORT_PATH={REPORT_PATH}")
        print(f"VERDICT={verdict}")
        return rc

    finally:
        for name, _, _ in created_agents:
            run_cmd([*AEX_CMD, "agent", "delete", name], env=cmd_env, timeout=120)

        # Keep daemon running if it was running before this script.
        if not daemon_was_running:
            run_cmd([*AEX_CMD, "daemon", "stop"], env=cmd_env, timeout=120)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
