from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .db import get_db_connection
from .logging_config import StructuredLogger

logger = StructuredLogger(__name__)
security = HTTPBearer()

def get_agent_from_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> str:
    token = credentials.credentials
    with get_db_connection() as conn:
        cursor = conn.cursor()
        row = cursor.execute("SELECT name FROM agents WHERE api_token = ?", (token,)).fetchone()
        
        if not row:
            logger.warning("Authentication failed: Invalid token")
            raise HTTPException(status_code=403, detail="Invalid API token")
            
        return row["name"]
