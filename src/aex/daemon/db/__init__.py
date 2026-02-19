"""AEX Database package — connection, schema, integrity.

Re-exports public API so consumers can continue using:
    from .db import get_db_connection, init_db, check_db_integrity
"""

from .connection import DB_PATH, get_db_connection
from .schema import init_db
from .integrity import check_db_integrity

__all__ = [
    "DB_PATH",
    "get_db_connection",
    "init_db",
    "check_db_integrity",
]
