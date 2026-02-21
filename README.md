# AEX — AI Execution Kernel

> Local-first governance layer for AI agent execution.  
> Budget enforcement · Capability policies · Audit trails · OpenAI-compatible proxy.

## Quick Start

```bash
pip install -e .
aex init
aex daemon start
aex agent create atlas 5.00 30
aex doctor
```

## Architecture

```
┌──────────────────────────────────────────────┐
│           Agent Frameworks                    │
│  (LangGraph · CrewAI · AutoGen · raw SDK)    │
│                                               │
│  OPENAI_BASE_URL=http://127.0.0.1:9000/v1   │
│  OPENAI_API_KEY=<agent-token>                │
└──────────────┬───────────────────────────────┘
               │
    ┌──────────▼──────────┐
    │     AEX Daemon      │
    │                     │
    │  Auth (SHA-256)     │
    │  Policy Engine      │
    │  Budget Enforcer    │
    │  Rate Limiter       │
    │  Audit Logger       │
    └──────────┬──────────┘
               │
    ┌──────────▼──────────┐
    │  LLM Providers      │
    │  (Groq · OpenAI)    │
    └─────────────────────┘
```

## Features (v1.2)

| Feature | Description |
|---|---|
| **Budget Enforcement** | Per-agent budget in USD, integer micro-unit accounting |
| **Rate Limiting** | Per-agent RPM caps |
| **Capability Policies** | Model whitelist, tool/streaming/vision toggles, strict mode |
| **Token Hashing** | SHA-256 hashed storage, entropy validation |
| **Token TTL** | Optional time-to-live (hours) on agent tokens |
| **Token Scopes** | `execution` or `read-only` |
| **Passthrough Mode** | Agents bring own provider keys, governance still active |
| **Compatibility Contract** | 100% OpenAI protocol fidelity, verifiable via `aex doctor --compat` |
| **Audit Trail** | Every request, denial, and violation logged |
| **Dashboard** | Live metrics at `http://127.0.0.1:9000/dashboard` |
| **Framework Integration** | `aex.integrations` helpers for Python |

## Framework Compatibility

| Framework | Integration Method |
|---|---|
| **LangGraph** | `OPENAI_BASE_URL` + `OPENAI_API_KEY` env vars |
| **CrewAI** | `OPENAI_BASE_URL` + `OPENAI_API_KEY` env vars |
| **AutoGen** | `OPENAI_BASE_URL` + `OPENAI_API_KEY` env vars |
| **OpenAI SDK** | `openai.OpenAI(base_url=..., api_key=...)` |
| **AEX Helper** | `from aex.integrations import get_openai_client` |

### Python Integration

```python
from aex.integrations import get_openai_client

client = get_openai_client("my-agent")
response = client.chat.completions.create(
    model="gpt-oss-20b",
    messages=[{"role": "user", "content": "Hello"}]
)
```

## Agent Management

```bash
# Create with capabilities
aex agent create atlas 10.00 30 \
  --allowed-models "gpt-oss-20b,gpt-oss-70b" \
  --no-streaming \
  --strict \
  --ttl 24 \
  --scope execution

# Passthrough mode
aex agent create proxy-agent 5.00 30 --allow-passthrough

# Inspect
aex agent inspect atlas

# Rotate token with new TTL
aex agent rotate-token atlas --ttl 48

# List all
aex agent list
```

## CLI Reference

| Command | Description |
|---|---|
| `aex init` | Initialize `~/.aex` directory |
| `aex daemon start/stop/status` | Manage daemon lifecycle |
| `aex agent create/inspect/delete/list` | Agent CRUD |
| `aex agent rotate-token` | Rotate agent API token |
| `aex run --agent <name> <cmd>` | Run command as agent |
| `aex doctor` | Environment health check |
| `aex doctor --compat --token <t>` | Protocol fidelity tests |
| `aex status` | Enforcement summary |
| `aex audit` | Formal invariant verification |
| `aex metrics` | Per-agent financials, burn rate, TTB |
| `aex models reload` | Hot-reload model config |
| `aex version` | Show version |

## Security Model

| Layer | Mechanism |
|---|---|
| Token Storage | SHA-256 hash (raw never stored for new agents) |
| Token Entropy | Minimum 128-bit (32 hex chars) |
| Token Expiry | Optional TTL in hours |
| Token Scope | `execution` or `read-only` |
| Provider Keys | Daemon-only, never exposed to agents |
| Passthrough | Opt-in per agent, governance still enforced |

## License

MIT
