"""AEX Database package — connection, schema, integrity.

Re-exports public API so consumers can continue using:
    from .db import get_db_connection, init_db, check_db_integrity
"""

from .connection import get_db_connection, get_db_path, get_db_dsn
from .schema import init_db
from .integrity import check_db_integrity

__all__ = [
    "get_db_path",
    "get_db_dsn",
    "get_db_connection",
    "init_db",
    "check_db_integrity",
]
