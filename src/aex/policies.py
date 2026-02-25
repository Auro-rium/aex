"""Policy primitives for the prod-first AEX flow."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable


_POLICY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class Policy:
    policy_id: str
    budget_usd: float = 50.0
    allow_tools: tuple[str, ...] = ()
    deny_tools: tuple[str, ...] = ()
    max_steps: int = 100
    dangerous_ops: bool = False
    require_approval_for_destructive_ops: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "budget_usd": self.budget_usd,
            "allow_tools": list(self.allow_tools),
            "deny_tools": list(self.deny_tools),
            "max_steps": self.max_steps,
            "dangerous_ops": self.dangerous_ops,
            "require_approval_for_destructive_ops": self.require_approval_for_destructive_ops,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=True, sort_keys=True)


def parse_tool_names(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        iterable: Iterable[str] = values.split(",")
    elif isinstance(values, (list, tuple, set)):
        iterable = values
    else:
        raise ValueError("Tool lists must be a comma-separated string or list")

    normalized = []
    seen = set()
    for raw in iterable:
        name = str(raw).strip().lower()
        if not name:
            continue
        if name in seen:
            continue
        seen.add(name)
        normalized.append(name)
    return tuple(normalized)


def _policy_dir(policy_dir: str | Path | None = None) -> Path:
    if policy_dir is not None:
        return Path(policy_dir)
    env_dir = (os.getenv("AEX_POLICY_DIR") or "").strip()
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".aex" / "policies"


def _require_policy_id(policy_id: str) -> str:
    pid = str(policy_id or "").strip()
    if not _POLICY_ID_RE.match(pid):
        raise ValueError(
            "policy_id must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63}"
        )
    return pid


def policy_from_dict(policy_id: str, payload: dict[str, Any]) -> Policy:
    pid = _require_policy_id(policy_id)
    budget = float(payload.get("budget_usd", 50.0))
    if budget < 0:
        raise ValueError("budget_usd must be >= 0")

    max_steps = int(payload.get("max_steps", 100))
    if max_steps <= 0:
        raise ValueError("max_steps must be >= 1")

    allow_tools = parse_tool_names(payload.get("allow_tools"))
    deny_tools = parse_tool_names(payload.get("deny_tools"))

    overlap = sorted(set(allow_tools).intersection(deny_tools))
    if overlap:
        raise ValueError(f"allow_tools and deny_tools overlap: {', '.join(overlap)}")

    dangerous_ops = bool(payload.get("dangerous_ops", False))
    require_approval = bool(payload.get("require_approval_for_destructive_ops", True))

    return Policy(
        policy_id=pid,
        budget_usd=budget,
        allow_tools=allow_tools,
        deny_tools=deny_tools,
        max_steps=max_steps,
        dangerous_ops=dangerous_ops,
        require_approval_for_destructive_ops=require_approval,
    )


def create_policy(policy_id: str, payload: dict[str, Any], policy_dir: str | Path | None = None) -> Policy:
    policy = policy_from_dict(policy_id, payload)
    root = _policy_dir(policy_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{policy.policy_id}.json"
    path.write_text(
        json.dumps(policy.to_dict(), indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return policy


def load_policy(policy_id: str, policy_dir: str | Path | None = None) -> Policy:
    pid = _require_policy_id(policy_id)
    path = _policy_dir(policy_dir) / f"{pid}.json"
    if not path.exists():
        raise FileNotFoundError(f"Policy not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid policy document: {path}")
    return policy_from_dict(pid, data)


def list_policies(policy_dir: str | Path | None = None) -> list[Policy]:
    root = _policy_dir(policy_dir)
    if not root.exists():
        return []
    items: list[Policy] = []
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                items.append(policy_from_dict(path.stem, data))
        except Exception:
            continue
    return items


def delete_policy(policy_id: str, policy_dir: str | Path | None = None) -> bool:
    pid = _require_policy_id(policy_id)
    path = _policy_dir(policy_dir) / f"{pid}.json"
    if not path.exists():
        return False
    path.unlink()
    return True
