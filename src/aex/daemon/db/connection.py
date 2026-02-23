"""PostgreSQL connection management for AEX."""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


def get_db_dsn() -> str:
    """Resolve PostgreSQL DSN from environment.

    Required: AEX_PG_DSN
    Example: postgresql://aex:aex@127.0.0.1:5432/aex
    """
    dsn = (os.getenv("AEX_PG_DSN") or "").strip()
    if not dsn:
        raise RuntimeError("AEX_PG_DSN is required (PostgreSQL backend is mandatory in v2.1)")
    return dsn


def get_db_path() -> str:
    """Backward-compatible accessor used by legacy callsites."""
    dsn = get_db_dsn()
    if "://" in dsn and "@" in dsn:
        scheme, rest = dsn.split("://", 1)
        _, host = rest.rsplit("@", 1)
        return f"{scheme}://***@{host}"
    return dsn


def _normalize_sql(query: str) -> str:
    q = query
    if "BEGIN IMMEDIATE" in q:
        q = q.replace("BEGIN IMMEDIATE", "BEGIN")
    if "AUTOINCREMENT" in q:
        q = q.replace("AUTOINCREMENT", "")

    # Translate SQLite-style '?' placeholders to PostgreSQL '%s'.
    out: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(q):
        ch = q[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
            i += 1
            continue
        if ch == "?" and not in_single and not in_double:
            out.append("%s")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


class CompatRow(dict):
    """Row wrapper supporting both dict and positional access."""

    def __init__(self, data: dict[str, Any], columns: list[str]):
        super().__init__(data)
        self._columns = columns

    def __getitem__(self, key):  # type: ignore[override]
        if isinstance(key, int):
            if key < 0 or key >= len(self._columns):
                raise IndexError(key)
            return super().__getitem__(self._columns[key])
        return super().__getitem__(key)


@dataclass
class CompatCursor:
    _cursor: Any
    _columns: list[str] | None = None

    def execute(self, query: str, params: Any = None):
        sql = _normalize_sql(query)
        if params is None:
            self._cursor.execute(sql)
        else:
            self._cursor.execute(sql, params)
        self._columns = [d.name for d in self._cursor.description] if self._cursor.description else None
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        if isinstance(row, dict):
            return CompatRow(row, self._columns or list(row.keys()))
        if hasattr(row, "_asdict"):
            data = row._asdict()
            return CompatRow(data, self._columns or list(data.keys()))
        return row

    def fetchall(self):
        rows = self._cursor.fetchall()
        out = []
        for row in rows:
            if isinstance(row, dict):
                out.append(CompatRow(row, self._columns or list(row.keys())))
            elif hasattr(row, "_asdict"):
                data = row._asdict()
                out.append(CompatRow(data, self._columns or list(data.keys())))
            else:
                out.append(row)
        return out

    @property
    def rowcount(self) -> int:
        return int(getattr(self._cursor, "rowcount", 0) or 0)

    @property
    def lastrowid(self):
        # psycopg does not guarantee this; use RETURNING for reliability.
        return getattr(self._cursor, "lastrowid", None)


class CompatConnection:
    def __init__(self, conn, row_factory):
        self._conn = conn
        self._row_factory = row_factory

    def cursor(self) -> CompatCursor:
        return CompatCursor(self._conn.cursor(row_factory=self._row_factory))

    def execute(self, query: str, params: Any = None) -> CompatCursor:
        cur = self.cursor()
        cur.execute(query, params)
        return cur

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


@contextmanager
def get_db_connection():
    """Yield a PostgreSQL connection wrapper compatible with existing callsites."""
    dsn = get_db_dsn()
    try:
        import psycopg
        from psycopg.rows import dict_row
    except Exception as exc:
        raise RuntimeError(
            "psycopg is required for PostgreSQL backend. Install with: pip install \"psycopg[binary]>=3.2\""
        ) from exc

    conn = psycopg.connect(dsn, row_factory=dict_row)
    wrapped = CompatConnection(conn, dict_row)
    try:
        yield wrapped
    finally:
        conn.close()
