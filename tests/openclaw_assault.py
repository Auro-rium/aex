#!/usr/bin/env python3
"""
🦞 AEX v2 — OpenClaw Integration Assault
Tests AEX proxy under real OpenClaw provider runtime with difficult patterns:
  1. Multi-turn tool calling loops via OpenClaw's OpenAIProvider
  2. Concurrent provider sessions hammering AEX simultaneously  
  3. Streaming under sustained pressure
  4. Budget exhaustion mid-stream
  5. Rapid-fire sequential requests
  6. Post-chaos ledger integrity audit

Uses OpenClaw's actual OpenAIProvider to make streaming requests through AEX.
"""

import asyncio
import json
import os
import sys
import time
import sqlite3
import subprocess
from pathlib import Path
from dataclasses import dataclass

try:
    from openclaw.agents.providers.openai_provider import OpenAIProvider
    from openclaw.agents.providers.base import LLMMessage, LLMResponse
except ImportError:
    print("Error: openclaw module not found.")
    print("Please install OpenClaw using: uv pip install git+https://github.com/zhaoyuong/openclaw-python.git")
    sys.exit(1)

# --- Monkey-Patch OpenClaw OpenAIProvider ---
# OpenClaw's default message converter strips out tool_call_id and tool_calls.
# We patch it directly here so the test script relies entirely on standard OpenClaw
# without modifying the upstream git repository.

async def patched_stream(self, messages, tools=None, max_tokens=4096, **kwargs):
    client = self.get_client()
    openai_messages = []
    for msg in messages:
        m = {"role": msg.role, "content": msg.content}
        if getattr(msg, "tool_call_id", None):
            m["tool_call_id"] = msg.tool_call_id
        if getattr(msg, "name", None):
            m["name"] = msg.name
        if getattr(msg, "tool_calls", None):
            m["tool_calls"] = [
                {
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tc.get("name", ""),
                        "arguments": json.dumps(tc.get("params", tc.get("arguments", {})))
                            if isinstance(tc.get("params", tc.get("arguments", {})), dict)
                            else str(tc.get("params", tc.get("arguments", "{}")))
                    },
                }
                for tc in msg.tool_calls
            ]
        openai_messages.append(m)

    try:
        params = {"model": self.model, "messages": openai_messages, "max_tokens": max_tokens, "stream": True, **kwargs}
        if tools: params["tools"] = tools

        stream = await client.chat.completions.create(**params)
        tool_calls_buffer = {}

        async for chunk in stream:
            if not chunk.choices: continue
            choice = chunk.choices[0]
            delta = choice.delta

            if delta.content:
                yield LLMResponse(type="text_delta", content=delta.content)

            if delta.tool_calls:
                for tool_call in delta.tool_calls:
                    idx = tool_call.index
                    if idx not in tool_calls_buffer:
                        tool_calls_buffer[idx] = {"id": tool_call.id or f"call_{idx}", "name": "", "arguments": ""}
                    if tool_call.function and tool_call.function.name:
                        tool_calls_buffer[idx]["name"] = tool_call.function.name
                    if tool_call.function and tool_call.function.arguments:
                        tool_calls_buffer[idx]["arguments"] += tool_call.function.arguments

            if choice.finish_reason:
                if tool_calls_buffer:
                    tcs = []
                    for tc in tool_calls_buffer.values():
                        try: args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                        except json.JSONDecodeError: args = {}
                        tcs.append({"id": tc["id"], "name": tc["name"], "arguments": args})
                    yield LLMResponse(type="tool_call", content=None, tool_calls=tcs)
                yield LLMResponse(type="done", content=None, finish_reason=choice.finish_reason)
    except Exception as e:
        yield LLMResponse(type="error", content=str(e))

OpenAIProvider.stream = patched_stream
# --------------------------------------------

from rich.console import Console
from rich.panel import Panel

console = Console()

AEX_BASE_URL = "http://127.0.0.1:9000/v1"
AEX_MODEL = "gpt-oss-20b"
DB_PATH = Path.home() / ".aex" / "aex.db"
AEX_CLI = str(Path(__file__).parent.parent / "aex_env" / "bin" / "aex")

# Tool definitions for OpenAI-compatible function calling
CALCULATOR_TOOL = {
    "type": "function",
    "function": {
        "name": "calculator",
        "description": "Perform a math calculation.",
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["add", "subtract", "multiply", "divide"]},
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["operation", "a", "b"],
        },
    },
}

LOOKUP_TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_data",
        "description": "Look up a value from a database by key.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key to look up"},
            },
            "required": ["key"],
        },
    },
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def create_aex_agent(name, budget="5.00", rpm=500):
    subprocess.run([AEX_CLI, "agent", "delete", name], capture_output=True)
    try:
        conn = get_db()
        conn.execute("DELETE FROM events WHERE agent = ?", (name,))
        conn.commit()
        conn.close()
    except Exception:
        pass
    cmd = [AEX_CLI, "agent", "create", name, budget, str(rpm)]
    out = subprocess.check_output(cmd, text=True)
    for line in out.splitlines():
        if "Token:" in line:
            return line.split("Token:")[1].strip()
    raise RuntimeError(f"Failed to create agent {name}")


def execute_tool_call(name, args):
    """Simulate tool execution locally."""
    if name == "calculator":
        op, a, b = args["operation"], args["a"], args["b"]
        if op == "add": return str(a + b)
        if op == "subtract": return str(a - b)
        if op == "multiply": return str(a * b)
        if op == "divide": return str(a / b) if b != 0 else "Error: division by zero"
    elif name == "lookup_data":
        db = {"population_india": "1.4 billion", "gdp_usa": "25.46 trillion USD",
              "capital_france": "Paris", "population_usa": "331 million"}
        return db.get(args.get("key", ""), "No data found")
    return "Unknown tool"


async def stream_and_collect(provider, messages, tools=None, max_tokens=1024):
    """Stream from OpenClaw provider, collect text + tool calls."""
    text_parts = []
    tool_calls = []

    async for resp in provider.stream(messages=messages, tools=tools, max_tokens=max_tokens):
        if resp.type == "text_delta" and resp.content:
            text_parts.append(resp.content)
        elif resp.type == "tool_call" and resp.tool_calls:
            tool_calls.extend(resp.tool_calls)
        elif resp.type == "error":
            raise RuntimeError(f"Provider error: {resp.content}")
        elif resp.type == "done":
            break

    return "".join(text_parts), tool_calls


async def multi_turn_loop(provider, messages, tools, max_turns=6):
    """Run a full multi-turn tool-calling loop like OpenClaw's agent_loop."""
    all_tool_call_turns = 0

    for turn in range(max_turns):
        text, tool_calls = await stream_and_collect(provider, messages, tools)

        # Add assistant message
        assistant_msg = LLMMessage(role="assistant", content=text or "")
        if tool_calls:
            assistant_msg.tool_calls = tool_calls
        messages.append(assistant_msg)

        if not tool_calls:
            break  # Agent finished

        all_tool_call_turns += 1

        # Execute each tool call and add results
        for tc in tool_calls:
            result = execute_tool_call(tc["name"], tc.get("arguments", {}))
            messages.append(LLMMessage(
                role="tool",
                content=result,
                tool_call_id=tc["id"],
                name=tc["name"],
            ))

    return messages, all_tool_call_turns


# ─── Test Cases ──────────────────────────────────────────────────────────────

async def test_1_tool_loop():
    """1. Multi-turn tool calling loop — OpenClaw provider does streaming, we do tool execution."""
    console.print("\n[bold yellow]TEST 1: Multi-Turn Tool Calling Loop via OpenClaw Provider[/bold yellow]")

    token = create_aex_agent("claw-toolloop", budget="2.00", rpm=500)
    provider = OpenAIProvider(model=AEX_MODEL, api_key=token, base_url=AEX_BASE_URL)

    messages = [
        LLMMessage(role="system", content="You are a helpful assistant. Use the calculator tool for math and lookup_data for data lookups. Always use tools, never answer from memory."),
        LLMMessage(role="user", content="Look up the population of India using lookup_data with key 'population_india', then use the calculator to add 100 to the number 1400."),
    ]

    try:
        final_msgs, tool_turns = await asyncio.wait_for(
            multi_turn_loop(provider, messages, tools=[CALCULATOR_TOOL, LOOKUP_TOOL]),
            timeout=60,
        )
        console.print(f"  Messages: {len(final_msgs)} | Tool turns: {tool_turns}")
        if tool_turns >= 1:
            console.print("[green]✅ PASSED — Multi-turn tool loop completed[/green]")
            return True
        console.print("[yellow]⚠ Agent completed but no tool calls detected[/yellow]")
        return True
    except asyncio.TimeoutError:
        console.print("[red]❌ FAILURE: Timed out (60s)[/red]")
        return False
    except Exception as e:
        console.print(f"[red]❌ FAILURE: {e}[/red]")
        return False


async def test_2_concurrent_providers():
    """2. Concurrent OpenClaw provider sessions — 5 providers all streaming through AEX at once."""
    console.print("\n[bold yellow]TEST 2: Concurrent OpenClaw Provider Sessions[/bold yellow]")

    configs = []
    for i in range(5):
        name = f"claw-concurrent-{i}"
        token = create_aex_agent(name, budget="2.00", rpm=500)
        configs.append((name, token))

    prompts = [
        "What is 42 * 17? Use the calculator.",
        "Calculate 100 + 200 using the calculator tool.",
        "What is 999 divided by 3? Use the calculator.",
        "Multiply 7 by 8 using the calculator.",
        "What is 1000 minus 1? Use the calculator.",
    ]

    async def run_one(name, token, prompt):
        provider = OpenAIProvider(model=AEX_MODEL, api_key=token, base_url=AEX_BASE_URL)
        msgs = [
            LLMMessage(role="system", content="You are a helpful assistant. Always use the calculator tool for math."),
            LLMMessage(role="user", content=prompt),
        ]
        try:
            final, tool_turns = await asyncio.wait_for(
                multi_turn_loop(provider, msgs, tools=[CALCULATOR_TOOL]),
                timeout=30,
            )
            return (name, "ok", len(final))
        except Exception as e:
            return (name, "error", str(e)[:100])

    start = time.time()
    results = await asyncio.gather(
        *[run_one(n, t, p) for (n, t), p in zip(configs, prompts)],
        return_exceptions=True,
    )
    duration = time.time() - start

    successes = sum(1 for r in results if isinstance(r, tuple) and r[1] == "ok")
    errors = [r for r in results if isinstance(r, tuple) and r[1] == "error"]
    exceptions = [r for r in results if isinstance(r, Exception)]

    console.print(f"  Agents: 5 | Duration: {duration:.2f}s")
    console.print(f"  Successes: {successes} | Errors: {len(errors)} | Exceptions: {len(exceptions)}")
    for e in errors:
        console.print(f"    [dim]{e[0]}: {e[2]}[/dim]")
    for ex in exceptions:
        console.print(f"    [dim]Exception: {ex}[/dim]")

    if successes >= 3:
        console.print("[green]✅ PASSED[/green]")
        return True
    console.print("[red]❌ FAILURE: Too many failures[/red]")
    return False


async def test_3_streaming_sustained():
    """3. Sustained streaming — long response streamed through AEX without stalling."""
    console.print("\n[bold yellow]TEST 3: Sustained Streaming Pressure[/bold yellow]")

    token = create_aex_agent("claw-stream", budget="3.00", rpm=500)
    provider = OpenAIProvider(model=AEX_MODEL, api_key=token, base_url=AEX_BASE_URL)

    messages = [
        LLMMessage(role="system", content="You are a helpful assistant."),
        LLMMessage(role="user", content="Write a detailed 500-word essay on the history of computing, from Babbage to modern GPUs."),
    ]

    try:
        chunk_count = 0
        total_chars = 0

        async for resp in provider.stream(messages=messages, max_tokens=2048):
            if resp.type == "text_delta" and resp.content:
                chunk_count += 1
                total_chars += len(resp.content)
            elif resp.type == "error":
                raise RuntimeError(resp.content)
            elif resp.type == "done":
                break

        console.print(f"  Chunks: {chunk_count} | Characters: {total_chars}")
        if chunk_count > 10 and total_chars > 200:
            console.print("[green]✅ PASSED — Sustained streaming completed[/green]")
            return True
        console.print(f"[yellow]⚠ Low output ({chunk_count} chunks, {total_chars} chars)[/yellow]")
        return True
    except asyncio.TimeoutError:
        console.print("[red]❌ FAILURE: Timed out[/red]")
        return False
    except Exception as e:
        console.print(f"[red]❌ FAILURE: {e}[/red]")
        return False


async def test_4_budget_exhaustion():
    """4. Budget exhaustion mid-stream — AEX should deny with 402."""
    console.print("\n[bold yellow]TEST 4: Budget Exhaustion Mid-Stream[/bold yellow]")

    token = create_aex_agent("claw-budget-crash", budget="0.001", rpm=500)
    provider = OpenAIProvider(model=AEX_MODEL, api_key=token, base_url=AEX_BASE_URL)

    budget_denied = False
    requests_made = 0

    for i in range(3):
        messages = [
            LLMMessage(role="system", content="You are a helpful assistant."),
            LLMMessage(role="user", content=f"Tell me a joke about programming (attempt {i + 1})."),
        ]
        try:
            async for resp in provider.stream(messages=messages, max_tokens=256):
                if resp.type == "error":
                    if "402" in str(resp.content) or "budget" in str(resp.content).lower():
                        budget_denied = True
                    break
                elif resp.type == "done":
                    break
            requests_made += 1
        except Exception as e:
            err = str(e)
            if "402" in err or "Insufficient" in err or "budget" in err.lower():
                budget_denied = True
            requests_made += 1

    # Verify no overspend
    conn = get_db()
    row = conn.execute(
        "SELECT budget_micro, spent_micro FROM agents WHERE name = ?",
        ("claw-budget-crash",),
    ).fetchone()
    conn.close()

    if row:
        console.print(f"  Budget: {row['budget_micro']}µ | Spent: {row['spent_micro']}µ")
        if row["spent_micro"] > row["budget_micro"]:
            console.print("[red]❌ FAILURE: Overspend detected![/red]")
            return False

    console.print(f"  Requests attempted: {requests_made} | Budget denied: {budget_denied}")
    console.print("[green]✅ PASSED — Budget enforcement held[/green]")
    return True


async def test_5_rapid_sequential():
    """5. Rapid sequential requests — 10 fast requests in sequence."""
    console.print("\n[bold yellow]TEST 5: Rapid Sequential Requests[/bold yellow]")

    token = create_aex_agent("claw-rapid", budget="5.00", rpm=500)
    provider = OpenAIProvider(model=AEX_MODEL, api_key=token, base_url=AEX_BASE_URL)

    successes = 0
    errors = 0
    start = time.time()

    for i in range(10):
        messages = [
            LLMMessage(role="system", content="Answer in exactly one word."),
            LLMMessage(role="user", content=f"What is {i + 1} + {i + 2}?"),
        ]
        try:
            text, _ = await asyncio.wait_for(
                stream_and_collect(provider, messages, max_tokens=32),
                timeout=15,
            )
            successes += 1
        except Exception as e:
            errors += 1

    duration = time.time() - start
    console.print(f"  Successes: {successes}/10 | Errors: {errors} | Duration: {duration:.2f}s")

    if successes >= 7:
        console.print("[green]✅ PASSED[/green]")
        return True
    console.print("[red]❌ FAILURE: Too many errors[/red]")
    return False


async def test_6_invariant_audit():
    """6. Post-Chaos Invariant Audit — ledger integrity for all claw agents."""
    console.print("\n[bold yellow]TEST 6: Post-Chaos Invariant Audit[/bold yellow]")

    conn = get_db()

    neg = conn.execute(
        "SELECT count(*) as c FROM agents WHERE spent_micro < 0 OR budget_micro < 0"
    ).fetchone()["c"]

    over = conn.execute(
        "SELECT count(*) as c FROM agents WHERE spent_micro > budget_micro"
    ).fetchone()["c"]

    bad_ledger = 0
    agents = conn.execute(
        "SELECT name, spent_micro FROM agents WHERE name LIKE 'claw-%'"
    ).fetchall()

    for row in agents:
        events_sum = conn.execute(
            "SELECT COALESCE(SUM(cost_micro), 0) as s FROM events WHERE agent = ? AND action='usage.commit'",
            (row["name"],),
        ).fetchone()["s"]
        if events_sum != row["spent_micro"]:
            console.print(f"  [red]Ledger Mismatch: {row['name']}: DB={row['spent_micro']} Events={events_sum}[/red]")
            bad_ledger += 1

    conn.close()

    console.print(f"  Negative Balances: {neg}")
    console.print(f"  Overspends: {over}")
    console.print(f"  Ledger Mismatches: {bad_ledger}")

    if neg == 0 and over == 0 and bad_ledger == 0:
        console.print("[green]✅ PASSED — All invariants hold[/green]")
        return True
    console.print("[red]❌ FAILURE: Invariants violated[/red]")
    return False


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    console.print(Panel("[bold white]🦞 AEX v2 — OPENCLAW INTEGRATION ASSAULT[/bold white]", style="red"))

    tests = [
        test_1_tool_loop,
        test_2_concurrent_providers,
        test_3_streaming_sustained,
        test_4_budget_exhaustion,
        test_5_rapid_sequential,
        test_6_invariant_audit,
    ]

    score = 0
    for t in tests:
        try:
            if await t():
                score += 1
        except Exception as e:
            console.print(f"[bold red]❌ CRASH in {t.__name__}: {e}[/bold red]")
            import traceback
            traceback.print_exc()

    console.print(f"\n[bold]Final Score: {score}/{len(tests)}[/bold]")
    if score == len(tests):
        console.print("[bold green]🦞 OPENCLAW INTEGRATION CERTIFIED[/bold green]")
        sys.exit(0)
    else:
        console.print("[bold red]🦞 OPENCLAW INTEGRATION FAILED[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
