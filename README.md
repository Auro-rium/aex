<p align="center">
  <h1 align="center">AEX — AI Execution Kernel</h1>
  <p align="center">
    Local-first governance layer for AI agent execution.<br>
    Budget enforcement · Rate limiting · Capability policies · Audit trails · OpenAI-compatible proxy.
  </p>
</p>

<p align="center">
  <a href="https://github.com/Auro-rium/aex"><img src="https://img.shields.io/badge/version-1.2.0-blue" alt="Version"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-green" alt="Python 3.11+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-brightgreen" alt="MIT License"></a>
  <a href="https://github.com/Auro-rium/aex/issues"><img src="https://img.shields.io/badge/issues-welcome-orange" alt="Issues"></a>
</p>

---

## What is AEX?

AEX sits between your AI agents and LLM providers. Every API call passes through AEX, where it is **authenticated**, **budget-checked**, **rate-limited**, **policy-validated**, and **logged** — before being forwarded to the real provider.

Your agents see a standard OpenAI-compatible API. They never know AEX is there.

```
  Agent Frameworks (LangGraph, CrewAI, AutoGen, raw SDK)
          │
          │  OPENAI_BASE_URL=http://127.0.0.1:9000/v1
          │  OPENAI_API_KEY=<agent-token>
          ▼
    ┌─────────────────────┐
    │     AEX Daemon      │
    │                     │
    │  ✦ Auth (SHA-256)   │
    │  ✦ Policy Engine    │
    │  ✦ Budget Enforcer  │
    │  ✦ Rate Limiter     │
    │  ✦ Audit Logger     │
    └──────────┬──────────┘
               ▼
    ┌─────────────────────┐
    │  LLM Providers      │
    │  (Groq · OpenAI)    │
    └─────────────────────┘
```

## Install

```bash
pip install aex
```

Or install from source:

```bash
git clone https://github.com/Auro-rium/aex.git
cd aex
pip install -e .
```

## Quick Start

```bash
# 1. Initialize AEX (creates ~/.aex with config + database)
aex init

# 2. Set your provider API key
echo "GROQ_API_KEY=gsk_your_key_here" > ~/.aex/.env

# 3. Start the daemon
aex daemon start

# 4. Create an agent with $5 budget and 30 RPM limit
aex agent create my-agent 5.00 30

# 5. Use the token with any OpenAI-compatible SDK
export OPENAI_BASE_URL=http://127.0.0.1:9000/v1
export OPENAI_API_KEY=<token-from-step-4>
```

That's it. Every call your agent makes is now governed.

## Framework Integration

AEX works with **any** framework that uses the OpenAI SDK protocol:

| Framework | Integration |
|-----------|------------|
| **LangGraph** | Set `OPENAI_BASE_URL` + `OPENAI_API_KEY` env vars |
| **CrewAI** | Set `OPENAI_BASE_URL` + `OPENAI_API_KEY` env vars |
| **AutoGen** | Set `OPENAI_BASE_URL` + `OPENAI_API_KEY` env vars |
| **OpenAI SDK** | `openai.OpenAI(base_url=..., api_key=...)` |
| **AEX Helper** | `from aex.integrations import get_openai_client` |

### Python Helper

```python
from aex.integrations import get_openai_client

client = get_openai_client("my-agent")
response = client.chat.completions.create(
    model="gpt-oss-20b",
    messages=[{"role": "user", "content": "Hello"}]
)
```

### Run a script as an agent

```bash
aex run --agent my-agent python my_script.py
```

This injects `OPENAI_BASE_URL` and `OPENAI_API_KEY` automatically.

## Features

| Feature | Description |
|---------|-------------|
| **Budget Enforcement** | Per-agent USD budgets, integer micro-unit accounting (no float drift) |
| **Rate Limiting** | Per-agent RPM caps with sliding window |
| **Capability Policies** | Model whitelist, tool/streaming/vision toggles, strict mode |
| **Token Security** | SHA-256 hashed storage, entropy validation, optional TTL |
| **Token Scopes** | `execution` or `read-only` |
| **Passthrough Mode** | Agents bring own provider keys — governance still enforced |
| **Streaming Support** | Full SSE relay with post-stream cost settlement |
| **Audit Trail** | Every request, denial, and policy violation logged |
| **Dashboard** | Live metrics at `http://127.0.0.1:9000/dashboard` |
| **Invariant Verification** | `aex audit` formally verifies ledger integrity |
| **Stress Tested** | 10/10 infra assault certification (concurrency, budget collapse, rate limits) |

## CLI Reference

### Daemon

```bash
aex daemon start [--port 9000]   # Start the proxy daemon
aex daemon stop                  # Stop the daemon
aex daemon status                # Check if running
```

### Agents

```bash
# Create with full governance
aex agent create atlas 10.00 30 \
  --allowed-models "gpt-oss-20b" \
  --no-streaming \
  --strict \
  --ttl 24 \
  --scope execution

# Passthrough mode (agent provides own API key)
aex agent create proxy-agent 5.00 30 --allow-passthrough

# Management
aex agent inspect atlas          # Full details + token
aex agent list                   # All agents table
aex agent list --verbose         # Show micro-units
aex agent delete atlas           # Delete + kill process
aex agent rotate-token atlas     # New token, old invalidated
```

### Operations

```bash
aex doctor                       # Environment health check
aex doctor --compat --token <t>  # Protocol fidelity tests
aex status                       # Enforcement summary
aex audit                        # Formal invariant verification
aex metrics                      # Per-agent financials + burn rate
aex models reload                # Hot-reload model config
aex version                      # Show version
```

## Configuration

AEX reads model definitions from `~/.aex/config/models.yaml`:

```yaml
version: 1

providers:
  groq:
    base_url: https://api.groq.com/openai/v1
    type: openai_compatible

default_model: gpt-oss-20b

models:
  gpt-oss-20b:
    provider: groq
    provider_model: llama-3.1-8b-instant
    pricing:
      input_micro: 50    # micro-USD per token
      output_micro: 100
    limits:
      max_tokens: 8192
    capabilities:
      reasoning: true
      tools: true
      vision: false
```

Provider API keys go in `~/.aex/.env`:

```bash
GROQ_API_KEY=gsk_...
OPENAI_API_KEY=sk-...
```

## Security Model

| Layer | Mechanism |
|-------|-----------|
| Token Storage | SHA-256 hash (raw token never stored for new agents) |
| Token Entropy | Minimum 128-bit (32 hex chars) |
| Token Expiry | Optional TTL in hours (supports fractional: `--ttl 0.5`) |
| Token Scope | `execution` or `read-only` |
| Provider Keys | Daemon-only — never exposed to agents |
| Passthrough | Opt-in per agent, governance still enforced |
| Budget Integrity | `CHECK` constraints + atomic transactions + invariant audits |

## Accounting Guarantees

AEX uses **integer micro-units** (1 USD = 1,000,000 µ) with SQLite `CHECK` constraints to provide:

- **No negative balances** — `spent_micro >= 0` enforced at database level
- **No overspends** — `spent_micro <= budget_micro` enforced at database level
- **Ledger integrity** — `SUM(events.cost_micro)` always equals `agents.spent_micro`
- **Atomic transactions** — Budget mutations and event logging happen in the same `BEGIN IMMEDIATE` transaction

Verify anytime with `aex audit`.

## License

[MIT](LICENSE)
