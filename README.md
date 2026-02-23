# AEX v2.1

PostgreSQL-first execution governance kernel for LLM/agent traffic.

## Run Local

```bash
pip install -e .
export AEX_PG_DSN="postgresql://aex:aex@127.0.0.1:5432/aex"

aex init
aex daemon start
```

Health:

```bash
curl -s http://127.0.0.1:9000/health | jq
curl -s http://127.0.0.1:9000/ready | jq
```

## Docker

```bash
docker compose up -d --build
```

- AEX: `9000` (override `AEX_HTTP_PORT`)
- Postgres: `5433` (override `AEX_POSTGRES_HOST_PORT`)

## Core Capabilities

- deterministic idempotent admission (`execution_id`)
- budget reserve/commit/release settlement
- hash-chained event ledger + replay checks
- OpenAI-compatible proxy (`/v1/*`, `/openai/v1/*`)
- crash recovery sweeps + invariant checks
- backend dashboard payload (`/admin/dashboard/data`)

## Migrate / Rollback

```bash
aex migrate snapshot --tag pre_change
aex migrate apply --snapshot-first --tag pre_change
aex migrate rollback --tag pre_change
```

## Production

- Docker backend (any VPS/provider)
- Vercel edge proxy: `deploy/vercel/vercel.json`
- set proxy destination to your backend domain
- smoke check: `scripts/prod_smoke.sh`
