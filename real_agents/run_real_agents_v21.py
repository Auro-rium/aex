#!/usr/bin/env python3
"""AEX v2.1 real-framework stress harness.

Runs real agentic frameworks against AEX OpenAI-compatible endpoints so AEX
controls admission, budgeting, policy, ledger, and settlement.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import random
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


TASKS = [
    "Summarize this architecture tradeoff in 3 bullets: deterministic gating vs raw model speed.",
    "Generate a short risk register for distributed budget reservation.",
    "Write a compact incident note for a transient provider timeout.",
    "Propose one policy rule to prevent tool abuse and explain why.",
    "Produce a 5-line runbook snippet for replay verification.",
    "Give a brief postmortem skeleton for partial commit failure.",
    "Create two test ideas for rate-limit and quota regressions.",
    "Explain idempotency key strategy in under 90 words.",
    "Draft one migration guard for schema version drift.",
    "Give an operator checklist before enabling passthrough mode.",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def aex_root(base_url: str) -> str:
    b = base_url.rstrip("/")
    if b.endswith("/openai/v1"):
        return b[: -len("/openai/v1")]
    if b.endswith("/v1"):
        return b[: -len("/v1")]
    return b


def compact_error(exc: Exception, limit: int = 240) -> str:
    text = f"{exc.__class__.__name__}: {exc}"
    return text[:limit]


def status_code_from_exc(exc: Exception) -> int | None:
    for attr in ("status_code", "http_status"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    resp = getattr(exc, "response", None)
    if resp is not None:
        val = getattr(resp, "status_code", None)
        if isinstance(val, int):
            return val
    return None


def textify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text", part)))
            else:
                parts.append(str(part))
        return " ".join(parts).strip()
    return str(content)


def parse_frameworks(raw: str) -> list[str]:
    vals = [v.strip().lower() for v in raw.split(",") if v.strip()]
    out: list[str] = []
    for v in vals:
        if v not in out:
            out.append(v)
    return out


def require_module(module_name: str, framework: str, strict: bool) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except Exception as exc:
        if strict:
            raise RuntimeError(f"{framework} requires module '{module_name}': {exc}") from exc
        print(f"[skip] framework={framework} module={module_name} reason={compact_error(exc)}")
        return False


def require_psycopg() -> None:
    try:
        import psycopg  # noqa: F401
    except Exception as exc:
        raise RuntimeError("psycopg is required. Install: pip install \"psycopg[binary]>=3.2\"") from exc


def get_agent_token(dsn: str, agent_name: str) -> str | None:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT api_token FROM agents WHERE name = %s",
            (agent_name,),
        ).fetchone()
        if not row:
            return None
        return str(row["api_token"])


def ensure_agent_exists(
    *,
    dsn: str,
    agent_name: str,
    budget_usd: float,
    rpm: int,
    tenant_id: str,
    project_id: str,
    create_missing: bool,
) -> str:
    token = get_agent_token(dsn, agent_name)
    if token:
        return token
    if not create_missing:
        raise RuntimeError(
            f"Agent '{agent_name}' missing. Re-run with --create-missing or create it via `aex agent create`."
        )

    cmd = [
        "aex",
        "agent",
        "create",
        agent_name,
        str(budget_usd),
        str(rpm),
        "--tenant-id",
        tenant_id,
        "--project-id",
        project_id,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Failed to create agent '{agent_name}'.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )

    token = get_agent_token(dsn, agent_name)
    if not token:
        raise RuntimeError(f"Agent '{agent_name}' created but token lookup failed.")
    return token


class Adapter:
    def __init__(self, name: str, run_prompt: Callable[[str], dict[str, Any]]):
        self.name = name
        self._run_prompt = run_prompt

    def run(self, prompt: str) -> dict[str, Any]:
        return self._run_prompt(prompt)


def make_openai_adapter(base_url: str, model: str, token: str) -> Adapter:
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=token, timeout=60)

    def _run(prompt: str) -> dict[str, Any]:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Be concise and technical."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=240,
        )
        text = (resp.choices[0].message.content or "").strip()
        usage = getattr(resp, "usage", None)
        return {

            
            "text": text,
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        }

    return Adapter("openai", _run)


def make_instructor_adapter(base_url: str, model: str, token: str) -> Adapter:
    import instructor
    from openai import OpenAI
    from pydantic import BaseModel, Field

    class StructuredOut(BaseModel):
        result: str = Field(description="Short technical response.")

    client = instructor.from_openai(OpenAI(base_url=base_url, api_key=token, timeout=60))

    def _run(prompt: str) -> dict[str, Any]:
        data = client.chat.completions.create(
            model=model,
            response_model=StructuredOut,
            messages=[
                {"role": "system", "content": "Return compact, technical text."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=180,
        )
        return {"text": data.result}

    return Adapter("instructor", _run)


def _make_chat_openai(model: str, token: str, base_url: str):
    from langchain_openai import ChatOpenAI

    variants = [
        {"model": model, "api_key": token, "base_url": base_url, "temperature": 0.2, "max_tokens": 180},
        {"model": model, "api_key": token, "openai_api_base": base_url, "temperature": 0.2, "max_tokens": 180},
        {"model_name": model, "api_key": token, "base_url": base_url, "temperature": 0.2, "max_tokens": 180},
        {"model_name": model, "openai_api_key": token, "openai_api_base": base_url, "temperature": 0.2, "max_tokens": 180},
    ]
    last_exc: Exception | None = None
    for kwargs in variants:
        try:
            return ChatOpenAI(**kwargs)
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(f"Unable to initialize ChatOpenAI adapter: {last_exc}")


def make_langchain_adapter(base_url: str, model: str, token: str) -> Adapter:
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = _make_chat_openai(model, token, base_url)

    def _run(prompt: str) -> dict[str, Any]:
        msg = llm.invoke(
            [
                SystemMessage(content="Respond with concise technical output."),
                HumanMessage(content=prompt),
            ]
        )
        usage = getattr(msg, "usage_metadata", {}) or {}
        return {
            "text": textify_content(msg.content),
            "prompt_tokens": int(usage.get("input_tokens", 0) or 0),
            "completion_tokens": int(usage.get("output_tokens", 0) or 0),
        }

    return Adapter("langchain", _run)


def make_langgraph_adapter(base_url: str, model: str, token: str) -> Adapter:
    from langchain_core.messages import HumanMessage
    from langgraph.graph import END, START, StateGraph

    llm = _make_chat_openai(model, token, base_url)

    def llm_node(state: dict[str, Any]) -> dict[str, Any]:
        msg = llm.invoke([HumanMessage(content=state["prompt"])])
        return {"answer": textify_content(msg.content)}

    graph_builder = StateGraph(dict)
    graph_builder.add_node("ask_model", llm_node)
    graph_builder.add_edge(START, "ask_model")
    graph_builder.add_edge("ask_model", END)
    graph = graph_builder.compile()

    def _run(prompt: str) -> dict[str, Any]:
        out = graph.invoke({"prompt": prompt})
        return {"text": str(out.get("answer", "")).strip()}

    return Adapter("langgraph", _run)


def make_llama_index_adapter(base_url: str, model: str, token: str) -> Adapter:
    try:
        from llama_index.llms.openai import OpenAI as LlamaOpenAI
    except Exception as exc:
        raise RuntimeError(
            "LlamaIndex OpenAI module unavailable. Install llama-index and openai extras."
        ) from exc

    llm = LlamaOpenAI(model=model, api_key=token, api_base=base_url, temperature=0.2, max_tokens=180)

    def _run(prompt: str) -> dict[str, Any]:
        result = llm.complete(prompt)
        text = getattr(result, "text", None)
        return {"text": (text or str(result)).strip()}

    return Adapter("llama_index", _run)


def make_haystack_adapter(base_url: str, model: str, token: str) -> Adapter:
    try:
        from haystack.components.generators.openai import OpenAIGenerator
    except Exception:
        from haystack.components.generators.chat.openai import OpenAIChatGenerator as OpenAIGenerator
    from haystack.utils import Secret

    generator = OpenAIGenerator(
        model=model,
        api_key=Secret.from_token(token),
        api_base_url=base_url,
    )

    def _run(prompt: str) -> dict[str, Any]:
        res = generator.run(prompt=prompt)
        replies = res.get("replies") or []
        text = replies[0] if replies else str(res)
        return {"text": textify_content(text)}

    return Adapter("haystack", _run)


def make_dspy_adapter(base_url: str, model: str, token: str) -> Adapter:
    import dspy

    try:
        lm = dspy.LM(model=f"openai/{model}", api_key=token, api_base=base_url, temperature=0.2, max_tokens=180)
    except Exception:
        lm = dspy.LM(f"openai/{model}", api_key=token, api_base=base_url)
    dspy.configure(lm=lm)

    def _run(prompt: str) -> dict[str, Any]:
        dspy.configure(lm=lm)
        try:
            out = lm(prompt)
            return {"text": textify_content(out)}
        except Exception:
            predictor = dspy.Predict("question -> answer")
            pred = predictor(question=prompt)
            return {"text": textify_content(getattr(pred, "answer", pred))}

    return Adapter("dspy", _run)


def make_pydantic_ai_adapter(base_url: str, model: str, token: str) -> Adapter:
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIModel

    model_obj = OpenAIModel(model_name=model, base_url=base_url, api_key=token)
    agent = Agent(model_obj, system_prompt="Return concise technical output.")

    def _run(prompt: str) -> dict[str, Any]:
        result = agent.run_sync(prompt)
        text = getattr(result, "output", None)
        if text is None:
            text = str(result)
        return {"text": str(text).strip()}

    return Adapter("pydantic_ai", _run)


def make_smolagents_adapter(base_url: str, model: str, token: str) -> Adapter:
    from smolagents import CodeAgent, OpenAIServerModel

    server_model = OpenAIServerModel(model_id=model, api_base=base_url, api_key=token)
    try:
        agent = CodeAgent(tools=[], model=server_model, add_base_tools=False)
    except Exception:
        agent = CodeAgent(tools=[], model=server_model)

    def _run(prompt: str) -> dict[str, Any]:
        out = agent.run(prompt)
        return {"text": textify_content(out)}

    return Adapter("smolagents", _run)


def make_agno_adapter(base_url: str, model: str, token: str) -> Adapter:
    from agno.agent import Agent
    from agno.models.openai import OpenAIChat

    ag_model = OpenAIChat(id=model, api_key=token, base_url=base_url)
    agent = Agent(model=ag_model, markdown=False)

    def _run(prompt: str) -> dict[str, Any]:
        out = agent.run(prompt)
        text = getattr(out, "content", None) or getattr(out, "text", None) or str(out)
        return {"text": str(text).strip()}

    return Adapter("agno", _run)


ADAPTER_BUILDERS: dict[str, Callable[[str, str, str], Adapter]] = {
    "openai": make_openai_adapter,
    "instructor": make_instructor_adapter,
    "langchain": make_langchain_adapter,
    "langgraph": make_langgraph_adapter,
    "llama_index": make_llama_index_adapter,
    "haystack": make_haystack_adapter,
    "dspy": make_dspy_adapter,
    "pydantic_ai": make_pydantic_ai_adapter,
    "smolagents": make_smolagents_adapter,
    "agno": make_agno_adapter,
}

MODULE_CHECKS: dict[str, str] = {
    "openai": "openai",
    "instructor": "instructor",
    "langchain": "langchain_openai",
    "langgraph": "langgraph",
    "llama_index": "llama_index",
    "haystack": "haystack",
    "dspy": "dspy",
    "pydantic_ai": "pydantic_ai",
    "smolagents": "smolagents",
    "agno": "agno",
}


@dataclass
class WorkerSpec:
    agent_name: str
    framework: str
    token: str
    runtime_seconds: float
    sleep_min: float
    sleep_max: float


@dataclass
class WorkerStats:
    agent_name: str
    framework: str
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    denied: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_latency_sec: float = 0.0
    first_started_at: str | None = None
    last_completed_at: str | None = None
    last_error: str | None = None
    samples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["avg_latency_sec"] = (
            (self.total_latency_sec / self.attempts) if self.attempts else 0.0
        )
        return out


def build_prompt(agent_name: str, framework: str, step: int) -> str:
    task = random.choice(TASKS)
    return (
        f"[agent={agent_name}] [framework={framework}] [step={step}] "
        f"{task} Include one deterministic check statement."
    )


async def run_one_call(spec: WorkerSpec, adapter: Adapter, stats: WorkerStats, step: int) -> None:
    prompt = build_prompt(spec.agent_name, spec.framework, step)
    stats.attempts += 1
    if stats.first_started_at is None:
        stats.first_started_at = now_iso()
    t0 = time.perf_counter()
    try:
        result = await asyncio.to_thread(adapter.run, prompt)
        elapsed = time.perf_counter() - t0
        stats.total_latency_sec += elapsed
        stats.successes += 1
        stats.prompt_tokens += int(result.get("prompt_tokens", 0) or 0)
        stats.completion_tokens += int(result.get("completion_tokens", 0) or 0)
        text = str(result.get("text", "")).strip()
        if text and len(stats.samples) < 3:
            stats.samples.append(text[:180])
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        stats.total_latency_sec += elapsed
        stats.failures += 1
        code = status_code_from_exc(exc)
        if code in (402, 403, 423, 429):
            stats.denied += 1
        stats.last_error = compact_error(exc)
    finally:
        stats.last_completed_at = now_iso()


async def run_sequential_phase(
    workers: list[tuple[WorkerSpec, Adapter, WorkerStats]],
    rounds: int,
) -> None:
    print(f"[phase] sequential rounds={rounds}")
    step = 0
    for _ in range(rounds):
        for spec, adapter, stats in workers:
            step += 1
            await run_one_call(spec, adapter, stats, step)


async def run_parallel_phase(
    workers: list[tuple[WorkerSpec, Adapter, WorkerStats]],
) -> None:
    print(f"[phase] parallel workers={len(workers)}")

    async def _worker_loop(spec: WorkerSpec, adapter: Adapter, stats: WorkerStats) -> None:
        deadline = time.monotonic() + spec.runtime_seconds
        step = 0
        while time.monotonic() < deadline:
            step += 1
            await run_one_call(spec, adapter, stats, step)
            await asyncio.sleep(random.uniform(spec.sleep_min, spec.sleep_max))

    await asyncio.gather(*[_worker_loop(spec, adapter, stats) for spec, adapter, stats in workers])


async def run_burst_phase(
    workers: list[tuple[WorkerSpec, Adapter, WorkerStats]],
    rounds: int,
    width: int,
    pause_sec: float,
) -> None:
    print(f"[phase] burst rounds={rounds} width={width}")
    step = 0
    for _ in range(rounds):
        pick = random.sample(workers, k=min(width, len(workers)))
        tasks = []
        for spec, adapter, stats in pick:
            step += 1
            tasks.append(run_one_call(spec, adapter, stats, step))
        await asyncio.gather(*tasks)
        await asyncio.sleep(pause_sec)


async def fetch_aex_snapshot(base_url: str) -> dict[str, Any]:
    import httpx

    root = aex_root(base_url)
    out: dict[str, Any] = {"root": root, "health": None, "metrics": None, "replay": None}
    async with httpx.AsyncClient(timeout=30) as client:
        for path, key in (
            ("/health", "health"),
            ("/metrics", "metrics"),
            ("/admin/replay", "replay"),
        ):
            try:
                resp = await client.get(f"{root}{path}")
                out[key] = {
                    "status_code": resp.status_code,
                    "json": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else None,
                }
            except Exception as exc:
                out[key] = {"error": compact_error(exc)}
    return out


def summarize(workers: list[tuple[WorkerSpec, Adapter, WorkerStats]]) -> dict[str, Any]:
    per_framework: dict[str, dict[str, float]] = {}
    for _, _, st in workers:
        bucket = per_framework.setdefault(
            st.framework,
            {
                "attempts": 0,
                "successes": 0,
                "failures": 0,
                "denied": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "latency_sec": 0.0,
            },
        )
        bucket["attempts"] += st.attempts
        bucket["successes"] += st.successes
        bucket["failures"] += st.failures
        bucket["denied"] += st.denied
        bucket["prompt_tokens"] += st.prompt_tokens
        bucket["completion_tokens"] += st.completion_tokens
        bucket["latency_sec"] += st.total_latency_sec

    totals = {
        "attempts": sum(st.attempts for _, _, st in workers),
        "successes": sum(st.successes for _, _, st in workers),
        "failures": sum(st.failures for _, _, st in workers),
        "denied": sum(st.denied for _, _, st in workers),
        "prompt_tokens": sum(st.prompt_tokens for _, _, st in workers),
        "completion_tokens": sum(st.completion_tokens for _, _, st in workers),
        "latency_sec": sum(st.total_latency_sec for _, _, st in workers),
    }
    return {"totals": totals, "per_framework": per_framework}


def print_summary(summary: dict[str, Any]) -> None:
    totals = summary["totals"]
    print("\n=== Stress Summary ===")
    print(
        "totals: "
        f"attempts={totals['attempts']} success={totals['successes']} "
        f"fail={totals['failures']} denied={totals['denied']} "
        f"prompt_tokens={totals['prompt_tokens']} completion_tokens={totals['completion_tokens']}"
    )
    print("by_framework:")
    for framework, row in sorted(summary["per_framework"].items()):
        avg = (row["latency_sec"] / row["attempts"]) if row["attempts"] else 0.0
        print(
            f"  - {framework:12s} attempts={int(row['attempts']):5d} "
            f"success={int(row['successes']):5d} fail={int(row['failures']):5d} "
            f"denied={int(row['denied']):4d} avg_lat={avg:.3f}s"
        )


async def main_async(args: argparse.Namespace) -> int:
    random.seed(args.seed)
    require_psycopg()

    if not os.getenv("AEX_PG_DSN"):
        raise RuntimeError("AEX_PG_DSN is required.")
    dsn = os.environ["AEX_PG_DSN"]

    requested = parse_frameworks(args.frameworks)
    unknown = [f for f in requested if f not in ADAPTER_BUILDERS]
    if unknown:
        raise RuntimeError(f"Unknown frameworks: {unknown}. Allowed: {sorted(ADAPTER_BUILDERS.keys())}")

    enabled_frameworks: list[str] = []
    for fw in requested:
        mod = MODULE_CHECKS[fw]
        if require_module(mod, fw, args.strict_framework_imports):
            enabled_frameworks.append(fw)
    if not enabled_frameworks:
        raise RuntimeError("No framework modules available to run.")

    worker_specs: list[WorkerSpec] = []
    for idx in range(args.agents_total):
        fw = enabled_frameworks[idx % len(enabled_frameworks)]
        agent_name = f"{args.agent_prefix}-{fw}-{idx + 1}"
        token = ensure_agent_exists(
            dsn=dsn,
            agent_name=agent_name,
            budget_usd=args.budget_usd,
            rpm=args.rpm,
            tenant_id=args.tenant_id,
            project_id=args.project_id,
            create_missing=args.create_missing,
        )
        worker_specs.append(
            WorkerSpec(
                agent_name=agent_name,
                framework=fw,
                token=token,
                runtime_seconds=random.uniform(args.minutes_min * 60, args.minutes_max * 60),
                sleep_min=max(0.01, args.sleep_min),
                sleep_max=max(args.sleep_min, args.sleep_max),
            )
        )

    workers: list[tuple[WorkerSpec, Adapter, WorkerStats]] = []
    for spec in worker_specs:
        builder = ADAPTER_BUILDERS[spec.framework]
        try:
            adapter = builder(args.base_url, args.model, spec.token)
        except Exception as exc:
            if args.strict_framework_imports:
                raise RuntimeError(
                    f"Adapter init failed framework={spec.framework} agent={spec.agent_name}: {compact_error(exc)}"
                ) from exc
            print(
                f"[skip] adapter init failed framework={spec.framework} "
                f"agent={spec.agent_name} reason={compact_error(exc)}"
            )
            continue
        workers.append((spec, adapter, WorkerStats(agent_name=spec.agent_name, framework=spec.framework)))

    if not workers:
        raise RuntimeError("No runnable workers after adapter initialization.")

    print(
        f"[start] workers={len(workers)} frameworks={sorted({w[0].framework for w in workers})} "
        f"base_url={args.base_url} model={args.model}"
    )
    print(
        f"[profile] sequential_rounds={args.sequential_rounds} "
        f"parallel_runtime={args.minutes_min}-{args.minutes_max} min "
        f"burst_rounds={args.burst_rounds}"
    )

    start_ts = now_iso()
    await run_sequential_phase(workers, args.sequential_rounds)
    await run_parallel_phase(workers)
    await run_burst_phase(workers, args.burst_rounds, args.burst_width, args.burst_pause_sec)
    end_ts = now_iso()

    summary = summarize(workers)
    print_summary(summary)
    snapshot = await fetch_aex_snapshot(args.base_url)

    report = {
        "started_at": start_ts,
        "ended_at": end_ts,
        "config": {
            "base_url": args.base_url,
            "model": args.model,
            "frameworks_requested": requested,
            "frameworks_enabled": sorted({w[0].framework for w in workers}),
            "agents_total_requested": args.agents_total,
            "agents_total_runnable": len(workers),
            "minutes_min": args.minutes_min,
            "minutes_max": args.minutes_max,
            "budget_usd": args.budget_usd,
            "rpm": args.rpm,
            "tenant_id": args.tenant_id,
            "project_id": args.project_id,
        },
        "summary": summary,
        "workers": [stats.to_dict() for _, _, stats in workers],
        "aex_snapshot": snapshot,
    }

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"stress_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_file.write_text(json.dumps(report, indent=2))
    print(f"[done] report={out_file}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Real framework stress test for AEX v2.1.")
    parser.add_argument("--base-url", default="http://127.0.0.1:9000/v1")
    parser.add_argument("--model", default="gpt-oss-20b")
    parser.add_argument("--frameworks", default="openai,instructor,langchain,langgraph,llama_index,haystack,dspy,pydantic_ai,smolagents,agno")
    parser.add_argument("--agents-total", type=int, default=15)
    parser.add_argument("--minutes-min", type=float, default=5.0)
    parser.add_argument("--minutes-max", type=float, default=8.0)
    parser.add_argument("--sleep-min", type=float, default=0.05, help="Min sleep between requests per worker.")
    parser.add_argument("--sleep-max", type=float, default=0.35, help="Max sleep between requests per worker.")
    parser.add_argument("--sequential-rounds", type=int, default=2)
    parser.add_argument("--burst-rounds", type=int, default=8)
    parser.add_argument("--burst-width", type=int, default=6)
    parser.add_argument("--burst-pause-sec", type=float, default=0.8)
    parser.add_argument("--budget-usd", type=float, default=3.0)
    parser.add_argument("--rpm", type=int, default=240)
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--agent-prefix", default="rf21")
    parser.add_argument("--create-missing", action="store_true")
    parser.add_argument("--strict-framework-imports", action="store_true")
    parser.add_argument("--seed", type=int, default=21)
    parser.add_argument("--output-dir", default="real_agents/out")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.minutes_max < args.minutes_min:
        raise SystemExit("--minutes-max must be >= --minutes-min")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
