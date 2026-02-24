# AEX Production Real Checks

This test pack validates a live `v2.1` deployment using real API calls.

## What It Tests

- smoke: `/health`, `/ready`, `/admin/alerts`, `/admin/dashboard/data`
- auth: missing token and invalid token behavior on `/v1/chat/completions`
- proxy real calls:
  - `/v1/chat/completions`
  - `/v1/responses`
  - `/v1/embeddings`
- idempotency:
  - same `Idempotency-Key` + same payload replay behavior
  - same `Idempotency-Key` + different payload conflict behavior (`409`)

## Required Inputs

- `AEX_PROD_BASE_URL` (example: `https://your-aex-service.example.com`)
- `AEX_PROD_AGENT_TOKEN` (or `AEX_AGENT_TOKEN`)

Optional:

- `AEX_TEST_CHAT_MODEL` (default `gpt-oss-20b`)
- `AEX_TEST_EMBEDDING_MODEL` (default `text-embedding-3-small`)
- `AEX_TENANT_ID`, `AEX_PROJECT_ID` (if you want explicit scope headers)
- `AEX_PROVIDER_API_KEY` with `--use-passthrough-provider-key` for `x-aex-provider-key`

## Run

```bash
cd /home/lenovo/Documents/aex
python3 scripts/prod_real_checks.py \
  --base-url "$AEX_PROD_BASE_URL" \
  --token "$AEX_PROD_AGENT_TOKEN"
```

With passthrough provider key:

```bash
python3 scripts/prod_real_checks.py \
  --base-url "$AEX_PROD_BASE_URL" \
  --token "$AEX_PROD_AGENT_TOKEN" \
  --provider-api-key "$AEX_PROVIDER_API_KEY" \
  --use-passthrough-provider-key
```

## Output

Reports are written to:

- `scripts/prod_checks/results/*.json`
- `scripts/prod_checks/results/*.md`

Exit code is non-zero if any check fails.
