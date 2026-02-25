"""Migration/snapshot/rollback commands for PostgreSQL-backed AEX."""

from __future__ import annotations

from datetime import datetime, UTC
import re

import typer

from . import console, migrate_app
from ..daemon.db import get_db_connection, init_db


_SNAPSHOT_SCHEMA = "aex_backup"
_MIGRATION_TABLES = (
    "webhook_deliveries",
    "webhook_subscriptions",
    "memberships",
    "users",
    "quota_limits",
    "budgets",
    "rate_windows",
    "pids",
    "event_log",
    "reservations",
    "executions",
    "events",
    "tool_plugins",
    "agents",
    "projects",
    "tenants",
)


def _safe_tag(value: str) -> str:
    tag = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()
    if not tag:
        raise typer.BadParameter("snapshot tag cannot be empty")
    if not re.match(r"^[a-z][a-z0-9_]{2,62}$", tag):
        raise typer.BadParameter("tag must match: ^[a-z][a-z0-9_]{2,62}$")
    return tag


def _default_tag(prefix: str = "snap") -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return _safe_tag(f"{prefix}_{ts}")


def _table_exists(conn, schema: str, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = ? AND table_name = ?
        """,
        (schema, table),
    ).fetchone()
    return bool(row)


def _snapshot_table_name(table: str, tag: str) -> str:
    return f"{table}__{tag}"


def _reset_sequences(conn) -> None:
    """Align sequence nextvals with restored table contents."""
    for table in _MIGRATION_TABLES:
        seq_rows = conn.execute(
            f"""
            SELECT
              a.attname AS column_name,
              pg_get_serial_sequence('public."{table}"', a.attname) AS seq_name
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = ?
              AND a.attnum > 0
              AND NOT a.attisdropped
            """,
            (table,),
        ).fetchall()

        for row in seq_rows:
            seq_name = row["seq_name"]
            col = row["column_name"]
            if not seq_name:
                continue
            max_row = conn.execute(
                f'SELECT COALESCE(MAX("{col}"), 0) AS v FROM public."{table}"'
            ).fetchone()
            next_val = int(max_row["v"] or 0) + 1
            conn.execute("SELECT setval(?::regclass, ?, false)", (seq_name, next_val))


def _create_snapshot(conn, tag: str) -> None:
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_SNAPSHOT_SCHEMA}")
    for table in _MIGRATION_TABLES:
        snap_table = _snapshot_table_name(table, tag)
        conn.execute(f'DROP TABLE IF EXISTS {_SNAPSHOT_SCHEMA}."{snap_table}"')
        conn.execute(
            f'CREATE TABLE {_SNAPSHOT_SCHEMA}."{snap_table}" AS TABLE public."{table}"'
        )


@migrate_app.command("snapshot")
def create_snapshot(
    tag: str = typer.Option("", "--tag", help="Snapshot tag (defaults to timestamp)."),
):
    """Create a full runtime snapshot under schema `aex_backup`."""
    final_tag = _safe_tag(tag) if tag else _default_tag("snap")
    try:
        with get_db_connection() as conn:
            conn.execute("BEGIN")
            _create_snapshot(conn, final_tag)
            conn.commit()
        console.print(f"[green]Snapshot created:[/green] {_SNAPSHOT_SCHEMA}/*__{final_tag}")
    except Exception as exc:
        console.print(f"[red]Snapshot failed:[/red] {exc}")
        raise typer.Exit(1)


@migrate_app.command("apply")
def apply_migrations(
    snapshot_first: bool = typer.Option(
        True,
        "--snapshot-first/--no-snapshot-first",
        help="Create snapshot before applying init/migrations.",
    ),
    tag: str = typer.Option("", "--tag", help="Optional snapshot tag when --snapshot-first is set."),
):
    """Apply idempotent schema initialization/migrations with optional pre-snapshot."""
    snap_tag = _safe_tag(tag) if tag else _default_tag("pre_migrate")
    try:
        if snapshot_first:
            with get_db_connection() as conn:
                conn.execute("BEGIN")
                _create_snapshot(conn, snap_tag)
                conn.commit()
            console.print(f"[green]Pre-migration snapshot:[/green] {_SNAPSHOT_SCHEMA}/*__{snap_tag}")

        init_db()
        console.print("[green]Migrations applied successfully.[/green]")
    except Exception as exc:
        console.print(f"[red]Migration apply failed:[/red] {exc}")
        raise typer.Exit(1)


@migrate_app.command("rollback")
def rollback_snapshot(
    tag: str = typer.Option(..., "--tag", help="Snapshot tag to restore from."),
):
    """Rollback runtime tables to a previous snapshot tag."""
    final_tag = _safe_tag(tag)
    try:
        with get_db_connection() as conn:
            for table in _MIGRATION_TABLES:
                snap_table = _snapshot_table_name(table, final_tag)
                if not _table_exists(conn, _SNAPSHOT_SCHEMA, snap_table):
                    raise RuntimeError(f"Snapshot table not found: {_SNAPSHOT_SCHEMA}.{snap_table}")

            conn.execute("BEGIN")
            table_list = ", ".join(f'public."{t}"' for t in _MIGRATION_TABLES)
            conn.execute(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE")

            for table in reversed(_MIGRATION_TABLES):
                snap_table = _snapshot_table_name(table, final_tag)
                conn.execute(
                    f'INSERT INTO public."{table}" SELECT * FROM {_SNAPSHOT_SCHEMA}."{snap_table}"'
                )

            _reset_sequences(conn)
            conn.commit()
        console.print(f"[green]Rollback complete from tag:[/green] {final_tag}")
    except Exception as exc:
        console.print(f"[red]Rollback failed:[/red] {exc}")
        raise typer.Exit(1)
