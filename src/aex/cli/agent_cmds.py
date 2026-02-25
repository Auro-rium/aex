"""Agent CRUD commands: create, inspect, delete, rotate-token, list."""

import os
import signal
import secrets
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import typer
from rich.table import Table

from . import agent_app, console
from ..daemon.db import get_db_connection
from ..daemon.auth import hash_token
from ..daemon.control.lifecycle import transition_agent_state


@agent_app.command("create")
def create_agent(
    name: str,
    budget: float,
    rpm: int,
    tenant_id: str = typer.Option("default", "--tenant-id", help="Tenant scope ID"),
    project_id: str = typer.Option("default", "--project-id", help="Project scope ID"),
    allowed_models: Optional[str] = typer.Option(None, "--allowed-models", help="Comma-separated list of allowed model names"),
    max_input_tokens: Optional[int] = typer.Option(None, "--max-input-tokens", help="Max input tokens per request"),
    max_output_tokens: Optional[int] = typer.Option(None, "--max-output-tokens", help="Max output tokens per request"),
    max_tokens_per_request: Optional[int] = typer.Option(None, "--max-tokens-per-request", help="Total max tokens (input + output) per request"),
    max_tokens_per_minute: Optional[int] = typer.Option(None, "--max-tokens-per-minute", help="Max tokens allowed per minute (TPM)"),
    no_streaming: bool = typer.Option(False, "--no-streaming", help="Disable streaming for this agent"),
    no_tools: bool = typer.Option(False, "--no-tools", help="Disable tool usage for this agent"),
    allowed_tool_names: Optional[str] = typer.Option(None, "--allowed-tool-names", help="Comma-separated list of allowed tool names"),
    no_function_calling: bool = typer.Option(False, "--no-function-calling", help="Disable function calling"),
    allow_vision: bool = typer.Option(False, "--allow-vision", help="Allow vision (image inputs)"),
    strict: bool = typer.Option(False, "--strict", help="Enable strict mode"),
    ttl: Optional[float] = typer.Option(None, "--ttl", help="Token time-to-live in hours. Supports fractional hours (e.g., 0.001 for ~3.6 seconds)"),
    scope: str = typer.Option("execution", "--scope", help="Token scope: 'execution' or 'read-only'"),
    allow_passthrough: bool = typer.Option(False, "--allow-passthrough", help="Allow agent to use own provider API key"),
):
    """Create a new agent with budget (USD) and RPM limit."""

    if scope not in ("execution", "read-only"):
        console.print("[red]Error: --scope must be 'execution' or 'read-only'[/red]")
        raise typer.Exit(1)

    budget_micro = int(budget * 1_000_000)
    token = secrets.token_hex(16)
    token_sha = hash_token(token)

    # Compute expiry
    expires_at = None
    if ttl is not None:
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=ttl)).isoformat()

    allowed_models_json = None
    if allowed_models:
        allowed_models_json = json.dumps([m.strip() for m in allowed_models.split(",")])

    allowed_tools_json = None
    if allowed_tool_names:
        allowed_tools_json = json.dumps([t.strip() for t in allowed_tool_names.split(",")])

    tenant_id = tenant_id.strip() or "default"
    project_id = project_id.strip() or "default"

    try:
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO tenants (tenant_id, name, slug, status)
                VALUES (?, ?, ?, 'ACTIVE')
                ON CONFLICT(tenant_id) DO NOTHING
                """,
                (tenant_id, f"Tenant {tenant_id}", tenant_id),
            )
            conn.execute(
                """
                INSERT INTO projects (project_id, tenant_id, name, slug, status)
                VALUES (?, ?, ?, ?, 'ACTIVE')
                ON CONFLICT(project_id) DO NOTHING
                """,
                (project_id, tenant_id, f"Project {project_id}", project_id),
            )
            conn.execute(
                """INSERT INTO agents (
                    name, tenant_id, project_id, api_token, budget_micro, rpm_limit,
                    allowed_models, max_input_tokens, max_output_tokens,
                    max_tokens_per_request, max_tokens_per_minute,
                    allow_streaming, allow_tools, allowed_tool_names,
                    allow_function_calling, allow_vision, strict_mode,
                    token_hash, token_expires_at, token_scope,
                    allow_passthrough
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    name, tenant_id, project_id, token, budget_micro, rpm,
                    allowed_models_json, max_input_tokens, max_output_tokens,
                    max_tokens_per_request, max_tokens_per_minute,
                    0 if no_streaming else 1,
                    0 if no_tools else 1,
                    allowed_tools_json,
                    0 if no_function_calling else 1,
                    1 if allow_vision else 0,
                    1 if strict else 0,
                    token_sha, expires_at, scope,
                    1 if allow_passthrough else 0,
                ),
            )
            conn.commit()
        console.print(f"[green]Agent '{name}' created.[/green]")
        console.print(f"Scope: tenant={tenant_id} project={project_id}")
        console.print(f"Token: [bold]{token}[/bold]")
        console.print(f"Budget: ${budget:.2f} ({budget_micro} micro)")

        caps = []
        if no_streaming:
            caps.append("streaming=OFF")
        if no_tools:
            caps.append("tools=OFF")
        if no_function_calling:
            caps.append("function_calling=OFF")
        if allow_vision:
            caps.append("vision=ON")
        if strict:
            caps.append("STRICT MODE")
        if allowed_models:
            caps.append(f"models={allowed_models}")
        if max_input_tokens:
            caps.append(f"max_input={max_input_tokens}")
        if max_output_tokens:
            caps.append(f"max_output={max_output_tokens}")
        if caps:
            console.print(f"Capabilities: {', '.join(caps)}")

        if ttl:
            console.print(f"Token expires: {expires_at}")
        if scope != "execution":
            console.print(f"Scope: {scope}")
        if allow_passthrough:
            console.print("Passthrough: ENABLED")

    except Exception as e:
        console.print(f"[red]Error creating agent: {e}[/red]")
        raise typer.Exit(1)


@agent_app.command("inspect")
def inspect_agent(name: str):
    """Get agent details including token (sensitive)."""
    try:
        with get_db_connection() as conn:
            row = conn.execute("SELECT * FROM agents WHERE name = ?", (name,)).fetchone()

        if not row:
            console.print(f"[red]Agent '{name}' not found[/red]")
            raise typer.Exit(1)

        d = dict(row)
        budget_usd = d["budget_micro"] / 1_000_000
        spent_usd = d["spent_micro"] / 1_000_000
        reserved_usd = d["reserved_micro"] / 1_000_000
        remaining_usd = budget_usd - spent_usd - reserved_usd

        console.print(f"[bold]Agent: {d['name']}[/bold]")
        console.print(f"  Tenant:    {d.get('tenant_id', 'default')}")
        console.print(f"  Project:   {d.get('project_id', 'default')}")
        console.print(f"  Budget:    ${budget_usd:.6f}  ({d['budget_micro']} µ)")
        console.print(f"  Spent:     ${spent_usd:.6f}  ({d['spent_micro']} µ)")
        console.print(f"  Reserved:  ${reserved_usd:.6f}  ({d['reserved_micro']} µ)")
        console.print(f"  Remaining: ${remaining_usd:.6f}")
        console.print(f"  RPM Limit: {d['rpm_limit']}")
        console.print(f"  Last:      {d['last_activity'] or 'N/A'}")
        console.print(f"  State:     {d.get('lifecycle_state', 'READY')}")
        if d.get("lifecycle_reason"):
            console.print(f"  Reason:    {d['lifecycle_reason']}")

        console.print()
        console.print("[bold]Capabilities:[/bold]")
        console.print(f"  Streaming:         {'✅' if d.get('allow_streaming', 1) else '❌'}")
        console.print(f"  Tools:             {'✅' if d.get('allow_tools', 1) else '❌'}")
        console.print(f"  Function Calling:  {'✅' if d.get('allow_function_calling', 1) else '❌'}")
        console.print(f"  Vision:            {'✅' if d.get('allow_vision', 0) else '❌'}")
        console.print(f"  Strict Mode:       {'🔒 ON' if d.get('strict_mode', 0) else 'OFF'}")
        console.print(f"  Passthrough:       {'✅' if d.get('allow_passthrough', 0) else '❌'}")

        if d.get("allowed_models"):
            console.print(f"  Allowed Models:    {d['allowed_models']}")
        else:
            console.print("  Allowed Models:    ALL")

        if d.get("max_input_tokens"):
            console.print(f"  Max Input Tokens:  {d['max_input_tokens']}")
        if d.get("max_output_tokens"):
            console.print(f"  Max Output Tokens: {d['max_output_tokens']}")
        if d.get("max_tokens_per_request"):
            console.print(f"  Max Tok/Request:   {d['max_tokens_per_request']}")
        if d.get("max_tokens_per_minute"):
            console.print(f"  Max Tok/Minute:    {d['max_tokens_per_minute']}")
        
        console.print(f"  Used (Prompt):     {d.get('tokens_used_prompt', 0)}")
        console.print(f"  Used (Output):     {d.get('tokens_used_completion', 0)}")
        
        if d.get("allowed_tool_names"):
            console.print(f"  Allowed Tools:     {d['allowed_tool_names']}")

        console.print()
        console.print("[bold]Auth:[/bold]")
        console.print(f"  Scope:    {d.get('token_scope', 'execution')}")
        expires = d.get("token_expires_at")
        if expires:
            console.print(f"  Expires:  {expires}")
        else:
            console.print("  Expires:  Never")
        console.print(f"  Hashed:   {'✅' if d.get('token_hash') else '❌ (legacy)'}")

        console.print()
        console.print(f"[yellow]⚠ Token (sensitive): {d['api_token']}[/yellow]")

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error inspecting agent: {e}[/red]")


@agent_app.command("delete")
def delete_agent(name: str):
    """Delete an agent, kill its process, and remove reservations."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            row = cursor.execute("SELECT name FROM agents WHERE name = ?", (name,)).fetchone()
            if not row:
                console.print(f"[red]Agent '{name}' not found[/red]")
                raise typer.Exit(1)

            pid_row = cursor.execute("SELECT pid FROM pids WHERE agent = ?", (name,)).fetchone()
            if pid_row:
                try:
                    os.kill(pid_row["pid"], signal.SIGTERM)
                    console.print(f"[yellow]Killed process PID {pid_row['pid']}[/yellow]")
                except ProcessLookupError:
                    pass
                cursor.execute("DELETE FROM pids WHERE agent = ?", (name,))

            cursor.execute(
                "INSERT INTO events (agent, action, cost_micro, metadata) VALUES (?, ?, ?, ?)",
                (name, "AGENT_DELETED", 0, "Deleted by operator"),
            )

            cursor.execute("DELETE FROM rate_windows WHERE agent = ?", (name,))
            cursor.execute("DELETE FROM agents WHERE name = ?", (name,))
            conn.commit()

        console.print(f"[green]Agent '{name}' deleted.[/green]")
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error deleting agent: {e}[/red]")


@agent_app.command("rotate-token")
def rotate_token(
    name: str,
    ttl: Optional[float] = typer.Option(None, "--ttl", help="New token TTL in hours. Supports fractional hours (e.g., 0.001 for ~3.6 seconds)"),
):
    """Rotate an agent's API token (invalidates old token)."""
    new_token = secrets.token_hex(16)
    new_hash = hash_token(new_token)

    expires_at = None
    if ttl is not None:
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=ttl)).isoformat()

    try:
        with get_db_connection() as conn:
            cursor = conn.execute(
                "UPDATE agents SET api_token = ?, token_hash = ?, token_expires_at = ? WHERE name = ?",
                (new_token, new_hash, expires_at, name),
            )
            if cursor.rowcount == 0:
                console.print(f"[red]Agent '{name}' not found[/red]")
                raise typer.Exit(1)
            conn.commit()

        console.print(f"[green]Token rotated for agent '{name}'.[/green]")
        console.print(f"New Token: [bold]{new_token}[/bold]")
        if expires_at:
            console.print(f"Expires: {expires_at}")
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error rotating token: {e}[/red]")


@agent_app.command("list")
def list_agents(verbose: bool = typer.Option(False, "--verbose", "-v", help="Show raw micro-units")):
    """List all agents."""
    try:
        with get_db_connection() as conn:
            agents = conn.execute("SELECT * FROM agents").fetchall()

        table = Table(title="AEX Agents")
        table.add_column("Name")
        table.add_column("Tenant")
        table.add_column("Project")
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
        table.add_column("State")
        table.add_column("Scope")
        table.add_column("Caps")
        table.add_column("Last Activity")

        for row in agents:
            agent = dict(row)
            budget_micro = agent["budget_micro"]
            spent_micro = agent["spent_micro"]
            reserved_micro = agent["reserved_micro"]
            remaining_micro = budget_micro - spent_micro - reserved_micro

            caps = []
            if not agent.get("allow_streaming", 1):
                caps.append("!stream")
            if not agent.get("allow_tools", 1):
                caps.append("!tools")
            if agent.get("strict_mode", 0):
                caps.append("STRICT")
            if agent.get("allow_passthrough", 0):
                caps.append("PT")
            caps_str = " ".join(caps) if caps else "—"

            scope = agent.get("token_scope", "exec")
            scope_short = "RO" if scope == "read-only" else "exec"

            if verbose:
                table.add_row(
                    agent["name"],
                    agent.get("tenant_id", "default"),
                    agent.get("project_id", "default"),
                    str(budget_micro),
                    str(spent_micro),
                    str(reserved_micro),
                    str(remaining_micro),
                    str(agent["rpm_limit"]),
                    agent.get("lifecycle_state", "READY"),
                    scope_short,
                    caps_str,
                    agent["last_activity"] or "N/A",
                )
            else:
                table.add_row(
                    agent["name"],
                    agent.get("tenant_id", "default"),
                    agent.get("project_id", "default"),
                    f"{budget_micro / 1_000_000:.6f}",
                    f"{spent_micro / 1_000_000:.6f}",
                    f"{remaining_micro / 1_000_000:.6f}",
                    str(agent["rpm_limit"]),
                    agent.get("lifecycle_state", "READY"),
                    scope_short,
                    caps_str,
                    agent["last_activity"] or "N/A",
                )

        console.print(table)
    except Exception as e:
        console.print(f"[red]Error listing agents: {e}[/red]")


@agent_app.command("state")
def set_agent_state(
    name: str,
    to_state: str,
    reason: str = typer.Option("operator transition", "--reason", help="Reason code for transition"),
):
    """Transition agent lifecycle state (enforced FSM)."""
    try:
        t = transition_agent_state(name, to_state, reason)
        console.print(
            f"[green]Agent '{t.agent}' transitioned {t.from_state} -> {t.to_state}[/green]"
        )
    except Exception as e:
        console.print(f"[red]Failed to transition state: {e}[/red]")
