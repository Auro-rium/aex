"""AEX CLI — modular command package."""

import typer
import os
from pathlib import Path
from rich.console import Console

from .. import __version__
from ..daemon.db import get_db_connection, init_db

# ── Shared state ────────────────────────────────────────────────────────────

app = typer.Typer(help="AEX - AI Execution Control Plane")
console = Console()

# Sub-command groups
daemon_app = typer.Typer()
agent_app = typer.Typer()
models_app = typer.Typer()

app.add_typer(daemon_app, name="daemon", help="Manage the AEX daemon process")
app.add_typer(agent_app, name="agent", help="Manage AI agents and budgets")
app.add_typer(models_app, name="models", help="Manage model configuration")

# ── Path constants ──────────────────────────────────────────────────────────

AEX_DIR = Path.home() / ".aex"
PID_FILE = AEX_DIR / "aex.pid"
DB_PATH = AEX_DIR / "aex.db"
LOG_DIR = AEX_DIR / "logs"
CONFIG_DIR = AEX_DIR / "config"
MODELS_CONFIG_FILE = CONFIG_DIR / "models.yaml"


# ── Shared helpers ──────────────────────────────────────────────────────────

def get_daemon_pid():
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text().strip())
        except ValueError:
            return None
    return None


# ── Init command (lives at top level, so defined here) ──────────────────────

@app.command("init")
def init_aex():
    """Initialize AEX environment in ~/.aex"""
    console.print(f"[bold]Initializing AEX in {AEX_DIR}...[/bold]")

    AEX_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

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

    os.environ["AEX_DB_PATH"] = str(DB_PATH)
    try:
        init_db()
        console.print("[green]Database initialized.[/green]")
    except Exception as e:
        console.print(f"[red]Failed to initialize database: {e}[/red]")

    console.print("[green]AEX initialized successfully.[/green]")

    env_file = AEX_DIR / ".env"
    if not env_file.exists():
        env_file.write_text("# Provider API Keys\nGROQ_API_KEY=\n")
        console.print(f"[yellow]Set your provider API key in {env_file}[/yellow]")


# ── Register submodule commands (import triggers decorator registration) ────

from . import daemon_cmds   # noqa: E402, F401
from . import agent_cmds    # noqa: E402, F401
from . import ops_cmds      # noqa: E402, F401
