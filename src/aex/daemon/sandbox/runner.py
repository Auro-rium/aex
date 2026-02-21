"""Isolated subprocess runner for tool plugins."""

from __future__ import annotations

import json
import os
from pathlib import Path
import resource
import shlex
import shutil
import subprocess
import tempfile

from ..utils.logging_config import StructuredLogger
from .cap_tokens import verify_token
from .plugins import PluginError, get_enabled_plugin

logger = StructuredLogger(__name__)


def _preexec_limits(max_memory_mb: int = 256, max_cpu_seconds: int = 15):
    def _apply():
        os.setsid()
        mem_bytes = max_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (max_cpu_seconds, max_cpu_seconds))
        resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))

    return _apply


def _iter_parent_dirs(path: Path) -> list[Path]:
    parents = []
    cur = path.parent
    while str(cur) not in {"", "/"}:
        parents.append(cur)
        cur = cur.parent
    return list(reversed(parents))


def _bwrap_enabled() -> bool:
    if os.getenv("AEX_SANDBOX_USE_BWRAP", "1") == "0":
        return False
    return shutil.which("bwrap") is not None


def _bwrap_fallback_enabled() -> bool:
    return os.getenv("AEX_SANDBOX_BWRAP_FALLBACK", "1") == "1"


def _build_bwrap_command(*, cmd: list[str], tmp_path: Path, package_path: Path, allowed_fs: list[str], clean_env: dict[str, str], deny_net: bool) -> list[str]:
    bwrap = [
        "bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup-try",
        "--tmpfs",
        "/",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--dir",
        str(tmp_path),
        "--bind",
        str(tmp_path),
        str(tmp_path),
        "--chdir",
        str(tmp_path),
    ]

    if deny_net:
        bwrap.append("--unshare-net")

    # Minimal runtime mounts for typical plugin execution.
    for base in ["/usr", "/bin", "/lib", "/lib64", "/sbin"]:
        p = Path(base)
        if p.exists():
            bwrap.extend(["--ro-bind", base, base])

    ro_bind_paths: list[Path] = [package_path]
    for token_path in allowed_fs:
        p = Path(token_path)
        if p.exists():
            ro_bind_paths.append(p)

    for arg in cmd:
        if arg.startswith("/"):
            p = Path(arg)
            if p.exists():
                ro_bind_paths.append(p)

    seen = set()
    normalized_paths = []
    for path in ro_bind_paths:
        r = path.resolve()
        if r in seen:
            continue
        seen.add(r)
        normalized_paths.append(r)

    for path in normalized_paths:
        for parent in _iter_parent_dirs(path):
            bwrap.extend(["--dir", str(parent)])
        bwrap.extend(["--ro-bind", str(path), str(path)])

    for k, v in clean_env.items():
        bwrap.extend(["--setenv", k, v])

    bwrap.append("--")
    bwrap.extend(cmd)
    return bwrap


def run_plugin_tool(*, plugin_name: str, capability_token: str, input_payload: dict) -> dict:
    """Execute plugin entrypoint with process isolation and capability check."""
    cap = verify_token(capability_token)
    if cap.tool_name != plugin_name:
        raise PluginError("Capability token does not authorize this plugin")

    plugin = get_enabled_plugin(plugin_name)

    args = shlex.split(plugin["entrypoint"])
    if not args:
        raise PluginError("Plugin entrypoint is empty")

    package_path = Path(plugin["package_path"]).resolve()
    if not package_path.exists():
        raise PluginError("Plugin package path no longer exists")

    clean_env = {
        "PATH": os.getenv("PATH", ""),
        "PYTHONUNBUFFERED": "1",
        "AEX_PLUGIN_NAME": plugin_name,
        "AEX_EXECUTION_ID": cap.execution_id,
    }

    with tempfile.TemporaryDirectory(prefix="aex-plugin-") as tmp:
        tmp_path = Path(tmp)
        input_file = tmp_path / "input.json"
        output_file = tmp_path / "output.json"
        input_file.write_text(json.dumps(input_payload, ensure_ascii=True), encoding="utf-8")

        cmd = args + [str(input_file), str(output_file)]
        wrapped_cmd = cmd
        used_bwrap = False

        if _bwrap_enabled():
            wrapped_cmd = _build_bwrap_command(
                cmd=cmd,
                tmp_path=tmp_path,
                package_path=package_path,
                allowed_fs=cap.allowed_fs,
                clean_env=clean_env,
                deny_net=(cap.net_policy == "deny"),
            )
            used_bwrap = True

        try:
            result = subprocess.run(
                wrapped_cmd,
                cwd=str(tmp_path),
                env=clean_env,
                capture_output=True,
                text=True,
                timeout=max(1, cap.ttl_ms // 1000),
                preexec_fn=_preexec_limits(),
            )
        except subprocess.TimeoutExpired as exc:
            logger.warning("Plugin timed out", plugin=plugin_name, timeout=cap.ttl_ms)
            raise PluginError("Plugin execution timed out") from exc

        # Some hosts disable user namespaces; allow controlled fallback to rlimit-only mode.
        if (
            used_bwrap
            and result.returncode != 0
            and "creating new namespace failed" in (result.stderr or "").lower()
            and _bwrap_fallback_enabled()
        ):
            logger.warning("bwrap unavailable, retrying plugin without bwrap", plugin=plugin_name)
            try:
                result = subprocess.run(
                    cmd,
                    cwd=str(tmp_path),
                    env=clean_env,
                    capture_output=True,
                    text=True,
                    timeout=max(1, cap.ttl_ms // 1000),
                    preexec_fn=_preexec_limits(),
                )
            except subprocess.TimeoutExpired as exc:
                logger.warning("Plugin timed out after bwrap fallback", plugin=plugin_name, timeout=cap.ttl_ms)
                raise PluginError("Plugin execution timed out") from exc

        stdout = (result.stdout or "")[: cap.max_output_bytes]
        stderr = (result.stderr or "")[: cap.max_output_bytes]

        if result.returncode != 0:
            logger.warning(
                "Plugin tool execution failed",
                plugin=plugin_name,
                code=result.returncode,
                stderr=stderr[:300],
            )
            raise PluginError(f"Plugin failed with exit code {result.returncode}")

        if not output_file.exists():
            raise PluginError("Plugin did not produce output file")

        raw_output = output_file.read_text(encoding="utf-8")[: cap.max_output_bytes]
        try:
            parsed = json.loads(raw_output)
        except Exception:
            parsed = {"raw": raw_output}

        return {
            "plugin": plugin_name,
            "execution_id": cap.execution_id,
            "result": parsed,
            "stdout": stdout,
            "stderr": stderr,
        }
