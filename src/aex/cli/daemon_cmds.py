"""Daemon lifecycle commands: start, stop, status."""

import sys
import signal
import subprocess
import os

import typer

from . import daemon_app, console, AEX_DIR, PID_FILE, LOG_DIR, CONFIG_DIR, get_daemon_pid
from ..daemon.db import init_db


@daemon_app.command("start")
def start_daemon(port: int = 9000, reload: bool = False):
    """Start the AEX daemon."""
    if not (os.getenv("AEX_PG_DSN") or "").strip():
        console.print("[red]AEX_PG_DSN is required before starting daemon.[/red]")
        raise typer.Exit(1)

    AEX_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    try:
        init_db()
    except Exception as exc:
        console.print(f"[red]Database init failed, daemon not started: {exc}[/red]")
        raise typer.Exit(1)

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
    env["AEX_LOG_DIR"] = str(LOG_DIR)
    env["AEX_CONFIG_DIR"] = str(CONFIG_DIR)

    cmd = [
        sys.executable, "-m", "uvicorn",
        "aex.daemon.app:app",
        "--host", "127.0.0.1",
        "--port", str(port),
    ]
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
            console.print("Database: PostgreSQL (AEX_PG_DSN)")
            return
        except ProcessLookupError:
            pass

    console.print("[red]Daemon is NOT running[/red]")
