"""Tenant/project CRUD commands for v2.1 multi-tenant mode."""

from __future__ import annotations

import typer
from rich.table import Table

from . import console, tenant_app
from ..daemon.db import get_db_connection


@tenant_app.command("create")
def create_tenant(
    tenant_id: str,
    name: str = typer.Option("", "--name", help="Friendly tenant name"),
):
    """Create or upsert a tenant."""
    tenant = tenant_id.strip()
    if not tenant:
        raise typer.BadParameter("tenant_id cannot be empty")
    tenant_name = (name.strip() if name else "") or f"Tenant {tenant}"

    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO tenants (tenant_id, name, slug, status)
            VALUES (?, ?, ?, 'ACTIVE')
            ON CONFLICT(tenant_id) DO UPDATE SET name = excluded.name
            """,
            (tenant, tenant_name, tenant),
        )
        conn.commit()

    console.print(f"[green]Tenant '{tenant}' is ready.[/green]")


@tenant_app.command("list")
def list_tenants():
    """List all tenants."""

    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT tenant_id, name, status, created_at FROM tenants ORDER BY created_at ASC"
        ).fetchall()

    table = Table(title="AEX Tenants")
    table.add_column("Tenant ID")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Created")
    for row in rows:
        table.add_row(str(row["tenant_id"]), str(row["name"]), str(row["status"]), str(row["created_at"]))
    console.print(table)


@tenant_app.command("project-create")
def create_project(
    tenant_id: str,
    project_id: str,
    name: str = typer.Option("", "--name", help="Friendly project name"),
):
    """Create or upsert a project under a tenant."""
    tenant = tenant_id.strip()
    project = project_id.strip()
    if not tenant or not project:
        raise typer.BadParameter("tenant_id and project_id are required")

    project_name = (name.strip() if name else "") or f"Project {project}"
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO tenants (tenant_id, name, slug, status)
            VALUES (?, ?, ?, 'ACTIVE')
            ON CONFLICT(tenant_id) DO NOTHING
            """,
            (tenant, f"Tenant {tenant}", tenant),
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, tenant_id, name, slug, status)
            VALUES (?, ?, ?, ?, 'ACTIVE')
            ON CONFLICT(project_id) DO UPDATE SET
                tenant_id = excluded.tenant_id,
                name = excluded.name,
                slug = excluded.slug
            """,
            (project, tenant, project_name, project),
        )
        conn.commit()

    console.print(f"[green]Project '{project}' created under tenant '{tenant}'.[/green]")


@tenant_app.command("project-list")
def list_projects(
    tenant_id: str = typer.Option("", "--tenant-id", help="Filter by tenant ID"),
):
    """List projects (optionally filtered by tenant)."""

    with get_db_connection() as conn:
        if tenant_id.strip():
            rows = conn.execute(
                """
                SELECT project_id, tenant_id, name, status, created_at
                FROM projects
                WHERE tenant_id = ?
                ORDER BY created_at ASC
                """,
                (tenant_id.strip(),),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT project_id, tenant_id, name, status, created_at
                FROM projects
                ORDER BY created_at ASC
                """
            ).fetchall()

    table = Table(title="AEX Projects")
    table.add_column("Project ID")
    table.add_column("Tenant ID")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Created")
    for row in rows:
        table.add_row(
            str(row["project_id"]),
            str(row["tenant_id"]),
            str(row["name"]),
            str(row["status"]),
            str(row["created_at"]),
        )
    console.print(table)
