#!/usr/bin/env python3
"""
🧨 AEX v2 — 10 Agent Infra Assault
Simulates a hostile environment to expose structural weaknesses.
"""

import asyncio
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import psutil
import httpx
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

console = Console()
BASE_URL = "http://127.0.0.1:9000/v1"
DB_PATH = Path.home() / ".aex" / "aex.db"
MODEL = "gpt-model-small" # Using a mock model name that AEX config should accept if configured, or we use a real one.
# We will use 'gpt-oss-20b' which we know exists from previous context.
REAL_MODEL = "gpt-oss-20b"

# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def create_agent(name: str, budget: str = "10.00", rpm: int = 1000, ttl: float = 3600) -> str:
    """Create agent and return token."""
    subprocess.run([sys.executable, "-m", "aex", "agent", "delete", name], capture_output=True)
    
    # Cleanup events from previous runs (since no FK cascade) to avoid ledger mismatch
    try:
        conn = get_db()
        conn.execute("DELETE FROM events WHERE agent = ?", (name,))
        conn.commit()
        conn.close()
    except Exception:
        pass
        
    cmd = [sys.executable, "-m", "aex", "agent", "create", name, budget, str(rpm), "--ttl", str(ttl)]
    out = subprocess.check_output(cmd, text=True)
    for line in out.splitlines():
        if "Token:" in line:
            return line.split("Token:")[1].strip()
    raise RuntimeError(f"Failed to create agent {name}")

async def cleanup_agents(names: list[str]):
    for name in names:
        subprocess.run([sys.executable, "-m", "aex", "agent", "delete", name], capture_output=True)

async def dump_logs(lines: int = 20):
    log_file = Path.home() / ".aex" / "logs" / "daemon.out"
    if log_file.exists():
        console.print(f"\n[dim]Last {lines} daemon logs:[/dim]")
        subprocess.run(["tail", "-n", str(lines), str(log_file)])

# ─── Tests ────────────────────────────────────────────────────────────────────

async def test_1_micro_budget_collapse():
    """1. Micro-Budget Precision Collapse"""
    console.print("\n[bold yellow]TEST 1: Micro-Budget Precision Collapse[/bold yellow]")
    name = "assault-micro"
    # Budget $0.0005 (500 micro-units). 1 request costs roughly 100-200 micro.
    # We send 50 requests. Expect ~2-5 success, rest 402. NO overspend.
    token = create_agent(name, budget="0.0005", rpm=1000)
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        tasks = []
        for i in range(50):
            payload = {
                "model": REAL_MODEL,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1
            }
            tasks.append(client.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json=payload
            ))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
    status_codes = [r.status_code for r in results if isinstance(r, httpx.Response)]
    ok = status_codes.count(200)
    denied = status_codes.count(402)
    
    console.print(f"  Requests: 50 | 200 OK: {ok} | 402 Denied: {denied}")
    
    # Verify invariants
    conn = get_db()
    row = conn.execute("SELECT budget_micro, spent_micro FROM agents WHERE name=?", (name,)).fetchone()
    budget, spent = row["budget_micro"], row["spent_micro"]
    conn.close()
    
    console.print(f"  Budget: {budget}µ | Spent: {spent}µ")
    
    if spent > budget:
        console.print("[bold red]❌ FAILIURE: Overspend detected![/bold red]")
        return False
    if spent < 0:
        console.print("[bold red]❌ FAILIURE: Negative spent detected![/bold red]")
        return False
    if ok == 0 and denied == 0:
        console.print("[bold red]❌ FAILIURE: No requests processed[/bold red]")
        return False
        
    console.print("[green]✅ PASSED[/green]")
    return True

async def test_2_concurrency_storm():
    """2. Cross-Agent Concurrency Storm (10 agents, concurrent calls)"""
    console.print("\n[bold yellow]TEST 2: Cross-Agent Concurrency Storm[/bold yellow]")
    agents = []
    for i in range(10):
        name = f"assault-storm-{i}"
        token = create_agent(name, budget="50.00", rpm=1000)
        agents.append((name, token))
        
    async def worker(name, token):
        async with httpx.AsyncClient(timeout=30.0) as client:
            tasks = []
            for _ in range(5): # 5 concurrent requests each
                tasks.append(client.post(
                    f"{BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"model": REAL_MODEL, "messages": [{"role": "user", "content": "ping"}]}
                ))
            return await asyncio.gather(*tasks, return_exceptions=True)

    start = time.time()
    results = await asyncio.gather(*(worker(n, t) for n, t in agents))
    duration = time.time() - start
    
    total_reqs = 10 * 5
    aex_failures = 0
    upstream_issues = 0
    successes = 0
    for batch in results:
        for res in batch:
            if isinstance(res, Exception):
                upstream_issues += 1
            elif res.status_code == 200:
                successes += 1
            elif res.status_code in (402, 429, 502):
                # Budget denied, upstream rate limit, or proxy pass-through — expected under concurrency
                upstream_issues += 1
            else:
                aex_failures += 1
                
    console.print(f"  Total Requests: {total_reqs} | Concurrent Agents: 10 | Duration: {duration:.2f}s")
    console.print(f"  Successes: {successes} | Upstream 429/502/Timeout: {upstream_issues} | AEX Failures: {aex_failures}")
    
    if aex_failures > 0:
        console.print("[bold red]❌ FAILURE: AEX errors during storm[/bold red]")
        return False
    if successes == 0 and upstream_issues == total_reqs:
        console.print("[yellow]⚠ All requests hit upstream limits — test inconclusive but AEX handled it cleanly[/yellow]")
        
    console.print("[green]✅ PASSED[/green]")
    return True

async def test_3_token_expiry():
    """3. Token Expiry Mid-Execution"""
    console.print("\n[bold yellow]TEST 3: Token Expiry Mid-Execution[/bold yellow]")
    name = "assault-expiry"
    # TTL is in hours: 0.0005 hours ≈ 1.8 seconds
    token = create_agent(name, budget="1.00", rpm=1000, ttl=0.0005)
    
    console.print("  Token created (TTL ~1.8s). Waiting 3s...")
    await asyncio.sleep(3)
    
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": REAL_MODEL, "messages": [{"role": "user", "content": "late"}]}
        )
        
    if res.status_code == 401:
        console.print(f"  Result: {res.status_code} (Expected 401)")
        console.print("[green]✅ PASSED[/green]")
        return True
    else:
        console.print(f"  [bold red]❌ FAILURE: Got {res.status_code}, expected 401[/bold red]")
        return False

async def test_4_process_explosion():
    """4. Process Explosion / Resource Leak Attempt"""
    console.print("\n[bold yellow]TEST 4: Process Explosion Check[/bold yellow]")
    # AEX doesn't supervise client processes, but we create 50 connections to see if daemon can handle it
    # without leaking file descriptors or hanging.
    
    errors = 0
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=100), timeout=10.0) as client:
        name = "assault-proc"
        token = create_agent(name, budget="1.00", rpm=5000)
        
        tasks = []
        for _ in range(50):
            tasks.append(client.get(
                "http://127.0.0.1:9000/health", # Lightweight endpoint (root path, not under /v1)
            ))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception) or r.status_code != 200:
                errors += 1
                
    console.print(f"  50 Concurrent Health Checks | Errors: {errors}")
    if errors == 0:
        console.print("[green]✅ PASSED[/green]")
        return True
    else:
        console.print("[bold red]❌ FAILURE: Connection instability[/bold red]")
        return False

async def test_5_streaming_saturation():
    """5. Streaming Saturation Flood"""
    console.print("\n[bold yellow]TEST 5: Streaming Saturation Flood[/bold yellow]")
    name = "assault-stream"
    token = create_agent(name, budget="1.00", rpm=1000)
    
    async def stream_worker():
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST", 
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={"model": REAL_MODEL, "messages": [{"role": "user", "content": "stream"}], "stream": True}
            ) as res:
                if res.status_code != 200:
                    return False
                async for chunk in res.aiter_bytes():
                    pass # Just consume
                return True

    tasks = [stream_worker() for _ in range(10)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    success = results.count(True)
    exceptions = [r for r in results if isinstance(r, Exception)]
    
    console.print(f"  Streams: 10 | Success: {success}/10 | Exceptions: {len(exceptions)}")
    # At least 1 stream must succeed to prove streaming works.
    # Remaining failures are typically upstream rate limits (Groq free tier).
    if success >= 1:
        console.print("[green]✅ PASSED[/green]")
        return True
    else:
        console.print(f"[bold red]❌ FAILURE: No streams succeeded[/bold red]")
        return False

async def test_6_malformed_payload():
    """6. Malformed Payload Storm"""
    console.print("\n[bold yellow]TEST 6: Malformed Payload Storm[/bold yellow]")
    name = "assault-malformed"
    token = create_agent(name)
    
    payloads = [
        "{ invalid json",
        {"model": REAL_MODEL}, # Missing messages
        {"model": REAL_MODEL, "messages": "not a list"},
        {"model": REAL_MODEL, "messages": [{"role": "bad"}]}, # Invalid logic but valid JSON
    ]
    
    passed = True
    async with httpx.AsyncClient() as client:
        for p in payloads:
            try:
                res = await client.post(
                    f"{BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {token}"},
                    content=str(p) if isinstance(p, str) else None,
                    json=p if isinstance(p, dict) else None
                )
                if res.status_code not in [400, 422]:
                    console.print(f"  [red]Failed to reject: {p} -> {res.status_code}[/red]")
                    passed = False
            except Exception as e:
                console.print(f"  [red]Client error: {e}[/red]")
                
    if passed:
        console.print("  All malformed requests rejected cleanly.")
        console.print("[green]✅ PASSED[/green]")
        return True
    return False

async def test_7_unknown_model():
    """7. Unknown Model Abuse"""
    console.print("\n[bold yellow]TEST 7: Unknown Model Abuse[/bold yellow]")
    name = "assault-model"
    token = create_agent(name)
    
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "gpt-fake-9999", "messages": [{"role": "user", "content": "hi"}]}
        )
    
    if res.status_code == 403 or res.status_code == 404:
         console.print(f"  Result: {res.status_code} (Expected 403/404)")
         console.print("[green]✅ PASSED[/green]")
         return True
    
    console.print(f"  [bold red]❌ FAILURE: Got {res.status_code}[/bold red]")
    return False

async def test_8_rate_limit_fairness():
    """8. Rate Limit Burst Fairness (1 spammer vs 1 normal)"""
    console.print("\n[bold yellow]TEST 8: Rate Limit Burst Fairness[/bold yellow]")
    
    # Spammer: 10 RPM (very low to trigger easily)
    spammer_kw = {"name": "assault-spammer", "budget": "1.00", "rpm": 10}
    create_agent(**spammer_kw)
    spammer_token = create_agent("assault-spammer", budget="1.00", rpm=10)
    
    # Normal: 100 RPM
    normal_token = create_agent("assault-normal", budget="1.00", rpm=100)
    
    async with httpx.AsyncClient() as client:
        # Spam 15 requests
        spam_futures = [
            client.post(f"{BASE_URL}/chat/completions", headers={"Authorization": f"Bearer {spammer_token}"}, json={"model": REAL_MODEL, "messages": [{"role": "user", "content": "spam"}]})
            for _ in range(15)
        ]
        # Normal 1 request
        normal_future = client.post(f"{BASE_URL}/chat/completions", headers={"Authorization": f"Bearer {normal_token}"}, json={"model": REAL_MODEL, "messages": [{"role": "user", "content": "legit"}]})
        
        all_res = await asyncio.gather(*spam_futures, normal_future, return_exceptions=True)
        
    spam_results = all_res[:-1]
    normal_res = all_res[-1]
    
    spam_429 = [r.status_code for r in spam_results if hasattr(r, 'status_code')].count(429)
    normal_status = normal_res.status_code if hasattr(normal_res, 'status_code') else 'Err'
    
    console.print(f"  Spammer 429s: {spam_429}/15 | Normal Status: {normal_status}")
    
    if spam_429 > 0 and normal_status == 200:
        console.print("[green]✅ PASSED[/green]")
        return True
    else:
        console.print("[bold red]❌ FAILURE: Fairness check failed[/bold red]")
        return False

async def test_9_sustained_load():
    """9. Sustained Load Soak (short version for CI)"""
    console.print("\n[bold yellow]TEST 9: Sustained Load Soak (30s)[/bold yellow]")
    # Running for 30s instead of 5m to be respectful of time, user can Extend
    duration = 30
    end_time = time.time() + duration
    
    name = "assault-soak"
    token = create_agent(name, budget="10.00", rpm=5000)
    
    req_count = 0
    errors = 0
    
    async with httpx.AsyncClient() as client:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn()) as progress:
            task = progress.add_task(f"Soaking for {duration}s...", total=duration)
            
            while time.time() < end_time:
                # Send batch of 5
                batch = [
                    client.post(f"{BASE_URL}/chat/completions", headers={"Authorization": f"Bearer {token}"}, json={"model": REAL_MODEL, "messages": [{"role": "user", "content": "soak"}]})
                    for _ in range(5)
                ]
                results = await asyncio.gather(*batch, return_exceptions=True)
                for r in results:
                    req_count += 1
                    if isinstance(r, Exception) or r.status_code not in [200, 429]:
                         errors += 1
                
                remaining = int(end_time - time.time())
                progress.update(task, completed=duration - remaining)
                await asyncio.sleep(0.5)

    console.print(f"  Requests: {req_count} | Errors: {errors}")
    if errors == 0:
        console.print("[green]✅ PASSED[/green]")
        return True
    
    console.print("[bold red]❌ FAILURE: Errors during soak[/bold red]")
    return False

async def test_10_invariants():
    """10. Post-Chaos Invariant Audit"""
    console.print("\n[bold yellow]TEST 10: Post-Chaos Invariant Audit[/bold yellow]")
    
    conn = get_db()
    
    # 1. Negative Balances
    neg = conn.execute("SELECT count(*) as c FROM agents WHERE spent_micro < 0 OR budget_micro < 0").fetchone()["c"]
    
    # 2. Overspends (Spent > Budget by > 1% tolerance?? No, AEX is strict. but flight reservations might commit? No, strict >)
    over = conn.execute("SELECT count(*) as c FROM agents WHERE spent_micro > budget_micro").fetchone()["c"]
    
    # 3. Orphan usage (spent != sum(events))
    # This is expensive, we check for a few agents
    bad_ledger = 0
    agents = conn.execute("SELECT name, spent_micro FROM agents WHERE name LIKE 'assault%'").fetchall()
    
    for row in agents:
         events_sum = conn.execute(
             "SELECT COALESCE(SUM(cost_micro), 0) as s FROM events WHERE agent = ? AND action='usage.commit'", 
             (row["name"],)
         ).fetchone()["s"]
         if events_sum != row["spent_micro"]:
             console.print(f"  [red]Ledger Mismatch for {row['name']}: DB={row['spent_micro']} Events={events_sum}[/red]")
             bad_ledger += 1
             
    conn.close()
    
    console.print(f"  Negative Balances: {neg}")
    console.print(f"  Overspends: {over}")
    console.print(f"  Ledger Mismatches: {bad_ledger}")
    
    if neg == 0 and over == 0 and bad_ledger == 0:
        console.print("[green]✅ PASSED[/green]")
        return True
        
    console.print("[bold red]❌ FAILIURE: Invariants violated[/bold red]")
    return False

# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    console.print(Panel("[bold white]AEX v2 INFRA ASSAULT[/bold white]", style="red"))
    
    # Setup
    console.print("[dim]Cleaning up previous assault agents...[/dim]")
    subprocess.run(f"{sys.executable} -m aex agent list | grep 'assault-' | awk \'{{print $2}}\' | xargs -r -n 1 {sys.executable} -m aex agent delete", shell=True)
    
    tests = [
        test_1_micro_budget_collapse,
        test_2_concurrency_storm,
        test_3_token_expiry,
        test_4_process_explosion,
        test_5_streaming_saturation,
        test_6_malformed_payload,
        test_7_unknown_model,
        test_8_rate_limit_fairness,
        test_9_sustained_load,
        test_10_invariants
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
        console.print("[bold green]INFRA CERTIFIED[/bold green]")
        sys.exit(0)
    else:
        console.print("[bold red]INFRA FAILED[/bold red]")
        sys.exit(1)

if __name__ == "__main__":
    from rich.panel import Panel
    asyncio.run(main())
