"""Deterministic policy evaluation pipeline (kernel + plugins)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import importlib.util
import os
from types import ModuleType
from typing import Any

from ..utils.deterministic import canonical_json, stable_hash_hex
from ..utils.logging_config import StructuredLogger
from ..utils.policy_engine import validate_request as validate_request_kernel
from ..utils.policy_engine import validate_response as validate_response_kernel

logger = StructuredLogger(__name__)


@dataclass
class PolicyDecision:
    allow: bool
    reason: str | None
    obligations: list[dict[str, Any]]
    patch: dict[str, Any]
    decision_hash: str
    plugin_trace: list[dict[str, Any]]


def _policy_plugin_dir() -> Path:
    return Path(os.getenv("AEX_POLICY_PLUGIN_DIR", os.path.expanduser("~/.aex/policies")))


def _load_plugins() -> list[tuple[str, ModuleType]]:
    """Load plugins in deterministic lexical order."""
    plugin_dir = _policy_plugin_dir()
    if not plugin_dir.exists() or not plugin_dir.is_dir():
        return []

    loaded = []
    for path in sorted(plugin_dir.glob("*.py")):
        name = path.stem
        spec = importlib.util.spec_from_file_location(f"aex_policy_{name}", path)
        if not spec or not spec.loader:
            continue
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            if hasattr(module, "evaluate"):
                loaded.append((name, module))
            else:
                logger.warning("Policy plugin missing evaluate()", plugin=name)
        except Exception as exc:
            # Fail-safe: a broken plugin is handled in evaluation phase as deny.
            logger.error("Failed to load policy plugin", plugin=name, error=str(exc))
            loaded.append((name, None))
    return loaded


def _build_hash(trace: list[dict[str, Any]], allow: bool, reason: str | None, patch: dict[str, Any]) -> str:
    payload = {
        "allow": allow,
        "reason": reason,
        "trace": trace,
        "patch": patch,
    }
    return stable_hash_hex(canonical_json(payload))


def evaluate_request(
    *,
    agent_caps: dict,
    payload: dict,
    model_name: str,
    endpoint: str,
    execution_id: str,
) -> PolicyDecision:
    """Evaluate kernel rules followed by plugin rules.

    Deterministic reducer:
    - any explicit deny wins
    - otherwise allow
    - patch merge uses lexical plugin order
    """
    plugin_trace: list[dict[str, Any]] = []
    obligations: list[dict[str, Any]] = []
    merged_patch: dict[str, Any] = {}

    kernel_ok, kernel_reason = validate_request_kernel(agent_caps, payload, model_name)
    plugin_trace.append({
        "stage": "kernel",
        "decision": "allow" if kernel_ok else "deny",
        "reason": kernel_reason,
    })
    if not kernel_ok:
        return PolicyDecision(
            allow=False,
            reason=kernel_reason,
            obligations=obligations,
            patch=merged_patch,
            decision_hash=_build_hash(plugin_trace, False, kernel_reason, merged_patch),
            plugin_trace=plugin_trace,
        )

    context = {
        "agent": {"name": agent_caps.get("name"), "scope": agent_caps.get("token_scope", "execution")},
        "caps": agent_caps,
        "request": payload,
        "model": model_name,
        "endpoint": endpoint,
        "execution_id": execution_id,
    }

    for plugin_name, module in _load_plugins():
        if module is None:
            reason = f"Policy plugin '{plugin_name}' failed to load"
            plugin_trace.append({"stage": plugin_name, "decision": "deny", "reason": reason})
            return PolicyDecision(
                allow=False,
                reason=reason,
                obligations=obligations,
                patch=merged_patch,
                decision_hash=_build_hash(plugin_trace, False, reason, merged_patch),
                plugin_trace=plugin_trace,
            )

        try:
            result = module.evaluate(context)
            decision = (result or {}).get("decision", "abstain")
            reason = (result or {}).get("reason")
            patch = (result or {}).get("patch") or {}
            plugin_obligations = (result or {}).get("obligations") or []

            if not isinstance(patch, dict):
                patch = {}
            if not isinstance(plugin_obligations, list):
                plugin_obligations = []

            obligations.extend(plugin_obligations)
            for k in sorted(patch.keys()):
                merged_patch[k] = patch[k]

            plugin_trace.append(
                {
                    "stage": plugin_name,
                    "decision": decision,
                    "reason": reason,
                }
            )

            if decision == "deny":
                deny_reason = reason or f"Denied by plugin '{plugin_name}'"
                return PolicyDecision(
                    allow=False,
                    reason=deny_reason,
                    obligations=obligations,
                    patch=merged_patch,
                    decision_hash=_build_hash(plugin_trace, False, deny_reason, merged_patch),
                    plugin_trace=plugin_trace,
                )
        except Exception as exc:
            reason = f"Policy plugin '{plugin_name}' error"
            plugin_trace.append({"stage": plugin_name, "decision": "deny", "reason": reason})
            logger.error("Policy plugin evaluation failed", plugin=plugin_name, error=str(exc))
            return PolicyDecision(
                allow=False,
                reason=reason,
                obligations=obligations,
                patch=merged_patch,
                decision_hash=_build_hash(plugin_trace, False, reason, merged_patch),
                plugin_trace=plugin_trace,
            )

    return PolicyDecision(
        allow=True,
        reason=None,
        obligations=obligations,
        patch=merged_patch,
        decision_hash=_build_hash(plugin_trace, True, None, merged_patch),
        plugin_trace=plugin_trace,
    )


def evaluate_response(agent_caps: dict, response: dict) -> tuple[bool, str | None]:
    return validate_response_kernel(agent_caps, response)
