"""AEX Authentication middleware — FastAPI dependency for token validation."""

from datetime import datetime, timezone

from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from ..db import get_db_connection
from ..utils.logging_config import StructuredLogger
from .hashing import hash_token, _MIN_TOKEN_HEX_LEN

logger = StructuredLogger(__name__)
security = HTTPBearer()


def get_agent_from_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> dict:
    """Authenticate agent by token and return full agent row as dict.

    Authentication flow:
    1. Validate token entropy (reject short/weak tokens)
    2. Hash token → lookup by token_hash (primary path)
    3. Fallback: lookup by raw api_token (backward compat for pre-v1.2 agents)
    4. Check TTL expiry
    5. Return full agent dict including scope
    """
    token = credentials.credentials

    # 1. Entropy validation
    if len(token) < _MIN_TOKEN_HEX_LEN:
        logger.warning("Authentication failed: Token too short", length=len(token))
        raise HTTPException(status_code=401, detail="Invalid API token: insufficient entropy")

    # 2. Hash-based lookup (primary)
    token_sha = hash_token(token)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        row = cursor.execute(
            "SELECT * FROM agents WHERE token_hash = ?", (token_sha,)
        ).fetchone()

        # 3. Fallback: raw token lookup (backward compat for pre-v1.2 agents without hash)
        if not row:
            row = cursor.execute(
                "SELECT * FROM agents WHERE api_token = ?", (token,)
            ).fetchone()

        if not row:
            logger.warning("Authentication failed: Invalid token")
            raise HTTPException(status_code=401, detail="Invalid API token")

        agent = dict(row)

    # 4. TTL check
    expires_at = agent.get("token_expires_at")
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if now > expiry:
                logger.warning("Authentication failed: Token expired",
                             agent=agent["name"], expired_at=expires_at)
                raise HTTPException(status_code=401, detail="API token has expired")
        except ValueError:
            pass  # Malformed expiry — treat as no expiry

    return agent
