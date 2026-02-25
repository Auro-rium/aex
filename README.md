# AEX v2.1

AEX is governance middleware for production agents. You run your agents on your infra; AEX enforces policy, budgets, observability, and operator controls.

## Prod User Flow

1. Export key

```bash
export AEX_API_KEY=xxx
```

2. Create policy (UI-first, JSON policy payload)

```json
{
  "budget_usd": 50,
  "allow_tools": ["search", "github", "slack"],
  "deny_tools": ["shell", "db_write"],
  "max_steps": 100,
  "dangerous_ops": false,
  "require_approval_for_destructive_ops": true
}
```

3. Wrap agent in one line

```python
from aex import AEX, wrap, enable

agent = AEX.wrap(MyAgent(), policy_id="prod_safe")
# equivalent
agent = wrap(MyAgent(), policy_id="prod_safe", tenant="acme", project="prod")
enable()  # env-driven global interception mode
agent.run()
```

Simplest prod one-liner (no pre-exported env needed):

```python
from aex import wrap

agent = wrap(
    MyAgent(),
    api_key="aex_live_token",
    base_url="https://aex-production.up.railway.app",
    tenant="acme",
    project="prod",
)
agent.run()
```

Fastest team workflow (set once, then no token repetition):

```python
from aex import login, wrap

login(
    api_key="aex_live_token",
    base_url="https://aex-production.up.railway.app",
    tenant="acme",
    project="prod",
)

agent = wrap(MyAgent())  # no token/base_url needed now
agent.run()
```

```javascript
const agent = AEX.wrap(MyAgent, "prod_safe");
await agent.run();
```

4. Deploy normally (AEX does not force hosting)

- AWS
- Vercel
- Fly
- Kubernetes

5. Observe in dashboard

- `/dashboard`
- `/admin/console` (full UI command center replacing CLI workflows)
- `/admin/dashboard/data`
- `/admin/alerts`
- `/admin/replay`
- UI no-code onboarding wizard:
  - open `/dashboard`
  - use **No-Code Onboarding** -> **Create Connect Pack**
  - copy generated `.env` block and smoke curl
 - UI command center:
   - open `/admin/console`
   - test/set runtime DB DSN
   - manage tenants/projects/agents/policies/plugins
   - run replay/audit and migration snapshot/apply/rollback

6. Control instantly

- Pause all: `POST /admin/control/pause_all`
- Sandbox all: `POST /admin/control/sandbox_all`
- Kill all agents: `POST /admin/control/kill_all`
- Optional hardening: set `AEX_ADMIN_CONTROL_KEY` and send `x-aex-admin-key` for control actions.

## Run Local

```bash
pip install -e .
export AEX_PG_DSN="postgresql://aex:aex@127.0.0.1:5432/aex"
uvicorn aex.daemon.app:app --host 127.0.0.1 --port 9000
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

## Production Validation

- smoke: `scripts/prod_smoke.sh`
- real checks: `python3 scripts/prod_real_checks.py --base-url <url> --token <agent_token>`
