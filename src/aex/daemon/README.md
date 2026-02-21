# Daemon - v2.0

AEX daemon is the control-plane runtime around these modules:
- `app` - FastAPI ingress and endpoints
- `auth` - token auth/TTL/scope
- `control` - admission, routing, lifecycle
- `db` - SQLite WAL schema + integrity
- `ledger` - reservation + settlement + replay
- `policy` - deterministic request/response policy pipeline
- `runtime` - crash recovery
- `sandbox` - tool execution isolation
- `observability` - tracing + burn-rate helpers
- `utils` - config, invariants, rate-limit, metrics, logging
