"""AEX Authentication package — hashing and middleware.

Re-exports public API so consumers can continue using:
    from .auth import hash_token, get_agent_from_token
"""

from .hashing import hash_token
from .middleware import get_agent_from_token

__all__ = [
    "hash_token",
    "get_agent_from_token",
]
