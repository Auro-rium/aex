"""Secure plugin registry and manifest verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from ..db import get_db_connection


class PluginError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def install_plugin(manifest_path: str, package_path: str) -> dict[str, Any]:
    manifest_file = Path(manifest_path)
    package_file = Path(package_path)
    if not manifest_file.exists() or not package_file.exists():
        raise PluginError("Manifest or package path does not exist")

    manifest = yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
    required = ["name", "version", "entrypoint", "sha256"]
    for key in required:
        if key not in manifest:
            raise PluginError(f"Manifest missing key '{key}'")

    observed_sha = _sha256_file(package_file)
    if observed_sha != manifest["sha256"]:
        raise PluginError("Plugin sha256 mismatch")

    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO tool_plugins (name, version, entrypoint, package_path, sha256, manifest_json, enabled)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(name) DO UPDATE SET
                version=excluded.version,
                entrypoint=excluded.entrypoint,
                package_path=excluded.package_path,
                sha256=excluded.sha256,
                manifest_json=excluded.manifest_json
            """,
            (
                manifest["name"],
                manifest["version"],
                manifest["entrypoint"],
                str(package_file),
                observed_sha,
                json.dumps(manifest, ensure_ascii=True),
            ),
        )
        conn.commit()

    return {
        "name": manifest["name"],
        "version": manifest["version"],
        "enabled": False,
        "sha256": observed_sha,
    }


def set_plugin_enabled(name: str, enabled: bool) -> None:
    with get_db_connection() as conn:
        cur = conn.execute(
            "UPDATE tool_plugins SET enabled = ? WHERE name = ?",
            (1 if enabled else 0, name),
        )
        if cur.rowcount == 0:
            raise PluginError(f"Plugin '{name}' not found")
        conn.commit()


def list_plugins() -> list[dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT name, version, entrypoint, package_path, sha256, enabled, created_at FROM tool_plugins ORDER BY name"
        ).fetchall()
    return [dict(r) for r in rows]


def get_enabled_plugin(name: str) -> dict[str, Any]:
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM tool_plugins WHERE name = ? AND enabled = 1",
            (name,),
        ).fetchone()
    if not row:
        raise PluginError(f"Plugin '{name}' is not enabled")
    return dict(row)
