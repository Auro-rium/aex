"""Tool plugin commands: install, list, enable/disable."""

import typer
from rich.table import Table

from . import plugin_app, console
from ..daemon.sandbox import install_plugin, list_plugins, set_plugin_enabled


@plugin_app.command("install")
def install(manifest: str, package: str):
    """Install (register) a tool plugin by manifest + package path."""
    try:
        info = install_plugin(manifest, package)
        console.print(
            f"[green]Installed plugin {info['name']} v{info['version']} ({info['sha256'][:12]}...)[/green]"
        )
        console.print("Use 'aex plugin enable <name>' to activate")
    except Exception as e:
        console.print(f"[red]Plugin install failed: {e}[/red]")


@plugin_app.command("list")
def list_all():
    """List registered tool plugins."""
    try:
        rows = list_plugins()
        table = Table(title="AEX Tool Plugins")
        table.add_column("Name")
        table.add_column("Version")
        table.add_column("Enabled")
        table.add_column("Entrypoint")
        table.add_column("Created")

        for r in rows:
            table.add_row(
                r["name"],
                r["version"],
                "yes" if r.get("enabled") else "no",
                r["entrypoint"],
                r.get("created_at") or "N/A",
            )
        console.print(table)
    except Exception as e:
        console.print(f"[red]Failed to list plugins: {e}[/red]")


@plugin_app.command("enable")
def enable(name: str):
    """Enable a registered plugin."""
    try:
        set_plugin_enabled(name, True)
        console.print(f"[green]Enabled plugin '{name}'[/green]")
    except Exception as e:
        console.print(f"[red]Failed to enable plugin: {e}[/red]")


@plugin_app.command("disable")
def disable(name: str):
    """Disable a registered plugin."""
    try:
        set_plugin_enabled(name, False)
        console.print(f"[green]Disabled plugin '{name}'[/green]")
    except Exception as e:
        console.print(f"[red]Failed to disable plugin: {e}[/red]")
