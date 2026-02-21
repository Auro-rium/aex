# db - v2.0

SQLite WAL-backed persistence.

Key properties:
- runtime DB path resolution via `AEX_DB_PATH`
- schema migrations are idempotent
- startup integrity gate combines physical check + invariants

Core schema objects:
- `agents`, `executions`, `reservations`, `event_log`, `events`, `tool_plugins`, `rate_windows`, `pids`
