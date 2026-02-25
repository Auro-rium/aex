"""Replay/audit commands for deterministic ledger verification."""
import typer

from . import app, console
from ..daemon.ledger import replay_ledger_balances, verify_hash_chain
from ..daemon.db import get_db_connection


@app.command("replay")
def replay(
    execution_id: str = typer.Option("", "--execution", help="Replay a specific execution id"),
    verify: bool = typer.Option(False, "--verify", help="Verify hash chain and replayed balances"),
):
    """Run deterministic replay checks."""
    if execution_id:
        try:
            with get_db_connection() as conn:
                row = conn.execute(
                    "SELECT execution_id, agent, endpoint, state, status_code, created_at, terminal_at FROM executions WHERE execution_id = ?",
                    (execution_id,),
                ).fetchone()
            if not row:
                console.print(f"[red]Execution '{execution_id}' not found[/red]")
                raise typer.Exit(1)

            console.print("[bold]Execution Replay[/bold]")
            console.print(f"  Execution: {row['execution_id']}")
            console.print(f"  Agent:     {row['agent']}")
            console.print(f"  Endpoint:  {row['endpoint']}")
            console.print(f"  State:     {row['state']}")
            console.print(f"  Status:    {row['status_code']}")
            console.print(f"  Created:   {row['created_at']}")
            console.print(f"  Terminal:  {row['terminal_at'] or 'N/A'}")
        except typer.Exit:
            raise
        except Exception as e:
            console.print(f"[red]Replay failed: {e}[/red]")
            raise typer.Exit(1)
        return

    if not verify:
        verify = True

    chain = verify_hash_chain() if verify else None
    replay = replay_ledger_balances() if verify else None

    console.print("[bold]Ledger Replay Audit[/bold]")
    if chain:
        console.print(f"  Hash chain: {'PASS' if chain.ok else 'FAIL'} - {chain.detail}")
    if replay:
        console.print(f"  Balances:   {'PASS' if replay.ok else 'FAIL'} - {replay.detail}")

    if chain and replay and (not chain.ok or not replay.ok):
        raise typer.Exit(1)
