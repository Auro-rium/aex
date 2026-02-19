import typer
import os
import signal
import sys
import subprocess
import secrets
import sqlite3
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.table import Table
from dotenv import load_dotenv

from . import __version__
from .daemon.db import get_db_connection, init_db
from .daemon.supervisor import register_process

app = typer.Typer(help="AEX - AI Execution Control Plane")
console = Console()
daemon_app = typer.Typer()
agent_app = typer.Typer()
models_app = typer.Typer()

app.add_typer(daemon_app, name="daemon", help="Manage the AEX daemon process")
app.add_typer(agent_app, name="agent", help="Manage AI agents and budgets")
app.add_typer(models_app, name="models", help="Manage model configuration")

# Constants
AEX_DIR = Path.home() / ".aex"
PID_FILE = AEX_DIR / "aex.pid"
DB_PATH = AEX_DIR / "aex.db"
LOG_DIR = AEX_DIR / "logs"
CONFIG_DIR = AEX_DIR / "config"
MODELS_CONFIG_FILE = CONFIG_DIR / "models.yaml"

def get_daemon_pid():
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text().strip())
        except ValueError:
            return None
    return None

@app.command("init")
def init_aex():
    """Initialize AEX environment in ~/.aex"""
    console.print(f"[bold]Initializing AEX in {AEX_DIR}...[/bold]")
    
    # Create directories
    AEX_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    
    # Create valid models.yaml (v1 schema)
    if not MODELS_CONFIG_FILE.exists():
        console.print("Creating default models.yaml...")
        default_yaml = """version: 1

providers:
  groq:
    base_url: https://api.groq.com/openai/v1
    type: openai_compatible

default_model: gpt-oss-20b

models:
  gpt-oss-20b:
    provider: groq
    provider_model: llama-3.1-8b-instant
    pricing:
      input_micro: 50
      output_micro: 100
    limits:
      max_tokens: 8192
    capabilities:
      reasoning: true
      tools: true
      vision: false
"""
        MODELS_CONFIG_FILE.write_text(default_yaml)
    
    # Initialize DB
    os.environ["AEX_DB_PATH"] = str(DB_PATH)
    try:
        init_db()
        console.print("[green]Database initialized.[/green]")
    except Exception as e:
        console.print(f"[red]Failed to initialize database: {e}[/red]")

    console.print(f"[green]AEX initialized successfully.[/green]")
    
    # Create .env template for provider keys
    env_file = AEX_DIR / ".env"
    if not env_file.exists():
        env_file.write_text("# Provider API Keys\nGROQ_API_KEY=\n")
        console.print(f"[yellow]Set your provider API key in {env_file}[/yellow]")

# --- Daemon Commands ---

@daemon_app.command("start")
def start_daemon(port: int = 9000, reload: bool = False):
    """Start the AEX daemon."""
    if not AEX_DIR.exists():
        console.print("[yellow]AEX not initialized. Running init...[/yellow]")
        init_aex()
    
    pid = get_daemon_pid()
    if pid:
        try:
            os.kill(pid, 0)
            console.print(f"[red]Daemon already running (PID {pid})[/red]")
            return
        except ProcessLookupError:
            console.print("[yellow]Stale PID file found, removing...[/yellow]")
            PID_FILE.unlink()

    console.print(f"[green]Starting AEX daemon on port {port}...[/green]")
    
    env = os.environ.copy()
    env["AEX_DB_PATH"] = str(DB_PATH)
    env["AEX_LOG_DIR"] = str(LOG_DIR)
    env["AEX_CONFIG_DIR"] = str(CONFIG_DIR)
    
    cmd = [sys.executable, "-m", "uvicorn", "aex.daemon.app:app", "--host", "127.0.0.1", "--port", str(port)]
    if reload:
        cmd.append("--reload")
        
    log_file = open(LOG_DIR / "daemon.out", "a")
    proc = subprocess.Popen(cmd, env=env, stdout=log_file, stderr=subprocess.STDOUT)
    
    PID_FILE.write_text(str(proc.pid))
        
    console.print(f"Daemon started with PID {proc.pid}")
    console.print(f"Logs: {LOG_DIR}/daemon.out")

@daemon_app.command("stop")
def stop_daemon():
    """Stop the AEX daemon."""
    pid = get_daemon_pid()
    if not pid:
        console.print("[red]Daemon not running (PID file not found)[/red]")
        return
        
    try:
        os.kill(pid, signal.SIGTERM)
        console.print(f"[green]Stopped daemon (PID {pid})[/green]")
        if PID_FILE.exists():
            PID_FILE.unlink()
    except ProcessLookupError:
        console.print("[yellow]Daemon process not found, cleaning up PID file[/yellow]")
        if PID_FILE.exists():
            PID_FILE.unlink()

@daemon_app.command("status")
def status_daemon():
    """Check daemon status."""
    pid = get_daemon_pid()
    if pid:
        try:
            os.kill(pid, 0)
            console.print(f"[green]Daemon is running (PID {pid})[/green]")
            console.print(f"Configuration: {CONFIG_DIR}")
            console.print(f"Database: {DB_PATH}")
            return
        except ProcessLookupError:
            pass
            
    console.print("[red]Daemon is NOT running[/red]")

# --- Agent Commands ---

@agent_app.command("create")
def create_agent(name: str, budget: float, rpm: int):
    """Create a new agent with budget (USD) and RPM limit."""
    os.environ["AEX_DB_PATH"] = str(DB_PATH)
    
    budget_micro = int(budget * 1_000_000)
    token = secrets.token_hex(16)
    
    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO agents (name, api_token, budget_micro, rpm_limit) VALUES (?, ?, ?, ?)",
                (name, token, budget_micro, rpm)
            )
            conn.commit()
        console.print(f"[green]Agent '{name}' created.[/green]")
        console.print(f"Token: [bold]{token}[/bold]")
        console.print(f"Budget: ${budget:.2f} ({budget_micro} micro)")
    except Exception as e:
        console.print(f"[red]Error creating agent: {e}[/red]")

@agent_app.command("inspect")
def inspect_agent(name: str):
    """Get agent details including token (sensitive)."""
    os.environ["AEX_DB_PATH"] = str(DB_PATH)
    try:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT name, api_token, budget_micro, spent_micro, reserved_micro, rpm_limit, last_activity FROM agents WHERE name = ?",
                (name,)
            ).fetchone()
        
        if not row:
            console.print(f"[red]Agent '{name}' not found[/red]")
            raise typer.Exit(1)
        
        budget_usd = row["budget_micro"] / 1_000_000
        spent_usd = row["spent_micro"] / 1_000_000
        reserved_usd = row["reserved_micro"] / 1_000_000
        remaining_usd = budget_usd - spent_usd - reserved_usd

        console.print(f"[bold]Agent: {row['name']}[/bold]")
        console.print(f"  Budget:    ${budget_usd:.6f}  ({row['budget_micro']} µ)")
        console.print(f"  Spent:     ${spent_usd:.6f}  ({row['spent_micro']} µ)")
        console.print(f"  Reserved:  ${reserved_usd:.6f}  ({row['reserved_micro']} µ)")
        console.print(f"  Remaining: ${remaining_usd:.6f}")
        console.print(f"  RPM Limit: {row['rpm_limit']}")
        console.print(f"  Last:      {row['last_activity'] or 'N/A'}")
        console.print()
        console.print(f"[yellow]⚠ Token (sensitive): {row['api_token']}[/yellow]")
            
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error inspecting agent: {e}[/red]")

@agent_app.command("delete")
def delete_agent(name: str):
    """Delete an agent, kill its process, and remove reservations."""
    os.environ["AEX_DB_PATH"] = str(DB_PATH)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # Check agent exists
            row = cursor.execute("SELECT name FROM agents WHERE name = ?", (name,)).fetchone()
            if not row:
                console.print(f"[red]Agent '{name}' not found[/red]")
                raise typer.Exit(1)

            # Kill active process if any
            pid_row = cursor.execute("SELECT pid FROM pids WHERE agent = ?", (name,)).fetchone()
            if pid_row:
                try:
                    os.kill(pid_row["pid"], signal.SIGTERM)
                    console.print(f"[yellow]Killed process PID {pid_row['pid']}[/yellow]")
                except ProcessLookupError:
                    pass
                cursor.execute("DELETE FROM pids WHERE agent = ?", (name,))

            # Log deletion event
            cursor.execute(
                "INSERT INTO events (agent, action, cost_micro, metadata) VALUES (?, ?, ?, ?)",
                (name, "AGENT_DELETED", 0, "Deleted by operator")
            )

            # Remove agent (cascading: events stay for audit trail)
            cursor.execute("DELETE FROM rate_windows WHERE agent = ?", (name,))
            cursor.execute("DELETE FROM agents WHERE name = ?", (name,))
            conn.commit()

        console.print(f"[green]Agent '{name}' deleted.[/green]")
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error deleting agent: {e}[/red]")

@agent_app.command("rotate-token")
def rotate_token(name: str):
    """Rotate an agent's API token (invalidates old token)."""
    os.environ["AEX_DB_PATH"] = str(DB_PATH)
    new_token = secrets.token_hex(16)
    try:
        with get_db_connection() as conn:
            cursor = conn.execute("UPDATE agents SET api_token = ? WHERE name = ?", (new_token, name))
            if cursor.rowcount == 0:
                console.print(f"[red]Agent '{name}' not found[/red]")
                raise typer.Exit(1)
            conn.commit()
        
        console.print(f"[green]Token rotated for agent '{name}'.[/green]")
        console.print(f"New Token: [bold]{new_token}[/bold]")
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error rotating token: {e}[/red]")

@agent_app.command("list")
def list_agents(verbose: bool = typer.Option(False, "--verbose", "-v", help="Show raw micro-units")):
    """List all agents."""
    os.environ["AEX_DB_PATH"] = str(DB_PATH)
    try:
        with get_db_connection() as conn:
            agents = conn.execute("SELECT * FROM agents").fetchall()
            
        table = Table(title="AEX Agents")
        table.add_column("Name")
        if verbose:
            table.add_column("Budget (µ)", justify="right")
            table.add_column("Spent (µ)", justify="right")
            table.add_column("Reserved (µ)", justify="right")
            table.add_column("Remaining (µ)", justify="right")
        else:
            table.add_column("Budget ($)", justify="right")
            table.add_column("Spent ($)", justify="right")
            table.add_column("Remaining ($)", justify="right")
        table.add_column("RPM", justify="right")
        table.add_column("Last Activity")
        
        for agent in agents:
            budget_micro = agent["budget_micro"]
            spent_micro = agent["spent_micro"]
            reserved_micro = agent["reserved_micro"]
            remaining_micro = budget_micro - spent_micro - reserved_micro
            
            if verbose:
                table.add_row(
                    agent["name"],
                    str(budget_micro),
                    str(spent_micro),
                    str(reserved_micro),
                    str(remaining_micro),
                    str(agent["rpm_limit"]),
                    agent["last_activity"] or "N/A"
                )
            else:
                table.add_row(
                    agent["name"],
                    f"{budget_micro / 1_000_000:.6f}",
                    f"{spent_micro / 1_000_000:.6f}",
                    f"{remaining_micro / 1_000_000:.6f}",
                    str(agent["rpm_limit"]),
                    agent["last_activity"] or "N/A"
                )
            
        console.print(table)
    except Exception as e:
        console.print(f"[red]Error listing agents: {e}[/red]")

# --- Run Command (GAP 1: --agent flag) ---

@app.command("run")
def run_command(
    agent: str = typer.Option(..., "--agent", "-a", help="Agent name to run as"),
    command: list[str] = typer.Argument(..., help="Command to run")
):
    """
    Run a command as an agent using AEX proxy.
    
    Example: aex run --agent my-agent python script.py
    """
    os.environ["AEX_DB_PATH"] = str(DB_PATH)
    
    # 1. Fetch agent token
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
    
    # 2. Prepare environment
    env = os.environ.copy()
    env["OPENAI_BASE_URL"] = "http://127.0.0.1:9000/v1"
    env["OPENAI_API_KEY"] = token
    env["AEX_AGENT_TOKEN"] = token
    
    # 3. Launch process
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

# --- Metrics Command (GAP 4) ---

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
        
        console.print(f"[bold]AEX Kernel Metrics[/bold]")
        console.print(f"  Agents:           {data['total_agents']}")
        console.print(f"  Total Spent:      ${data['total_spent_global_usd']:.6f}")
        console.print(f"  Active Processes: {data['active_processes']}")
        console.print(f"  Total Requests:   {data['total_requests']}")
        console.print(f"  Denied (Budget):  {data['total_denied_budget']}")
        console.print(f"  Denied (Rate):    {data['total_denied_rate_limit']}")
        console.print()

        table = Table(title="Per-Agent Financials")
        table.add_column("Agent")
        table.add_column("Spent ($)", justify="right")
        table.add_column("Remaining ($)", justify="right")
        table.add_column("RPM", justify="right")
        table.add_column("Last Activity")

        for ag in data["agents"]:
            table.add_row(
                ag["name"],
                f"{ag['spent_usd']:.6f}",
                f"{ag['remaining_usd']:.6f}",
                str(ag["rpm_limit"]),
                ag["last_activity"] or "N/A"
            )
        console.print(table)

    except httpx.ConnectError:
        console.print("[red]Cannot connect to daemon. Is it running?[/red]")
    except Exception as e:
        console.print(f"[red]Failed to fetch metrics: {e}[/red]")

# --- Models Commands ---

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

@app.command("version")
def show_version():
    """Show AEX version."""
    console.print(f"AEX {__version__}")

if __name__ == "__main__":
    app()
