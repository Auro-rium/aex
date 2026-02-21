# AEX — AI Execution Kernel

**Production-grade local proxy for AI agent governance.**

AEX is the mandatory boundary between your AI agents and LLM providers.
It enforces per-agent budgets, rate limits, model policies, and capability restrictions —
transparently, with zero code changes in your agent code.

## Install

```bash
pip install git+ssh://git@github.com/Auro-rium/aex.git
```

Or via pipx (recommended for CLI tools):

```bash
pipx install git+ssh://git@github.com/Auro-rium/aex.git
```

Pin to a release:

```bash
pip install git+ssh://git@github.com/Auro-rium/aex.git@v1.1.0
```

## Quick Start

```bash
# Initialize the AEX environment
aex init

# Set your provider API key
echo "GROQ_API_KEY=your-key-here" > ~/.aex/.env

# Start the kernel daemon
aex daemon start

# Create an agent with $10 budget and 60 RPM limit
aex agent create my-agent 10 60

# Run your script under AEX governance (zero code changes)
aex run --agent my-agent python my_script.py
```

Your script uses `OpenAI()` as normal — AEX intercepts all calls
via `OPENAI_BASE_URL` injection. Streaming is fully supported.

## What It Does

| Feature              | Description                                           |
|----------------------|-------------------------------------------------------|
| Budget enforcement   | Integer micro-unit accounting, no float drift          |
| Rate limiting        | Per-agent RPM limits                                   |
| Model governance     | Reject unknown models, enforce capability gates        |
| Transparent proxy    | Agents see standard OpenAI API, AEX maps to providers |
| Streaming            | Full SSE passthrough with cost settlement              |
| Crash recovery       | Stale reservations cleared on restart                  |
| Config hot-reload    | Atomic — keeps old config on validation failure        |
| PID supervision      | Track and kill runaway agent processes                 |

## CLI Reference

```
aex version                     # Show version
aex init                        # Initialize ~/.aex
aex daemon start                # Start the kernel daemon
aex daemon stop                 # Stop the daemon
aex daemon status               # Check daemon status
aex agent create NAME USD RPM   # Create a new agent
aex agent list [--verbose]      # List all agents
aex agent inspect NAME          # Show agent details + token
aex agent delete NAME           # Delete agent and clean up
aex agent rotate-token NAME     # Rotate API token
aex models reload               # Hot-reload model configuration
aex run --agent NAME CMD...     # Execute under AEX governance
aex metrics                     # Display system metrics
```

## Configuration

Models are configured in `~/.aex/config/models.yaml`:

```yaml
version: 1

providers:
  groq:
    base_url: https://api.groq.com/openai/v1
    type: openai_compatible

models:
  gpt-oss-20b:
    provider: groq
    provider_model: llama-3.1-8b-instant
    pricing:
      input_micro: 50
      output_micro: 100
    limits:
      max_tokens: 8192
    capabilities:
      reasoning: true
      tools: true
      vision: false
```

## License

MIT
