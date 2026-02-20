# 🚀 Using AEX with OpenClaw (AEX Local, Provider Optional)

This guide explains how a developer running **OpenClaw locally** can use **AEX** as execution decision infrastructure in front of it.

Groq/OpenAI are optional. Local LLMs are also supported.
We will route OpenClaw → AEX → LLM Service.

---

# 🧠 Architecture Overview

```
OpenClaw
    ↓
AEX (localhost:9000)
    ↓
LLM Service (Ollama / LM Studio / vLLM / llama.cpp / Groq / OpenAI)
```

AEX becomes the decision layer for each agent action:

* Budget enforcement
* Rate limiting
* Capability control
* Audit logging

---

# 1️⃣ Prerequisites

You must have:

* Python 3.11+
* AEX installed
* OpenClaw installed
* A reachable LLM endpoint (local or remote)

Recommended local LLM options:

• Ollama
• LM Studio
• llama.cpp server
• vLLM

Example (Ollama):

```
ollama run llama3
```

By default Ollama runs at:

```
http://localhost:11434
```

---

# 2️⃣ Configure AEX Routing (Local Example)

Inside:

```
~/.aex/config/models.yaml
```

Configure an OpenAI-compatible provider. Example below uses Ollama (local).

Example configuration:

```yaml
version: 1

providers:
  local:
    base_url: http://localhost:11434/v1
    type: openai_compatible

models:
  llama3-local:
    provider: local
    provider_model: llama3
    pricing:
      input_micro: 1
      output_micro: 2
    limits:
      max_tokens: 4096
    capabilities:
      reasoning: true
      tools: true
      vision: false

default_model: llama3-local
```

Set provider key env var for AEX (dummy is fine for Ollama):

```bash
export LOCAL_API_KEY=dummy
```

If daemon is already running, reload models:

```bash
python -m aex models reload
```

---

# 3️⃣ Start AEX Daemon

```bash
python -m aex daemon start
```

Verify:

```bash
python -m aex doctor
```

You should see:

```
Daemon Health: ok
```

---

# 4️⃣ Create an Agent

Example:

```bash
python -m aex agent create dev-agent 5 120 --allowed-models llama3-local
```

This creates:

* $5 logical budget
* 120 RPM limit
* Access only to llama3-local

Copy the token.

---

# 5️⃣ Configure OpenClaw to Use AEX

Set environment variables:

```bash
export OPENAI_BASE_URL=http://localhost:9000/v1
export OPENAI_API_KEY=<YOUR_AGENT_TOKEN>
```

Now OpenClaw thinks it is talking to OpenAI.

But it is actually talking to AEX.

AEX forwards to your configured LLM provider.

---

# 6️⃣ Test With Curl

```bash
curl http://localhost:9000/v1/chat/completions \
  -H "Authorization: Bearer <YOUR_AGENT_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
        "model": "llama3-local",
        "messages": [{"role":"user","content":"hello"}],
        "max_tokens": 50
      }'
```

If it returns a response → system is wired correctly.

---

# 7️⃣ What AEX Decides Per Agent Action

For every request, AEX evaluates and enforces:

✔ Budget limits
✔ Rate limits
✔ Model restrictions
✔ Capability flags
✔ Audit logging
✔ Token isolation per agent

If budget exceeds:

Requests return HTTP 402.

If RPM exceeded:

Requests return HTTP 429.

---

# 8️⃣ Why This Helps OpenClaw

Without AEX:

OpenClaw → LLM directly.
No guardrails.

With AEX:

OpenClaw → AEX → LLM

Now you can:

* Cap runaway agents
* Prevent model abuse
* Track usage per agent
* Run multi-agent experiments safely

---

# 9️⃣ Deployment Modes

You can run:

* Local AEX + Local LLMs (fully local inference)
* Local AEX + Remote APIs (Groq/OpenAI/others)

AEX stays the decision layer in both modes.

---

# 🔟 Minimal Workflow Summary

```bash
# Start local LLM
ollama run llama3

# Provider key env var used by AEX for provider "local"
export LOCAL_API_KEY=dummy

# Start AEX
python -m aex daemon start

# Create agent
python -m aex agent create dev 5 120 --allowed-models llama3-local

# Point OpenClaw to AEX
export OPENAI_BASE_URL=http://localhost:9000/v1
export OPENAI_API_KEY=<TOKEN>
```

Done.

---

# 🧭 What This Is

This setup turns AEX into:

Execution infrastructure that decides each agent action before it reaches a model.
