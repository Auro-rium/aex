-- AEX Cloud Control Plane Schema (Neon PostgreSQL)
-- Invariants addressed:
-- 1) exactly-once reserve->commit->release
-- 2) execution_id idempotency
-- 3) hash-chained event integrity
-- 4) SERIALIZABLE-safe transactional model (enforced in app tx settings)
-- 5) crash-safe settlement via outbox
-- 6) deterministic replay verification

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TYPE role_enum AS ENUM ('owner', 'admin', 'developer', 'viewer', 'service');
CREATE TYPE api_key_status_enum AS ENUM ('active', 'revoked');
CREATE TYPE execution_state_enum AS ENUM (
  'reserving',
  'reserved',
  'dispatched',
  'response_received',
  'committed',
  'released',
  'denied',
  'failed'
);
CREATE TYPE reservation_state_enum AS ENUM ('reserved', 'committed', 'released', 'failed');
CREATE TYPE outbox_state_enum AS ENUM ('pending', 'leased', 'done', 'dead');
CREATE TYPE webhook_delivery_state_enum AS ENUM ('pending', 'delivered', 'failed');

CREATE TABLE tenants (
  tenant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'read_only')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE users (
  user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email CITEXT NOT NULL UNIQUE,
  display_name TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE tenant_memberships (
  tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  role role_enum NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (tenant_id, user_id)
);

CREATE TABLE projects (
  project_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  slug TEXT NOT NULL,
  display_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (tenant_id, slug)
);

CREATE TABLE api_keys (
  api_key_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  project_id UUID REFERENCES projects(project_id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  key_prefix TEXT NOT NULL,
  key_hash TEXT NOT NULL,
  scopes TEXT[] NOT NULL,
  status api_key_status_enum NOT NULL DEFAULT 'active',
  expires_at TIMESTAMPTZ,
  created_by UUID REFERENCES users(user_id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (tenant_id, key_prefix)
);

CREATE INDEX idx_api_keys_tenant_status ON api_keys (tenant_id, status);

CREATE TABLE provider_credentials (
  provider_credential_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  project_id UUID REFERENCES projects(project_id) ON DELETE CASCADE,
  provider_name TEXT NOT NULL,
  credential_ref TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (tenant_id, project_id, provider_name)
);

CREATE TABLE executions (
  execution_pk BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
  project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  execution_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  provider_name TEXT NOT NULL,
  model TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  request_fingerprint BYTEA NOT NULL,
  request_body JSONB NOT NULL,
  response_body JSONB,
  error_body JSONB,
  provider_request_id TEXT,
  status_code INTEGER,
  state execution_state_enum NOT NULL DEFAULT 'reserving',
  reserve_micro BIGINT NOT NULL DEFAULT 0 CHECK (reserve_micro >= 0),
  commit_micro BIGINT NOT NULL DEFAULT 0 CHECK (commit_micro >= 0),
  release_micro BIGINT NOT NULL DEFAULT 0 CHECK (release_micro >= 0),
  retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  terminal_at TIMESTAMPTZ,
  UNIQUE (execution_id),
  UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX idx_executions_tenant_created ON executions (tenant_id, created_at DESC);
CREATE INDEX idx_executions_tenant_state ON executions (tenant_id, state);
CREATE INDEX idx_executions_tenant_project ON executions (tenant_id, project_id);

CREATE TABLE reservation_ledger (
  execution_id TEXT PRIMARY KEY REFERENCES executions(execution_id) ON DELETE CASCADE,
  tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
  project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  reserved_micro BIGINT NOT NULL CHECK (reserved_micro >= 0),
  committed_micro BIGINT NOT NULL DEFAULT 0 CHECK (committed_micro >= 0),
  released_micro BIGINT NOT NULL DEFAULT 0 CHECK (released_micro >= 0),
  state reservation_state_enum NOT NULL DEFAULT 'reserved',
  version BIGINT NOT NULL DEFAULT 0,
  reserved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  committed_at TIMESTAMPTZ,
  released_at TIMESTAMPTZ,
  CHECK (committed_micro <= reserved_micro),
  CHECK (released_micro <= committed_micro)
);

CREATE INDEX idx_reservation_ledger_tenant ON reservation_ledger (tenant_id, state);

CREATE TABLE settlement_outbox (
  outbox_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
  project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  execution_id TEXT NOT NULL REFERENCES executions(execution_id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('commit', 'release', 'webhook', 'reconcile')),
  payload JSONB NOT NULL,
  state outbox_state_enum NOT NULL DEFAULT 'pending',
  lease_owner TEXT,
  lease_expires_at TIMESTAMPTZ,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (tenant_id, execution_id, kind)
);

CREATE INDEX idx_settlement_outbox_poll ON settlement_outbox (state, next_attempt_at, tenant_id);
CREATE INDEX idx_settlement_outbox_exec ON settlement_outbox (execution_id);

CREATE TABLE settlement_attempts (
  attempt_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
  execution_id TEXT NOT NULL REFERENCES executions(execution_id) ON DELETE CASCADE,
  attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
  action TEXT NOT NULL CHECK (action IN ('reserve', 'commit', 'release', 'reconcile')),
  status TEXT NOT NULL CHECK (status IN ('ok', 'retry', 'error')),
  details JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (tenant_id, execution_id, action, attempt_no)
);

CREATE INDEX idx_settlement_attempts_exec ON settlement_attempts (execution_id, created_at DESC);

-- Tenant-partitioned hash-chain event log
CREATE TABLE event_log (
  seq BIGINT GENERATED ALWAYS AS IDENTITY,
  tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
  project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  execution_id TEXT REFERENCES executions(execution_id) ON DELETE SET NULL,
  chain_scope TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json JSONB NOT NULL,
  prev_hash CHAR(64) NOT NULL,
  event_hash CHAR(64) NOT NULL,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (tenant_id, seq),
  UNIQUE (tenant_id, event_hash)
) PARTITION BY LIST (tenant_id);

CREATE TABLE event_log_default PARTITION OF event_log DEFAULT;

CREATE INDEX idx_event_log_default_exec ON event_log_default (execution_id, recorded_at);
CREATE INDEX idx_event_log_default_scope ON event_log_default (tenant_id, chain_scope, seq DESC);

CREATE OR REPLACE FUNCTION create_event_log_partition(p_tenant UUID)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
  partition_name TEXT;
BEGIN
  partition_name := format('event_log_t_%s', replace(p_tenant::text, '-', '_'));
  EXECUTE format(
    'CREATE TABLE IF NOT EXISTS %I PARTITION OF event_log FOR VALUES IN (%L);',
    partition_name,
    p_tenant
  );
  EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I (execution_id, recorded_at);', partition_name || '_exec_idx', partition_name);
  EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I (chain_scope, seq DESC);', partition_name || '_scope_idx', partition_name);
END;
$$;

CREATE OR REPLACE FUNCTION enforce_event_chain_integrity()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
  expected_prev CHAR(64);
BEGIN
  SELECT e.event_hash
    INTO expected_prev
    FROM event_log e
   WHERE e.tenant_id = NEW.tenant_id
     AND e.chain_scope = NEW.chain_scope
   ORDER BY e.seq DESC
   LIMIT 1;

  IF expected_prev IS NULL THEN
    IF NEW.prev_hash <> repeat('0', 64) THEN
      RAISE EXCEPTION 'Invalid genesis prev_hash for tenant %, scope %', NEW.tenant_id, NEW.chain_scope;
    END IF;
  ELSE
    IF NEW.prev_hash <> expected_prev THEN
      RAISE EXCEPTION 'Hash chain break for tenant %, scope %', NEW.tenant_id, NEW.chain_scope;
    END IF;
  END IF;

  IF NEW.event_hash IS NULL OR length(NEW.event_hash) <> 64 THEN
    RAISE EXCEPTION 'event_hash must be a 64-char SHA-256 hex digest';
  END IF;

  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_enforce_event_chain
BEFORE INSERT ON event_log
FOR EACH ROW
EXECUTE FUNCTION enforce_event_chain_integrity();

CREATE TABLE replay_verifications (
  replay_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
  project_id UUID REFERENCES projects(project_id) ON DELETE SET NULL,
  from_seq BIGINT NOT NULL,
  to_seq BIGINT NOT NULL,
  expected_root_hash CHAR(64) NOT NULL,
  computed_root_hash CHAR(64) NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ok', 'mismatch', 'error')),
  details JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (to_seq >= from_seq)
);

CREATE INDEX idx_replay_verifications_tenant ON replay_verifications (tenant_id, created_at DESC);

CREATE TABLE webhook_endpoints (
  webhook_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  project_id UUID REFERENCES projects(project_id) ON DELETE CASCADE,
  target_url TEXT NOT NULL,
  secret_hash TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  events TEXT[] NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_webhook_endpoints_tenant ON webhook_endpoints (tenant_id, enabled);

CREATE TABLE webhook_deliveries (
  delivery_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  webhook_id UUID NOT NULL REFERENCES webhook_endpoints(webhook_id) ON DELETE CASCADE,
  execution_id TEXT REFERENCES executions(execution_id) ON DELETE SET NULL,
  event_type TEXT NOT NULL,
  payload JSONB NOT NULL,
  signature TEXT NOT NULL,
  status webhook_delivery_state_enum NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  last_http_status INTEGER,
  next_attempt_at TIMESTAMPTZ,
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  delivered_at TIMESTAMPTZ
);

CREATE INDEX idx_webhook_deliveries_retry ON webhook_deliveries (status, next_attempt_at);
CREATE INDEX idx_webhook_deliveries_tenant ON webhook_deliveries (tenant_id, created_at DESC);

CREATE TABLE invariant_runs (
  run_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  checker_name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ok', 'warning', 'error')),
  fail_count INTEGER NOT NULL DEFAULT 0 CHECK (fail_count >= 0),
  details JSONB NOT NULL DEFAULT '{}'::jsonb,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ,
  duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0)
);

CREATE INDEX idx_invariant_runs_tenant ON invariant_runs (tenant_id, started_at DESC);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at := NOW();
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_tenants_updated_at BEFORE UPDATE ON tenants FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_projects_updated_at BEFORE UPDATE ON projects FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_api_keys_updated_at BEFORE UPDATE ON api_keys FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_provider_credentials_updated_at BEFORE UPDATE ON provider_credentials FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_executions_updated_at BEFORE UPDATE ON executions FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_settlement_outbox_updated_at BEFORE UPDATE ON settlement_outbox FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_webhook_endpoints_updated_at BEFORE UPDATE ON webhook_endpoints FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;

-- Application transaction contract (must be used by api/worker code):
--   BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE;
--   SELECT ... FOR UPDATE reservation_ledger WHERE execution_id = $1;
--   perform reserve/commit/release transitions with monotonic state checks;
--   INSERT event_log row with correct prev_hash/event_hash;
--   INSERT/UPDATE settlement_outbox for side effects;
--   COMMIT;
