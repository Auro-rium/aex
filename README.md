# AEX — Auto Execution Kernel

Local-first governance layer for AI agent execution.
**Budget enforcement · Token governance · Capability policies · Multi-provider routing · Deterministic audit ledger**

---

## Why AEX Exists

AI agents do not stop on their own.

They loop.
They retry.
They escalate.
They overspend.

AEX is the boundary that stops them.

It sits between your agents and LLM providers and enforces:

* Hard spending limits
* Token rate ceilings
* Model restrictions
* Capability policies
* Audit logging with ledger integrity

All before a single token reaches the provider.

---

## Architecture Overview

```
  Agent Frameworks (LangGraph, CrewAI, AutoGen, SDK)
          │
          │  OPENAI_BASE_URL=http://127.0.0.1:9000/v1
          │  OPENAI_API_KEY=<agent-token>  (AEX token, not provider key)
          ▼
    ┌────────────────────────────┐
    │         AEX Kernel         │
    │                            │
    │  ✓ Auth & Identity         │
    │  ✓ Budget Reservation      │
    │  ✓ Token Rate Limiting     │
    │  ✓ Model Policy Checks     │
    │  ✓ Ledger Commit (Atomic)  │
    └──────────────┬─────────────┘
                   ▼
         LLM Providers (Groq, OpenAI, etc.)
```

Northbound: OpenAI-compatible
Southbound: Multi-provider routing

---

# Provider Key Ownership

AEX supports two key ownership modes:

* Daemon-owned provider keys (default):
  operator sets provider env vars on AEX host (for example `GROQ_API_KEY`).
* Agent-owned provider keys (passthrough):
  agent sends its own provider key per request via `x-aex-provider-key`.
* Fallback behavior:
  if `x-aex-provider-key` is missing, AEX uses daemon provider env key (if present).

If you do **not** want users consuming your Groq key:

* Do not share host env/files.
* Create agents with `--allow-passthrough`.
* Require clients to send their own provider key header.

Strict BYOK mode (recommended for shared environments):

* Do **not** set daemon provider key env vars for that provider (for example `GROQ_API_KEY`).
* Only accept requests with `x-aex-provider-key`.
* Keep per-agent controls (`--allowed-models`, budgets, RPM) in place.

---

# Interactive Enforcement Scenarios

## 1. Hard Budget Cutoff

Create an agent with $0.01:

```bash
aex agent create test-agent 0.01 60
```

Run a script that loops requests.

When budget hits zero:

```json
HTTP 402
{
  "detail": "Insufficient budget"
}
```

Execution stops immediately.

---

## 2. Token Per Minute (TPM) Limit

Configure in models.yaml:

```yaml
limits:
  max_tokens_per_minute: 1000
```

Send rapid requests.

When exceeded:

```json
HTTP 429
{
  "detail": "TPM Rate limit exceeded"
}
```

Throttle occurs before provider.

---

## 3. Model Whitelist Enforcement

Create an agent restricted to one model:

```bash
aex agent create atlas 10.00 30 --allowed-models "gpt-oss-20b"
```

If agent attempts another model:

```json
HTTP 403
{
  "detail": "Model not allowed"
}
```

Policy enforced pre-forward.

---

## 4. Live Budget Severing

While agent runs:

```sql
UPDATE agents SET budget_micro = spent_micro WHERE name='atlas';
```

Next generation attempt:

```json
HTTP 402
```

Live governance without restarting daemon.

---

# Multi-Provider Support

Define providers in `~/.aex/config/models.yaml`:

```yaml
providers:
  groq:
    base_url: https://api.groq.com/openai/v1
  openai:
    base_url: https://api.openai.com/v1
  ollama:
    base_url: http://localhost:11434/v1

models:
  gpt-oss-20b:
    provider: groq
  gpt-4:
    provider: openai
  llama-local:
    provider: ollama
```

Frameworks remain unchanged.
Routing handled internally.

---

## User-Owned Key (Passthrough) Example

Create an agent that must use its own provider key:

```bash
aex agent create my-agent 5.00 30 --allow-passthrough
```

Call AEX with:

* `Authorization: Bearer <AEX_AGENT_TOKEN>`
* `x-aex-provider-key: <USER_PROVIDER_KEY>`

Example:

```bash
curl http://127.0.0.1:9000/v1/chat/completions \
  -H "Authorization: Bearer <AEX_AGENT_TOKEN>" \
  -H "x-aex-provider-key: <USER_LLM_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
        "model": "gpt-oss-20b",
        "messages": [{"role":"user","content":"hello"}]
      }'
```

---

# Quick Start

```bash
# Install
pip install aex

# Initialize configuration (~/.aex)
aex init

# Start daemon
aex daemon start

# Create agent with $5 budget + 30 RPM
aex agent create my-agent 5.00 30

# Export environment variables
export OPENAI_BASE_URL=http://127.0.0.1:9000/v1
export OPENAI_API_KEY=<token-from-create>
```

---

# Framework Integration

Works with any OpenAI-compatible client:

* LangGraph
* CrewAI
* AutoGen
* smolagents
* Raw SDK
* curl

Example (Python SDK):

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:9000/v1",
    api_key="<AEX_TOKEN>"
)

resp = client.chat.completions.create(
    model="gpt-oss-20b",
    messages=[{"role": "user", "content": "Hello AEX"}]
)

print(resp.choices[0].message.content)
```

---

# CLI Management

Start daemon:

```bash
aex daemon start
```

Check status:

```bash
aex status
```

View live metrics:

```bash
aex metrics
```

Verify ledger integrity:

```bash
aex audit
```

Rotate agent token:

```bash
aex agent rotate-token my-agent
```

---

# Financial Integrity Guarantee

AEX enforces invariant:

```
agents.spent_micro ==
SUM(events.cost_micro WHERE action='usage.commit')
```

Verified under:

* Multi-agent concurrency
* Token expiry
* Rate bursts
* Budget exhaustion
* Dynamic severing
* Provider 429

Run:

```bash
aex audit
```

---

# What AEX Is Not

* Not an agent framework
* Not a SaaS dashboard
* Not vendor locked
* Not a cost-optimizer

It is a local execution governance kernel.

---

# Roadmap

* Global concurrency caps
* Session token ceilings
* Capability-level tool governance
* Crash recovery hardening
* Multi-provider fallback routing

---

If you run autonomous AI agents and care about:

* Not overspending
* Not losing control
* Not cross-contaminating budgets
* Not relying on external dashboards

AEX is the boundary layer.
