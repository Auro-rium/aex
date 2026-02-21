"""Deterministic serialization and hashing helpers."""

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Serialize data into stable JSON for replay-safe hashes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash_hex(*parts: str) -> str:
    """Create a stable SHA-256 digest over multiple string parts."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()
