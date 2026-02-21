# AEX v2.0 - Auto Execution Kernel

AEX v2.0 is a local-first governance kernel for agent execution with deterministic accounting.

Core guarantees:
- budget reserve/commit/release lifecycle per `execution_id`
- idempotent request replay behavior
- hash-chained ledger events for tamper evidence
- OpenAI-compatible northbound API with provider abstraction southbound

## v2.0 Runtime Architecture

Control path:
1. Auth (`Bearer` token, scope, TTL)
2. Admission (`execution_id`, rate-limit, policy, route, preflight reserve)
3. Provider dispatch (streaming/non-streaming)
4. Exactly-once settlement (`COMMITTED` or `RELEASED`/`DENIED`/`FAILED`)
5. Hash-chain event append + metrics projection

Execution states:
- `RESERVING -> RESERVED -> DISPATCHED -> COMMITTED`
- failure paths: `RELEASED`, `DENIED`, `FAILED`

## Active Endpoints (Sorted)

Admin:
- `GET /admin/activity`
- `POST /admin/reload_config`
- `GET /admin/replay`
- `GET /dashboard`
- `GET /health`
- `GET /metrics`

Proxy:
- `POST /openai/v1/chat/completions`
- `POST /openai/v1/embeddings`
- `POST /openai/v1/responses`
- `POST /openai/v1/tools/execute`
- `POST /v1/chat/completions`
- `POST /v1/embeddings`
- `POST /v1/responses`
- `POST /v1/tools/execute`

## Data Model (v2.0)

Primary tables:
- `agents` - identity, caps, budget/spend/reserved counters
- `executions` - idempotent execution identity + terminal cache
- `reservations` - reserve/commit/release state
- `event_log` - hash-chained immutable events
- `events` - compatibility/event metrics stream
- `rate_windows` - RPM/TPM windows
- `tool_plugins` - plugin registry

## Startup + Recovery

On daemon startup:
- initialize/migrate DB schema
- run integrity checks
- load model/provider config
- reconcile incomplete executions (release stale reservations, fail broken non-terminal flows)

## Dashboard

Live playout dashboard:
- `http://127.0.0.1:9000/dashboard`

## Quick Start

```bash
pip install aex

aex init
aex daemon start

aex agent create my-agent 5.00 30 --allow-passthrough

export OPENAI_BASE_URL=http://127.0.0.1:9000/v1
export OPENAI_API_KEY=<AEX_AGENT_TOKEN>
```

## Source Layout

Technical READMEs are provided in each major folder under `src/aex` and `src/aex/daemon`.
