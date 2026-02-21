# app - Endpoint Layer (v2.0)

FastAPI routing layer.

Modules:
- `__init__.py` - app creation + router registration + startup/shutdown hooks
- `lifecycle.py` - DB init, integrity gate, config load, recovery, enforcement loop
- `proxy.py` - OpenAI-compatible execution and tool endpoints
- `non_streaming.py` - single-response settlement path
- `streaming.py` - SSE relay settlement path
- `admin.py` - health/metrics/dashboard/replay/activity endpoints

Endpoint inventory: see `endpoints.md`.
