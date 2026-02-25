# AEX Cloud Control Plane (Neon + Railway + Redis)

## 1) Distributed Cloud Architecture

```text
                                       +------------------------------+
                                       |      AEX Dashboard (H)       |
                                       |   (read-only ops + replay)   |
                                       +---------------+--------------+
                                                       |
                                                       v
+-----------------------------+       +----------------+----------------+
|   Agent SDK Wrapper Layer   |       |      AEX Admin API (H)         |
| (OpenAI/Groq/Lang*/DSPy...) +------>+ (RBAC, keys, tenants, webhooks)|
|      local intercept        |       +----------------+----------------+
|   or remote proxy mode      |                        |
+---------------+-------------+                        v
                |                    +-----------------+----------------+
                | HTTPS              |   Neon Postgres Ledger (H conn)  |
                +------------------->+  SERIALIZABLE tx + event chain   |
                                     +-----------------+----------------+
                                                       ^
                                                       |
                                     +-----------------+----------------+
                                     |   Settlement Worker Service (H)   |
                                     |   (outbox, retries, reconciliation)|
                                     +-----------------+----------------+
                                                       ^
                                                       |
+-------------------------------+     +---------------+------------------+
| Stateless Proxy Cluster (H)   +---->+ Admission Service (H in same app)|
| /v1/chat,/v1/responses,/emb.. |     | reserve->policy->dispatch gate   |
+---------------+---------------+     +---------------+------------------+
                |                                     |
                |                                     v
                |                     +---------------+------------------+
                |                     |       Policy Engine (H)          |
                |                     | rules, scopes, quotas, allowlist |
                |                     +---------------+------------------+
                |                                     |
                v                                     v
      +---------+------------------+      +-----------+------------------+
      | LLM Providers (OpenAI etc) |      | Redis (H): rate limits,      |
      | passthrough with tenant key|      | distributed locks, short TTL  |
      +----------------------------+      +-------------------------------+

(H) = horizontally scalable
```

### Invariant mapping

- Exactly-once reserve -> commit -> release: enforced by `executions` + `reservation_ledger` row-level constraints, `SERIALIZABLE` tx, and Redis lock on `(tenant_id, execution_id)` for cross-instance coordination.
- `execution_id` idempotency: DB unique constraints + admission idempotency upsert.
- Hash chain integrity: append-only `event_log` with `prev_hash`/`event_hash`, per-tenant partitioning, and verifier job.
- SERIALIZABLE safety: all financial transitions in `SERIALIZABLE` transactions with retry-on-serialization-failure.
- Crash-safe settlement: transactional outbox + worker retry and dedupe keys.
- Deterministic replay: canonical payload hashing + replay tables + scheduled verification.

## 2) Universal Wrapper Design

### SDK surface

```python
from aex import AEX, wrap, enable

agent = AEX.wrap(agent, tenant="acme", project="prod")
# or
agent = wrap(agent, tenant="acme", project="prod")

enable()  # env-driven auto interception
```

### Runtime modes

- Local interception mode:
  - monkey-patches OpenAI-compatible client constructors and selected HTTP calls.
  - injects headers before request leaves process.
  - uses original provider endpoint if `AEX_ENABLE=0`.
- Remote proxy mode:
  - rewrites base URL to `AEX_BASE_URL` (`https://aex-cloud.app/v1`).
  - request goes through AEX API/proxy cluster.
  - provider credentials pass through as encrypted metadata headers or key reference IDs.

### Interception strategy

- OpenAI/Groq SDK: patch client initialization (`base_url`, `api_key`) and request methods.
- Frameworks (LangChain, LangGraph, AutoGen, DSPy, LlamaIndex): avoid framework internals; rely on OpenAI-compatible transport interception and environment variables.
- Raw HTTP: wrap `httpx.Client`, `httpx.AsyncClient`, and `requests.Session` adapters.
- Safety:
  - patch once per process (`_aex_patch_version` sentinel).
  - preserve original callables for rollback.
  - no patching when package missing.

### Provider key passthrough

- Preferred: tenant provider key vault reference (`provider_key_id`) stored server-side.
- Optional: ephemeral encrypted passthrough header from client (`X-AEX-Provider-Key-Enc`) for bring-your-own-key runtime.
- Never persist raw provider key in logs/event payload.

### `execution_id` and idempotency injection

- If caller provides `execution_id`, pass through unchanged.
- Else generate ULID: `ex_<ulid>`.
- Inject into:
  - body: `metadata.execution_id` for OpenAI-compatible payloads.
  - header: `X-AEX-Execution-Id`.
- Idempotency key:
  - caller provided key wins.
  - else deterministic key: `sha256(tenant|project|model|normalized_prompt|tools|execution_id)`.
  - header: `Idempotency-Key` + `X-AEX-Idempotency-Key`.

### Failure fallback behavior

- Default (`AEX_FAIL_OPEN=0`): fail-closed to preserve governance guarantees.
- Optional (`AEX_FAIL_OPEN=1`): direct provider fallback, but mark event as `governance_bypassed=true` and raise high-severity alert.
- Timeouts:
  - admission timeout -> retry with same idempotency key.
  - provider timeout -> reservation held; settlement worker reconciles final state.

### Environment auto-enable

```bash
AEX_ENABLE=1
AEX_BASE_URL=https://aex-cloud.app
AEX_TENANT=acme
AEX_PROJECT=prod
AEX_MODE=proxy
```

## 3) Database Migration (Neon only)

- SQLite fallback removed: Postgres DSN mandatory.
- All ledger transitions run in `SERIALIZABLE` transactions.
- Tenant-aware schema with RBAC and API keys.
- All execution-path tables require `tenant_id NOT NULL`.
- `event_log` partitioned by tenant.

Implemented schema file: `deploy/neon/001_multitenant_control_plane.sql`.

## 4) Service Split

### 1. `aex-api` (proxy + admission)

- Public `/v1/*` OpenAI-compatible API.
- AuthN, tenant scope resolution, policy gate, reserve.
- Dispatch to provider and write admission events.

### 2. `aex-settlement-worker`

- Consumes settlement outbox.
- Commit/release, retries, reconciliation, webhook delivery.
- Replays stuck executions and resolves partial outcomes.

### 3. `aex-admin-api`

- Tenant/project/user lifecycle.
- API key issuance/revocation.
- RBAC checks, webhook config, replay trigger endpoints.

### 4. `aex-dashboard` (optional)

- Read-only operational UI and audit views.
- Uses admin API only; no direct DB access from browser.

### 5. `aex-sdk` (client library)

- `wrap()` and `enable()` primitives.
- Request interception, execution metadata injection.
- Framework-agnostic compatibility shim.

## 5) Railway Deployment

- Config template: `deploy/railway/railway.json`
- Environment template: `deploy/railway/.env.example`

### Required services

- `aex-api`
- `aex-settlement-worker`
- `aex-admin-api`
- `aex-dashboard` (optional)
- Managed Redis (Railway plugin) + Neon external Postgres

### Health checks

- API: `/health`, `/ready`
- Worker: `/healthz` or heartbeat metric
- Admin: `/health`
- Dashboard: `/` and backend connectivity probe

### Scaling strategy

- `aex-api`: scale on CPU + p95 latency + RPS.
- `aex-settlement-worker`: scale on outbox backlog depth.
- `aex-admin-api`: low fixed replicas (1-2), burst on admin load.
- Redis and Neon are managed external dependencies.

## 6) Auth & Tenancy

- Tenant-scoped API keys:
  - hashed at rest (`sha256`/argon2), prefix identifier returned once.
  - scopes: `proxy:invoke`, `admin:read`, `admin:write`, `webhook:manage`.
- RBAC:
  - roles: `owner`, `admin`, `developer`, `viewer`, `service`.
  - enforced at admission/admin boundary.
- Webhook signing:
  - HMAC SHA-256 over canonical payload + timestamp.
  - headers: `X-AEX-Signature`, `X-AEX-Timestamp`, `X-AEX-Delivery-Id`.
- Admin protection:
  - admin API key/JWT + optional mTLS for internal routes.
  - strict CORS and IP allow-list option.
- Scope validation in admission:
  - resolve `tenant_id/project_id` from API key.
  - deny cross-tenant execution IDs.

## 7) Observability

- Prometheus metrics endpoint on each service (`/metrics`):
  - `aex_admission_requests_total`
  - `aex_reservation_conflicts_total`
  - `aex_settlement_retries_total`
  - `aex_event_chain_break_total`
  - `aex_replay_mismatch_total`
- OpenTelemetry traces:
  - trace IDs propagate SDK -> API -> worker -> provider.
- Structured logs:
  - JSON with `tenant_id`, `project_id`, `execution_id`, `idempotency_key`, `trace_id`.
- Drift invariant checker:
  - periodic job verifies budget totals, chain continuity, reservation states.
- Alerts:
  - chain mismatch > 0
  - settlement lag > threshold
  - SERIALIZABLE abort retry storm
  - governance bypass (fail-open) event

## 8) Failure Modes

- Neon transient drop:
  - short request timeout, exponential retry, circuit breaker.
  - keep idempotency key stable across retries.
- Railway restart mid-transaction:
  - Postgres tx atomic rollback; worker resumes from outbox/incomplete rows.
- Redis outage:
  - degrade lock/rate-limit to Postgres fallback path (reduced throughput), fail-closed on high-risk endpoints.
- Duplicate settlement attempt:
  - unique `execution_id` + `ON CONFLICT DO NOTHING` + state machine checks.
- Provider timeout:
  - execution remains `DISPATCHED`; worker reconciliation polls provider receipt.
- Worker crash:
  - outbox rows remain pending with lease timeout.
- Partial network partition:
  - request may be accepted but response lost; idempotent retry returns canonical prior result.

## 9) Codebase Reorganization

```text
src/aex/
  api/
  admission/
  settlement/
  db/
  sdk/
  auth/
  tenancy/
  observability/
  workers/
  frontend/
  config/
```

Deprecated local-only modules (move to `src/aex/_deprecated_local/`):

- `src/aex/daemon/` (single-process daemon paths)
- sqlite compatibility shims (if any leftovers)
- local PID supervision modules not relevant for stateless Railway services

## 10) Production Readiness Checklist

- [ ] Run Neon migration (`001_multitenant_control_plane.sql`) in staging then prod.
- [ ] Validate constraints/indexes and tenant partition creation.
- [ ] Run replay verification over historical execution windows.
- [ ] Horizontal scale test: 1x -> Nx `aex-api` and worker nodes.
- [ ] Webhook signing/verification integration test with retries.
- [ ] Rate limit correctness test under concurrent load.
- [ ] Failover drills:
  - Neon connection interruptions
  - Redis unavailability
  - Railway rolling restarts
- [ ] Verify no fail-open path in production unless explicitly enabled.
- [ ] Confirm audit log immutability + chain checker green.

## Immediate Implementation Sequence

1. Ship schema migration and dual-write adapters in `aex-api` and worker.
2. Enable SDK interception in proxy mode by default (`AEX_ENABLE=1`).
3. Move settlement to outbox worker and cut over admission path.
4. Enable invariant checker and alerts before production traffic ramp.
