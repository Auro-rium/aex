"""Operational commands: run, metrics, models, version, doctor, status, audit."""

import os
import sys
import signal
import subprocess

import typer
from rich.table import Table

from .. import __version__
from ..daemon.db import get_db_connection
from . import app, models_app, console, AEX_DIR, DB_PATH, LOG_DIR, CONFIG_DIR, MODELS_CONFIG_FILE, PID_FILE, get_daemon_pid


# ── Run ─────────────────────────────────────────────────────────────────────

@app.command("run")
def run_command(
    agent: str = typer.Option(..., "--agent", "-a", help="Agent name to run as"),
    command: list[str] = typer.Argument(..., help="Command to run"),
):
    """
    Run a command as an agent using AEX proxy.

    Example: aex run --agent my-agent python script.py
    """
    os.environ["AEX_DB_PATH"] = str(DB_PATH)

    try:
        with get_db_connection() as conn:
            row = conn.execute("SELECT api_token FROM agents WHERE name = ?", (agent,)).fetchone()
    except Exception:
        console.print("[red]Database error. Is AEX initialized?[/red]")
        raise typer.Exit(1)

    if not row:
        console.print(f"[red]Agent '{agent}' not found. Create it with: aex agent create {agent} ...[/red]")
        raise typer.Exit(1)

    token = row["api_token"]

    env = os.environ.copy()
    env["OPENAI_BASE_URL"] = "http://127.0.0.1:9000/v1"
    env["OPENAI_API_KEY"] = token
    env["AEX_AGENT_TOKEN"] = token

    cmd_str = " ".join(command)
    console.print(f"[green]AEX Kernel: Launching '{cmd_str}' as '{agent}'...[/green]")

    try:
        process = subprocess.Popen(command, env=env)
        process.wait()
        sys.exit(process.returncode)
    except KeyboardInterrupt:
        process.send_signal(signal.SIGINT)
        process.wait()
        sys.exit(130)
    except Exception as e:
        console.print(f"[red]Execution failed: {e}[/red]")
        sys.exit(1)


# ── Metrics ─────────────────────────────────────────────────────────────────

@app.command("metrics")
def show_metrics():
    """Display system metrics from the running daemon."""
    import httpx

    try:
        r = httpx.get("http://127.0.0.1:9000/metrics", timeout=5.0)
        if r.status_code != 200:
            console.print(f"[red]Daemon returned {r.status_code}[/red]")
            raise typer.Exit(1)

        data = r.json()

        console.print("[bold]AEX Kernel Metrics[/bold]")
        console.print(f"  Agents:             {data['total_agents']}")
        console.print(f"  Total Spent:        ${data['total_spent_global_usd']:.6f}")
        console.print(f"  Active Processes:   {data['active_processes']}")
        console.print(f"  Total Requests:     {data['total_requests']}")
        console.print(f"  Denied (Budget):    {data['total_denied_budget']}")
        console.print(f"  Denied (Rate):      {data['total_denied_rate_limit']}")
        console.print(f"  Policy Violations:  {data.get('total_policy_violations', 0)}")
        console.print()

        if data.get("top_models"):
            console.print("[bold]Top Models:[/bold]")
            for m in data["top_models"]:
                console.print(f"  {m['model']}: {m['count']} requests")
            console.print()

        table = Table(title="Per-Agent Financials")
        table.add_column("Agent")
        table.add_column("Spent ($)", justify="right")
        table.add_column("Remaining ($)", justify="right")
        table.add_column("Burn Rate", justify="right")
        table.add_column("TTB", justify="right")
        table.add_column("RPM", justify="right")
        table.add_column("Last Activity")

        for ag in data["agents"]:
            ttb = ag.get("ttb_seconds")
            if ttb is not None:
                if ttb > 3600:
                    ttb_str = f"{ttb // 3600}h {(ttb % 3600) // 60}m"
                elif ttb > 60:
                    ttb_str = f"{ttb // 60}m {ttb % 60}s"
                else:
                    ttb_str = f"{ttb}s"
            else:
                ttb_str = "∞"

            table.add_row(
                ag["name"],
                f"{ag['spent_usd']:.6f}",
                f"{ag['remaining_usd']:.6f}",
                f"{ag.get('burn_rate_micro_per_sec', 0)}µ/s",
                ttb_str,
                str(ag["rpm_limit"]),
                ag["last_activity"] or "N/A",
            )
        console.print(table)

    except httpx.ConnectError:
        console.print("[red]Cannot connect to daemon. Is it running?[/red]")
    except Exception as e:
        console.print(f"[red]Failed to fetch metrics: {e}[/red]")


# ── Models ──────────────────────────────────────────────────────────────────

@models_app.command("reload")
def reload_models():
    """Reload model configuration in the running daemon."""
    import httpx

    try:
        r = httpx.post("http://127.0.0.1:9000/admin/reload_config", timeout=5.0)
        if r.status_code == 200:
            console.print("[green]Model configuration reloaded successfully.[/green]")
        else:
            console.print(f"[red]Failed to reload config: {r.status_code} - {r.text}[/red]")
    except httpx.ConnectError:
        console.print("[red]Cannot connect to daemon. Is it running?[/red]")
    except Exception as e:
        console.print(f"[red]Failed to contact daemon: {e}[/red]")


# ── Version ─────────────────────────────────────────────────────────────────

@app.command("version")
def show_version():
    """Show AEX version."""
    console.print(f"AEX {__version__}")


# ── Doctor ──────────────────────────────────────────────────────────────────

@app.command("doctor")
def doctor(
    compat: bool = typer.Option(False, "--compat", help="Run compatibility contract tests against running daemon"),
    token: str = typer.Option("", "--token", help="Agent token for compat tests"),
):
    """Check AEX environment health. Use --compat for protocol fidelity tests."""

    # Compatibility contract mode
    if compat:
        if not token:
            console.print("[red]--compat requires --token (agent token for test requests)[/red]")
            raise typer.Exit(1)

        console.print(f"[bold]AEX Compatibility Contract — v{__version__}[/bold]")
        console.print()

        from ..daemon.utils.compat import run_all_compat_tests

        results = run_all_compat_tests(token)
        all_pass = True
        for r in results:
            icon = "✅" if r.passed else "❌"
            console.print(f"  {icon} {r.name}: {r.detail}")
            if not r.passed:
                all_pass = False

        console.print()
        passed = sum(1 for r in results if r.passed)
        if all_pass:
            console.print(f"[green]All {len(results)} compatibility tests passed.[/green]")
        else:
            console.print(f"[red]{passed}/{len(results)} tests passed.[/red]")
            raise typer.Exit(1)
        return

    # Standard doctor mode
    console.print(f"[bold]AEX Doctor v{__version__}[/bold]")
    console.print()

    all_ok = True

    # 1. AEX directory
    if AEX_DIR.exists():
        console.print(f"  ✅ AEX directory:  {AEX_DIR}")
    else:
        console.print("  ❌ AEX directory:  NOT FOUND (run: aex init)")
        all_ok = False

    # 2. Database
    if DB_PATH.exists():
        console.print(f"  ✅ Database:       {DB_PATH}")
        os.environ["AEX_DB_PATH"] = str(DB_PATH)
        try:
            from ..daemon.db import check_db_integrity

            if check_db_integrity():
                console.print("  ✅ DB Integrity:   PASS")
            else:
                console.print("  ❌ DB Integrity:   FAIL (run: aex audit)")
                all_ok = False
        except Exception as e:
            console.print(f"  ❌ DB Integrity:   ERROR ({e})")
            all_ok = False
    else:
        console.print("  ❌ Database:       NOT FOUND (run: aex init)")
        all_ok = False

    # 3. Config
    if MODELS_CONFIG_FILE.exists():
        console.print(f"  ✅ Config:         {MODELS_CONFIG_FILE}")
        try:
            import yaml

            with open(MODELS_CONFIG_FILE) as f:
                raw = yaml.safe_load(f)
            if raw and raw.get("version") == 1 and raw.get("models"):
                console.print(f"  ✅ Config Valid:   {len(raw['models'])} model(s)")
            else:
                console.print("  ⚠️  Config Valid:   Schema issues detected")
                all_ok = False
        except Exception as e:
            console.print(f"  ❌ Config Valid:   ERROR ({e})")
            all_ok = False
    else:
        console.print("  ❌ Config:         NOT FOUND")
        all_ok = False

    # 4. Daemon
    pid = get_daemon_pid()
    if pid:
        try:
            os.kill(pid, 0)
            console.print(f"  ✅ Daemon:         Running (PID {pid})")

            import httpx

            try:
                r = httpx.get("http://127.0.0.1:9000/health", timeout=3.0)
                if r.status_code == 200:
                    health = r.json()
                    console.print(f"  ✅ Daemon Health:  {health.get('status', 'unknown')} (v{health.get('version', '?')})")
                else:
                    console.print(f"  ⚠️  Daemon Health:  HTTP {r.status_code}")
            except Exception:
                console.print("  ⚠️  Daemon Health:  Not reachable on port 9000")
        except ProcessLookupError:
            console.print(f"  ⚠️  Daemon:         Stale PID file (PID {pid} not running)")
            all_ok = False
    else:
        console.print("  ⚠️  Daemon:         Not running")

    # 5. .env
    env_file = AEX_DIR / ".env"
    if env_file.exists():
        console.print(f"  ✅ .env file:      {env_file}")
    else:
        console.print("  ⚠️  .env file:      NOT FOUND (API keys may not be set)")

    console.print()
    if all_ok:
        console.print("[green]All checks passed.[/green]")
    else:
        console.print("[yellow]Some issues detected. Review above.[/yellow]")


# ── Status ──────────────────────────────────────────────────────────────────

@app.command("status")
def show_status():
    """Show enforcement summary."""
    os.environ["AEX_DB_PATH"] = str(DB_PATH)

    if not DB_PATH.exists():
        console.print("[red]Database not found. Run: aex init[/red]")
        raise typer.Exit(1)

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            agents = cursor.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
            total_budget = cursor.execute("SELECT SUM(budget_micro) FROM agents").fetchone()[0] or 0
            total_spent = cursor.execute("SELECT SUM(spent_micro) FROM agents").fetchone()[0] or 0
            total_reserved = cursor.execute("SELECT SUM(reserved_micro) FROM agents").fetchone()[0] or 0

            total_requests = cursor.execute(
                "SELECT COUNT(*) FROM events WHERE action IN ('usage.commit', 'USAGE_RECORDED')"
            ).fetchone()[0]
            budget_denials = cursor.execute(
                "SELECT COUNT(*) FROM events WHERE action IN ('budget.deny', 'DENIED_BUDGET')"
            ).fetchone()[0]
            rate_denials = cursor.execute("SELECT COUNT(*) FROM events WHERE action = 'RATE_LIMIT'").fetchone()[0]
            policy_violations = cursor.execute("SELECT COUNT(*) FROM events WHERE action = 'POLICY_VIOLATION'").fetchone()[0]
            kills = cursor.execute("SELECT COUNT(*) FROM events WHERE action = 'PROCESS_KILLED'").fetchone()[0]

            active_pids = cursor.execute("SELECT COUNT(*) FROM pids").fetchone()[0]

        console.print("[bold]AEX Enforcement Status[/bold]")
        console.print()
        console.print(f"  Agents:             {agents}")
        console.print(f"  Total Budget:       ${total_budget / 1_000_000:.6f}")
        console.print(f"  Total Spent:        ${total_spent / 1_000_000:.6f}")
        console.print(f"  Total Reserved:     ${total_reserved / 1_000_000:.6f}")
        console.print(f"  Remaining:          ${(total_budget - total_spent - total_reserved) / 1_000_000:.6f}")
        console.print()
        console.print(f"  Requests Served:    {total_requests}")
        console.print(f"  Budget Denials:     {budget_denials}")
        console.print(f"  Rate Limit Denials: {rate_denials}")
        console.print(f"  Policy Violations:  {policy_violations}")
        console.print(f"  Processes Killed:   {kills}")
        console.print(f"  Active Processes:   {active_pids}")

        pid = get_daemon_pid()
        if pid:
            try:
                os.kill(pid, 0)
                console.print(f"  Daemon:             [green]Running (PID {pid})[/green]")
            except ProcessLookupError:
                console.print("  Daemon:             [yellow]Stale PID[/yellow]")
        else:
            console.print("  Daemon:             [red]Not running[/red]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


# ── Audit ───────────────────────────────────────────────────────────────────

@app.command("audit")
def audit():
    """Run formal invariant checks on the database."""
    os.environ["AEX_DB_PATH"] = str(DB_PATH)

    if not DB_PATH.exists():
        console.print("[red]Database not found. Run: aex init[/red]")
        raise typer.Exit(1)

    console.print("[bold]AEX Audit — Invariant Verification[/bold]")
    console.print()

    try:
        from ..daemon.utils.invariants import run_all_checks

        with get_db_connection() as conn:
            results = run_all_checks(conn)

        all_passed = True
        for result in results:
            if result.passed:
                console.print(f"  ✅ {result.name}: PASS")
            else:
                console.print(f"  ❌ {result.name}: FAIL")
                if result.detail:
                    console.print(f"     {result.detail}")
                all_passed = False

        console.print()
        if all_passed:
            console.print(f"[green]All {len(results)} invariant checks passed.[/green]")
        else:
            failed = sum(1 for r in results if not r.passed)
            console.print(f"[red]{failed}/{len(results)} invariant check(s) FAILED.[/red]")
            raise typer.Exit(1)

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Audit error: {e}[/red]")
        raise typer.Exit(1)
