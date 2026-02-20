<p align="center">
  <h1 align="center">AEX — Auto Execution Kernel</h1>
  <p align="center">
    Local-first governance layer for AI agent execution.<br>
    <em>Budget enforcement · Rate limiting · Capability policies · Audit trails · OpenAI-compatible proxy</em>
  </p>
</p>

<p align="center">
  <a href="https://github.com/Auro-rium/aex"><img src="https://img.shields.io/badge/version-1.2.1-blue" alt="Version"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-green" alt="Python 3.11+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-brightgreen" alt="MIT License"></a>
</p>

---

## What is AEX?

AEX is a lightweight **local proxy** that sits between your AI agents and LLM providers (like Groq, OpenAI). It ensures that every API call is **authenticated**, **budget-checked**, **rate-limited**, **policy-validated**, and **logged** — all before reaching the real provider.

Because AEX acts as a standard OpenAI-compatible API, **your agents work with it out of the box** without knowing AEX is there!

<details>
<summary><strong>Visualize the Architecture</strong></summary>

```text
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

</details>

## What Can You Do With AEX?

AEX lets you completely govern AI agents running locally or in autonomous loops. Here's how:

<details>
<summary>💰 <strong>Set Hard Budgets (No Overspending)</strong></summary>
Give an agent exactly $5.00 to complete a task. AEX uses integer micro-unit accounting, meaning absolute precision and no floating-point drift. When the budget is gone, the agent is cut off (HTTP `402 Payment Required`).
</details>

<details>
<summary>⏱️ <strong>Apply Rate Limits (RPM)</strong></summary>
Prevent runaway agent loops. Set an agent to a maximum of 30 Requests Per Minute (RPM); any more and they are cleanly blocked until the window rolls over.
</details>

<details>
<summary>🛡️ <strong>Restrict Capabilities (Model Whitelists & Strict Mode)</strong></summary>
Force an agent to only use `gpt-oss-20b` (e.g., Llama 3 on Groq). Disable streaming. Disable vision. Or enable **Strict Mode**, which validates every request token against their exact allowed scopes.
</details>

<details>
<summary>🕵️ <strong>Audit Every Request</strong></summary>
Every prompt, every tool call, every token chunk, and every denial is logged to the local SQLite database. Use `aex metrics` or `aex audit` to track burns and ledger integrity anytime.
</details>

---

## 🚀 Quick Start

```bash
# 1. Install
pip install aex

# 2. Initialize (creates ~/.aex config and prompts for API keys)
aex init

# 3. Start the background proxy daemon
aex daemon start

# 4. Create an agent with a $5 budget and 30 RPM limit
aex agent create my-agent 5.00 30
# Copy the generated `Token:` output

# 5. Use the token with your favorite framework!
export OPENAI_BASE_URL=http://127.0.0.1:9000/v1
export OPENAI_API_KEY=<token-from-step-4>
```

---

## 🛠️ Framework Integration

AEX works instantly with **any** framework that supports the OpenAI SDK:

| Framework | How to Integrate |
|-----------|------------------|
| **LangGraph / CrewAI / AutoGen** | Export `OPENAI_BASE_URL` + `OPENAI_API_KEY` |
| **OpenAI SDK** | `openai.OpenAI(base_url=..., api_key=...)` |
| **AEX Python Helper** | `from aex.integrations import get_openai_client` |

### Python Helper Example

```python
from aex.integrations import get_openai_client

# Automatically fetches the locally stored token for "my-agent"
client = get_openai_client("my-agent")

response = client.chat.completions.create(
    model="gpt-oss-20b",
    messages=[{"role": "user", "content": "Hello AEX!"}]
)
print(response.choices[0].message.content)
```

### Run a Script as an Agent

```bash
aex run --agent my-agent python my_script.py
```
*(This command automatically injects `OPENAI_BASE_URL` and `OPENAI_API_KEY` into `my_script.py`'s environment variables.)*

---

## 💻 CLI Operations Reference

Expand the sections below to see all the powerful management tools.

<details>
<summary><strong>Daemon Controls</strong></summary>

```bash
aex daemon start [--port 9000]   # Start the proxy daemon
aex daemon stop                  # Stop the daemon
aex daemon status                # Check if running
```
</details>

<details>
<summary><strong>Agent Management</strong></summary>

```bash
# Create with strict governance rules
aex agent create atlas 10.00 30 \
  --allowed-models "gpt-oss-20b" \
  --no-streaming \
  --strict \
  --ttl 24 \
  --scope execution

# Passthrough Mode (Agent uses its own OpenAI API key, but governance is still applied)
aex agent create proxy-agent 5.00 30 --allow-passthrough

# Manage existing agents
aex agent inspect atlas          # View budget, limits, and token
aex agent list                   # View all agents
aex agent delete atlas           # Delete agent and active token
aex agent rotate-token atlas     # Invalidate old token, generate new
```
</details>

<details>
<summary><strong>Monitoring & Auditing</strong></summary>

```bash
aex metrics                      # Live burn rates and spending metrics
aex status                       # Enforcement and daemon summaries
aex audit                        # Formally verify the database ledger for leaks
aex doctor                       # Environment health check
```
</details>

---

## ⚙️ Configuration & Models

AEX manages model mappings inside `~/.aex/config/models.yaml`. 
You define "AEX models" in the config, and map them to real provider models and prices (in micro-USD: $1 = 1,000,000µ).

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
```

*Update provider keys directly in `~/.aex/.env` if you missed them during `aex init`.*

## License

[MIT](LICENSE)
