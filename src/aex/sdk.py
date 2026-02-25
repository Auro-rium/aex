"""Developer-facing SDK helpers for the prod-first flow."""

from __future__ import annotations

import importlib
import inspect
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlparse
from typing import Any, Mapping

from .policies import Policy, create_policy, load_policy, policy_from_dict

_PATCH_LOCK = threading.Lock()
_PATCHED_SENTINEL = "_aex_monkey_patched"
_PROFILE_ENV = "AEX_PROFILE_PATH"
_PROFILE_DEFAULT = Path.home() / ".aex" / "sdk_profile.json"


def _profile_path() -> Path:
    raw = (os.getenv(_PROFILE_ENV) or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _PROFILE_DEFAULT


def _load_profile() -> dict[str, str]:
    path = _profile_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for key in ("api_key", "base_url", "tenant", "project", "mode"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def _save_profile(data: Mapping[str, str]) -> Path:
    path = _profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "api_key": data.get("api_key", ""),
        "base_url": data.get("base_url", ""),
        "tenant": data.get("tenant", ""),
        "project": data.get("project", ""),
        "mode": data.get("mode", "proxy"),
    }
    text = json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(text)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return path


def _normalize_base_url(base_url: str | None) -> str:
    profile = _load_profile()
    raw = (base_url or os.getenv("AEX_BASE_URL") or profile.get("base_url") or "http://127.0.0.1:9000").strip().rstrip("/")
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("AEX base URL must be an absolute URL, e.g. https://aex-cloud.app")
    if raw.endswith("/v1"):
        return raw
    return f"{raw}/v1"


def _resolve_api_key(api_key: str | None = None) -> str:
    profile = _load_profile()
    candidates = (
        api_key,
        os.getenv("AEX_API_KEY"),
        os.getenv("AEX_AGENT_TOKEN"),
        os.getenv("OPENAI_API_KEY"),
        profile.get("api_key"),
    )
    for candidate in candidates:
        token = (candidate or "").strip()
        if token:
            return token
    return ""


def _patch_client_init(module_name: str, class_name: str, base_url: str) -> None:
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return
    cls = getattr(module, class_name, None)
    if cls is None:
        return
    if getattr(cls, _PATCHED_SENTINEL, False):
        return

    original_init = cls.__init__

    def _patched_init(self, *args, **kwargs):
        kwargs.setdefault("base_url", base_url)
        if kwargs.get("api_key") in (None, ""):
            token = (os.getenv("AEX_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
            if token:
                kwargs["api_key"] = token
        return original_init(self, *args, **kwargs)

    cls.__init__ = _patched_init
    setattr(cls, _PATCHED_SENTINEL, True)


def _install_monkey_patches(base_url: str) -> None:
    with _PATCH_LOCK:
        _patch_client_init("openai", "OpenAI", base_url)
        _patch_client_init("groq", "Groq", base_url)


class WrappedAgent:
    """Small adapter that injects AEX policy/runtime context before execution."""

    def __init__(self, agent: Any, policy: Policy | None = None, runtime: Mapping[str, str] | None = None):
        self._agent = agent
        self._policy = policy
        self._runtime = dict(runtime or {})

    def _apply_runtime_context(self) -> None:
        api_key = (self._runtime.get("AEX_API_KEY") or _resolve_api_key()).strip()
        if not api_key:
            raise RuntimeError("Missing AEX API key. Use wrap(agent, api_key='...') or set AEX_API_KEY.")

        os.environ.setdefault("AEX_AGENT_TOKEN", api_key)
        os.environ.setdefault("OPENAI_API_KEY", api_key)
        base_url = (self._runtime.get("OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").strip()
        if not base_url:
            base_url = _normalize_base_url(os.getenv("AEX_BASE_URL"))
        os.environ.setdefault("OPENAI_BASE_URL", base_url)
        if self._runtime.get("AEX_TENANT"):
            os.environ.setdefault("AEX_TENANT", self._runtime["AEX_TENANT"])
        if self._runtime.get("AEX_PROJECT"):
            os.environ.setdefault("AEX_PROJECT", self._runtime["AEX_PROJECT"])

        if not self._policy:
            return

        os.environ["AEX_POLICY_ID"] = self._policy.policy_id
        os.environ["AEX_POLICY_JSON"] = self._policy.to_json()
        os.environ["AEX_POLICY_MAX_STEPS"] = str(self._policy.max_steps)
        os.environ["AEX_POLICY_BUDGET_USD"] = str(self._policy.budget_usd)
        os.environ["AEX_POLICY_ALLOW_TOOLS"] = ",".join(self._policy.allow_tools)
        os.environ["AEX_POLICY_DENY_TOOLS"] = ",".join(self._policy.deny_tools)
        os.environ["AEX_DANGEROUS_OPS"] = "1" if self._policy.dangerous_ops else "0"
        os.environ["AEX_REQUIRE_DESTRUCTIVE_APPROVAL"] = (
            "1" if self._policy.require_approval_for_destructive_ops else "0"
        )

    def _inject_max_steps(self, fn: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        if not self._policy or "max_steps" in kwargs:
            return kwargs
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            return kwargs
        if "max_steps" in sig.parameters:
            kwargs["max_steps"] = self._policy.max_steps
        return kwargs

    def run(self, *args, **kwargs):
        self._apply_runtime_context()
        fn = getattr(self._agent, "run", None)
        if not callable(fn):
            raise AttributeError("Wrapped agent does not expose a callable run() method")
        merged_kwargs = self._inject_max_steps(fn, dict(kwargs))
        return fn(*args, **merged_kwargs)

    def __call__(self, *args, **kwargs):
        self._apply_runtime_context()
        if callable(self._agent):
            merged_kwargs = self._inject_max_steps(self._agent, dict(kwargs))
            return self._agent(*args, **merged_kwargs)
        return self.run(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._agent, name)


class AEX:
    """High-level, one-line SDK interface."""

    @staticmethod
    def login(
        *,
        api_key: str,
        base_url: str,
        tenant: str | None = None,
        project: str | None = None,
        mode: str = "proxy",
        monkey_patch: bool = False,
    ) -> dict[str, str]:
        token = (api_key or "").strip()
        if not token:
            raise ValueError("api_key is required")
        resolved_base_url = _normalize_base_url(base_url)
        profile = {
            "api_key": token,
            "base_url": resolved_base_url[:-3] if resolved_base_url.endswith("/v1") else resolved_base_url,
            "tenant": (tenant or "").strip(),
            "project": (project or "").strip(),
            "mode": (mode or "proxy").strip().lower(),
        }
        _save_profile(profile)
        return AEX.enable(
            api_key=token,
            base_url=profile["base_url"],
            tenant=profile["tenant"] or None,
            project=profile["project"] or None,
            mode=profile["mode"],
            monkey_patch=monkey_patch,
        )

    @staticmethod
    def enable(
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        tenant: str | None = None,
        project: str | None = None,
        mode: str | None = None,
        monkey_patch: bool = True,
    ) -> dict[str, str]:
        profile = _load_profile()
        resolved_base_url = _normalize_base_url(base_url)
        resolved_mode = (mode or os.getenv("AEX_MODE") or profile.get("mode") or "proxy").strip().lower()
        if resolved_mode not in {"proxy", "local"}:
            raise ValueError("AEX mode must be either 'proxy' or 'local'")
        resolved_api_key = _resolve_api_key(api_key)
        resolved_tenant = (tenant or os.getenv("AEX_TENANT") or profile.get("tenant") or "").strip()
        resolved_project = (project or os.getenv("AEX_PROJECT") or profile.get("project") or "").strip()

        os.environ["AEX_ENABLE"] = "1"
        os.environ["AEX_MODE"] = resolved_mode
        os.environ["AEX_BASE_URL"] = resolved_base_url[:-3] if resolved_base_url.endswith("/v1") else resolved_base_url
        os.environ["OPENAI_BASE_URL"] = resolved_base_url
        if resolved_api_key:
            os.environ["AEX_API_KEY"] = resolved_api_key
            os.environ.setdefault("AEX_AGENT_TOKEN", resolved_api_key)
            os.environ.setdefault("OPENAI_API_KEY", resolved_api_key)
        if resolved_tenant:
            os.environ["AEX_TENANT"] = resolved_tenant
        if resolved_project:
            os.environ["AEX_PROJECT"] = resolved_project

        if monkey_patch:
            _install_monkey_patches(resolved_base_url)

        exported = {
            "AEX_ENABLE": os.environ["AEX_ENABLE"],
            "AEX_MODE": os.environ["AEX_MODE"],
            "AEX_BASE_URL": os.environ["AEX_BASE_URL"],
            "OPENAI_BASE_URL": os.environ["OPENAI_BASE_URL"],
        }
        if resolved_api_key:
            exported["AEX_API_KEY"] = resolved_api_key
        if resolved_tenant:
            exported["AEX_TENANT"] = resolved_tenant
        if resolved_project:
            exported["AEX_PROJECT"] = resolved_project
        return exported

    @staticmethod
    def wrap(
        agent: Any,
        policy_id: str | None = None,
        policy: Policy | Mapping[str, Any] | None = None,
        *,
        api_key: str | None = None,
        tenant: str | None = None,
        project: str | None = None,
        mode: str | None = None,
        base_url: str | None = None,
        monkey_patch: bool = True,
        auto_enable: bool = True,
    ) -> WrappedAgent:
        if policy_id and policy is not None:
            raise ValueError("Provide either policy_id or policy, not both")

        if auto_enable:
            runtime = AEX.enable(
                base_url=base_url,
                api_key=api_key,
                tenant=tenant,
                project=project,
                mode=mode,
                monkey_patch=monkey_patch,
            )
        else:
            runtime = {
                "AEX_API_KEY": _resolve_api_key(api_key),
                "OPENAI_BASE_URL": _normalize_base_url(base_url),
            }
            profile = _load_profile()
            resolved_tenant = (tenant or os.getenv("AEX_TENANT") or profile.get("tenant") or "").strip()
            resolved_project = (project or os.getenv("AEX_PROJECT") or profile.get("project") or "").strip()
            if resolved_tenant:
                runtime["AEX_TENANT"] = resolved_tenant
            if resolved_project:
                runtime["AEX_PROJECT"] = resolved_project

        resolved_policy: Policy | None = None
        if isinstance(policy, Policy):
            resolved_policy = policy
        elif isinstance(policy, Mapping):
            if not policy_id:
                policy_id = str(policy.get("policy_id") or "").strip() or "inline_policy"
            resolved_policy = policy_from_dict(policy_id, dict(policy))
        elif policy_id:
            resolved_policy = load_policy(policy_id)

        return WrappedAgent(agent, resolved_policy, runtime=runtime)

    @staticmethod
    def policy(
        policy_id: str,
        *,
        budget_usd: float = 50.0,
        allow_tools: list[str] | tuple[str, ...] | str | None = None,
        deny_tools: list[str] | tuple[str, ...] | str | None = None,
        max_steps: int = 100,
        dangerous_ops: bool = False,
        require_approval_for_destructive_ops: bool = True,
    ) -> Policy:
        payload = {
            "budget_usd": budget_usd,
            "allow_tools": allow_tools or [],
            "deny_tools": deny_tools or [],
            "max_steps": max_steps,
            "dangerous_ops": dangerous_ops,
            "require_approval_for_destructive_ops": require_approval_for_destructive_ops,
        }
        return create_policy(policy_id, payload)


def wrap(agent: Any, *args, **kwargs) -> WrappedAgent:
    return AEX.wrap(agent, *args, **kwargs)


def enable(**kwargs) -> dict[str, str]:
    return AEX.enable(**kwargs)


def login(*, api_key: str, base_url: str, tenant: str | None = None, project: str | None = None) -> dict[str, str]:
    return AEX.login(api_key=api_key, base_url=base_url, tenant=tenant, project=project)
