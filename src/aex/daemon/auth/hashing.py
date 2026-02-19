"""Token hashing utilities for AEX authentication."""

import hashlib

# Minimum token length (hex chars) — 16 bytes = 32 hex chars
_MIN_TOKEN_HEX_LEN = 32


def hash_token(raw_token: str) -> str:
    """SHA-256 hash a raw token for storage/lookup."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
