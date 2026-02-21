"""Capability token mint/verify for sandbox calls."""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass

from ..utils.deterministic import canonical_json, stable_hash_hex


@dataclass
class CapabilityToken:
    execution_id: str
    agent: str
    tool_name: str
    allowed_fs: list[str]
    net_policy: str
    ttl_ms: int
    max_output_bytes: int


def _secret() -> str:
    return os.getenv("AEX_CAP_TOKEN_SECRET", "aex-local-cap-token-secret")


def mint_token(token: CapabilityToken) -> str:
    payload = {
        "execution_id": token.execution_id,
        "agent": token.agent,
        "tool_name": token.tool_name,
        "allowed_fs": sorted(token.allowed_fs),
        "net_policy": token.net_policy,
        "ttl_ms": int(token.ttl_ms),
        "max_output_bytes": int(token.max_output_bytes),
        "issued_ms": int(time.time() * 1000),
    }
    payload_json = canonical_json(payload)
    sig = stable_hash_hex(_secret(), payload_json)
    body = canonical_json({"payload": payload, "sig": sig})
    return base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii")


def verify_token(encoded: str) -> CapabilityToken:
    raw = base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
    wrapper = json.loads(raw)
    payload = wrapper["payload"]
    sig = wrapper["sig"]

    payload_json = canonical_json(payload)
    expected = stable_hash_hex(_secret(), payload_json)
    if sig != expected:
        raise ValueError("Capability token signature mismatch")

    issued = int(payload["issued_ms"])
    ttl = int(payload["ttl_ms"])
    now = int(time.time() * 1000)
    if now > issued + ttl:
        raise ValueError("Capability token expired")

    return CapabilityToken(
        execution_id=payload["execution_id"],
        agent=payload["agent"],
        tool_name=payload["tool_name"],
        allowed_fs=list(payload.get("allowed_fs") or []),
        net_policy=payload.get("net_policy", "deny"),
        ttl_ms=ttl,
        max_output_bytes=int(payload.get("max_output_bytes", 65536)),
    )
