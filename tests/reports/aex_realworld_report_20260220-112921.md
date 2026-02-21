# AEX Real-World OpenClaw Report (20260220-112921)

## Topline
- AEX model route: `gpt-oss-20b`
- Agent count: `12`
- Requests per agent: `5`
- Total requests: `60`
- Success: `31`
- Errors: `29`
- Budget denials observed: `10`
- Rate limit denials observed: `19`

## AEX CLI Command Evidence
### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex version`
- return code: `0`
- stdout:
```text
AEX 1.2.0
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex init`
- return code: `0`
- stdout:
```text
Initializing AEX in /home/lenovo/Documents/aex/.sim_home/.aex...
Creating default models.yaml...
Database initialized.
AEX initialized successfully.
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex daemon status`
- return code: `0`
- stdout:
```text
Daemon is NOT running
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex daemon stop`
- return code: `0`
- stdout:
```text
Daemon not running (PID file not found)
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex daemon start`
- return code: `0`
- stdout:
```text
Starting AEX daemon on port 9000...
Daemon started with PID 213164
Logs: /home/lenovo/Documents/aex/.sim_home/.aex/logs/daemon.out
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex doctor`
- return code: `0`
- stdout:
```text
AEX Doctor v1.2.0

  ✅ AEX directory:  /home/lenovo/Documents/aex/.sim_home/.aex
  ✅ Database:       /home/lenovo/Documents/aex/.sim_home/.aex/aex.db
  ✅ DB Integrity:   PASS
  ✅ Config:         
/home/lenovo/Documents/aex/.sim_home/.aex/config/models.yaml
  ✅ Config Valid:   2 model(s)
  ✅ Daemon:         Running (PID 213164)
  ✅ Daemon Health:  ok (v1.2.0)
  ✅ .env file:      /home/lenovo/Documents/aex/.sim_home/.aex/.env

All checks passed.
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex models reload`
- return code: `0`
- stdout:
```text
Model configuration reloaded successfully.
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex agent create rw-20260220-112921-a00 1.2 120 --allowed-models gpt-oss-20b`
- return code: `0`
- stdout:
```text
Agent 'rw-20260220-112921-a00' created.
Token: ***TOKEN_REDACTED***
Budget: $1.20 (1200000 micro)
Capabilities: models=gpt-oss-20b
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex agent create rw-20260220-112921-a01 1.2 120 --allowed-models gpt-oss-20b`
- return code: `0`
- stdout:
```text
Agent 'rw-20260220-112921-a01' created.
Token: ***TOKEN_REDACTED***
Budget: $1.20 (1200000 micro)
Capabilities: models=gpt-oss-20b
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex agent create rw-20260220-112921-a02 1.2 120 --allowed-models gpt-oss-20b`
- return code: `0`
- stdout:
```text
Agent 'rw-20260220-112921-a02' created.
Token: ***TOKEN_REDACTED***
Budget: $1.20 (1200000 micro)
Capabilities: models=gpt-oss-20b
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex agent create rw-20260220-112921-a03 1.2 120 --allowed-models gpt-oss-20b`
- return code: `0`
- stdout:
```text
Agent 'rw-20260220-112921-a03' created.
Token: ***TOKEN_REDACTED***
Budget: $1.20 (1200000 micro)
Capabilities: models=gpt-oss-20b
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex agent create rw-20260220-112921-a04 1.2 120 --allowed-models gpt-oss-20b`
- return code: `0`
- stdout:
```text
Agent 'rw-20260220-112921-a04' created.
Token: ***TOKEN_REDACTED***
Budget: $1.20 (1200000 micro)
Capabilities: models=gpt-oss-20b
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex agent create rw-20260220-112921-a05 1.2 120 --allowed-models gpt-oss-20b`
- return code: `0`
- stdout:
```text
Agent 'rw-20260220-112921-a05' created.
Token: ***TOKEN_REDACTED***
Budget: $1.20 (1200000 micro)
Capabilities: models=gpt-oss-20b
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex agent create rw-20260220-112921-a06 1.2 120 --allowed-models gpt-oss-20b`
- return code: `0`
- stdout:
```text
Agent 'rw-20260220-112921-a06' created.
Token: ***TOKEN_REDACTED***
Budget: $1.20 (1200000 micro)
Capabilities: models=gpt-oss-20b
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex agent create rw-20260220-112921-a07 1.2 120 --allowed-models gpt-oss-20b`
- return code: `0`
- stdout:
```text
Agent 'rw-20260220-112921-a07' created.
Token: ***TOKEN_REDACTED***
Budget: $1.20 (1200000 micro)
Capabilities: models=gpt-oss-20b
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex agent create rw-20260220-112921-a08 1.2 120 --allowed-models gpt-oss-20b`
- return code: `0`
- stdout:
```text
Agent 'rw-20260220-112921-a08' created.
Token: ***TOKEN_REDACTED***
Budget: $1.20 (1200000 micro)
Capabilities: models=gpt-oss-20b
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex agent create rw-20260220-112921-a09 1.2 120 --allowed-models gpt-oss-20b`
- return code: `0`
- stdout:
```text
Agent 'rw-20260220-112921-a09' created.
Token: ***TOKEN_REDACTED***
Budget: $1.20 (1200000 micro)
Capabilities: models=gpt-oss-20b
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex agent create rw-20260220-112921-a10 0.004 120 --allowed-models gpt-oss-20b`
- return code: `0`
- stdout:
```text
Agent 'rw-20260220-112921-a10' created.
Token: ***TOKEN_REDACTED***
Budget: $0.00 (4000 micro)
Capabilities: models=gpt-oss-20b
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex agent create rw-20260220-112921-a11 0.004 120 --allowed-models gpt-oss-20b`
- return code: `0`
- stdout:
```text
Agent 'rw-20260220-112921-a11' created.
Token: ***TOKEN_REDACTED***
Budget: $0.00 (4000 micro)
Capabilities: models=gpt-oss-20b
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex agent list`
- return code: `0`
- stdout:
```text
AEX Agents                                   
┏━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━┳━━━━━━━┳━━━━━━┳━━━━━━━━━━┓
┃           ┃    Budget ┃    Spent ┃ Remaining ┃     ┃       ┃      ┃ Last     ┃
┃ Name      ┃       ($) ┃      ($) ┃       ($) ┃ RPM ┃ Scope ┃ Caps ┃ Activity ┃
┡━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━╇━━━━━━━╇━━━━━━╇━━━━━━━━━━┩
│ rw-20260… │  1.200000 │ 0.000000 │  1.200000 │ 120 │ exec  │ —    │ N/A      │
│ rw-20260… │  1.200000 │ 0.000000 │  1.200000 │ 120 │ exec  │ —    │ N/A      │
│ rw-20260… │  1.200000 │ 0.000000 │  1.200000 │ 120 │ exec  │ —    │ N/A      │
│ rw-20260… │  1.200000 │ 0.000000 │  1.200000 │ 120 │ exec  │ —    │ N/A      │
│ rw-20260… │  1.200000 │ 0.000000 │  1.200000 │ 120 │ exec  │ —    │ N/A      │
│ rw-20260… │  1.200000 │ 0.000000 │  1.200000 │ 120 │ exec  │ —    │ N/A      │
│ rw-20260… │  1.200000 │ 0.000000 │  1.200000 │ 120 │ exec  │ —    │ N/A      │
│ rw-20260… │  1.200000 │ 0.000000 │  1.200000 │ 120 │ exec  │ —    │ N/A      │
│ rw-20260… │  1.200000 │ 0.000000 │  1.200000 │ 120 │ exec  │ —    │ N/A      │
│ rw-20260… │  1.200000 │ 0.000000 │  1.200000 │ 120 │ exec  │ —    │ N/A      │
│ rw-20260… │  0.004000 │ 0.000000 │  0.004000 │ 120 │ exec  │ —    │ N/A      │
│ rw-20260… │  0.004000 │ 0.000000 │  0.004000 │ 120 │ exec  │ —    │ N/A      │
└───────────┴───────────┴──────────┴───────────┴─────┴───────┴──────┴──────────┘
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex agent inspect rw-20260220-112921-a00`
- return code: `0`
- stdout:
```text
Agent: rw-20260220-112921-a00
  Budget:    $1.200000  (1200000 µ)
  Spent:     $0.000000  (0 µ)
  Reserved:  $0.000000  (0 µ)
  Remaining: $1.200000
  RPM Limit: 120
  Last:      N/A

Capabilities:
  Streaming:         ✅
  Tools:             ✅
  Function Calling:  ✅
  Vision:            ❌
  Strict Mode:       OFF
  Passthrough:       ❌
  Allowed Models:    ["gpt-oss-20b"]
  Used (Prompt):     0
  Used (Output):     0

Auth:
  Scope:    execution
  Expires:  Never
  Hashed:   ✅

⚠ Token (sensitive): ***TOKEN_REDACTED***
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex agent rotate-token rw-20260220-112921-a00`
- return code: `0`
- stdout:
```text
Token rotated for agent 'rw-20260220-112921-a00'.
New Token: ***TOKEN_REDACTED***
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex run --agent rw-20260220-112921-a00 /home/lenovo/Documents/aex/aex_env/bin/python -c import os; print('OPENAI_BASE_URL', os.getenv('OPENAI_BASE_URL')); print('HAS_OPENAI_API_KEY', bool(os.getenv('OPENAI_API_KEY')))`
- return code: `2`
- stdout:
```text
<empty>
```
- stderr:
```text
Usage: python -m aex run [OPTIONS] COMMAND...
Try 'python -m aex run --help' for help.
╭─ Error ──────────────────────────────────────────────────────────────────────╮
│ No such option: -c                                                           │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex metrics`
- return code: `0`
- stdout:
```text
AEX Kernel Metrics
  Agents:             12
  Total Spent:        $0.000000
  Active Processes:   0
  Total Requests:     0
  Denied (Budget):    0
  Denied (Rate):      0
  Policy Violations:  0

                              Per-Agent Financials                              
┏━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━┳━━━━━┳━━━━━━━━━━━━━━┓
┃             ┃           ┃   Remaining ┃           ┃     ┃     ┃ Last         ┃
┃ Agent       ┃ Spent ($) ┃         ($) ┃ Burn Rate ┃ TTB ┃ RPM ┃ Activity     ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━╇━━━━━╇━━━━━━━━━━━━━━┩
│ rw-2026022… │  0.000000 │    1.200000 │      0µ/s │   ∞ │ 120 │ N/A          │
│ rw-2026022… │  0.000000 │    1.200000 │      0µ/s │   ∞ │ 120 │ N/A          │
│ rw-2026022… │  0.000000 │    1.200000 │      0µ/s │   ∞ │ 120 │ N/A          │
│ rw-2026022… │  0.000000 │    1.200000 │      0µ/s │   ∞ │ 120 │ N/A          │
│ rw-2026022… │  0.000000 │    1.200000 │      0µ/s │   ∞ │ 120 │ N/A          │
│ rw-2026022… │  0.000000 │    1.200000 │      0µ/s │   ∞ │ 120 │ N/A          │
│ rw-2026022… │  0.000000 │    1.200000 │      0µ/s │   ∞ │ 120 │ N/A          │
│ rw-2026022… │  0.000000 │    1.200000 │      0µ/s │   ∞ │ 120 │ N/A          │
│ rw-2026022… │  0.000000 │    1.200000 │      0µ/s │   ∞ │ 120 │ N/A          │
│ rw-2026022… │  0.000000 │    1.200000 │      0µ/s │   ∞ │ 120 │ N/A          │
│ rw-2026022… │  0.000000 │    0.004000 │      0µ/s │   ∞ │ 120 │ N/A          │
│ rw-2026022… │  0.000000 │    0.004000 │      0µ/s │   ∞ │ 120 │ N/A          │
└─────────────┴───────────┴─────────────┴───────────┴─────┴─────┴──────────────┘
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex metrics`
- return code: `0`
- stdout:
```text
AEX Kernel Metrics
  Agents:             12
  Total Spent:        $0.544000
  Active Processes:   0
  Total Requests:     31
  Denied (Budget):    10
  Denied (Rate):      0
  Policy Violations:  0

                              Per-Agent Financials                              
┏━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━┳━━━━━┳━━━━━━━━━━━━┓
┃            ┃           ┃   Remaining ┃           ┃        ┃     ┃ Last       ┃
┃ Agent      ┃ Spent ($) ┃         ($) ┃ Burn Rate ┃    TTB ┃ RPM ┃ Activity   ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━╇━━━━━╇━━━━━━━━━━━━┩
│ rw-202602… │  0.053150 │    1.146850 │   3543µ/s │ 5m 23s │ 120 │ 2026-02-20 │
│            │           │             │           │        │     │ 11:29:40   │
│ rw-202602… │  0.055250 │    1.144750 │   3683µ/s │ 5m 10s │ 120 │ 2026-02-20 │
│            │           │             │           │        │     │ 11:29:38   │
│ rw-202602… │  0.053450 │    1.146550 │   3817µ/s │  5m 0s │ 120 │ 2026-02-20 │
│            │           │             │           │        │     │ 11:29:38   │
│ rw-202602… │  0.051950 │    1.148050 │   3710µ/s │  5m 9s │ 120 │ 2026-02-20 │
│            │           │             │           │        │     │ 11:29:38   │
│ rw-202602… │  0.056350 │    1.143650 │   4334µ/s │ 4m 23s │ 120 │ 2026-02-20 │
│            │           │             │           │        │     │ 11:29:38   │
│ rw-202602… │  0.056250 │    1.143750 │   4326µ/s │ 4m 24s │ 120 │ 2026-02-20 │
│            │           │             │           │        │     │ 11:29:38   │
│ rw-202602… │  0.052950 │    1.147050 │   4412µ/s │ 4m 19s │ 120 │ 2026-02-20 │
│            │           │             │           │        │     │ 11:29:38   │
│ rw-202602… │  0.053550 │    1.146450 │   4868µ/s │ 3m 55s │ 120 │ 2026-02-20 │
│            │           │             │           │        │     │ 11:29:38   │
│ rw-202602… │  0.055050 │    1.144950 │   5004µ/s │ 3m 48s │ 120 │ 2026-02-20 │
│            │           │             │           │        │     │ 11:29:39   │
│ rw-202602… │  0.056050 │    1.143950 │   5605µ/s │ 3m 24s │ 120 │ 2026-02-20 │
│            │           │             │           │        │     │ 11:29:39   │
│ rw-202602… │  0.000000 │    0.004000 │      0µ/s │      ∞ │ 120 │ N/A        │
│ rw-202602… │  0.000000 │    0.004000 │      0µ/s │      ∞ │ 120 │ N/A        │
└────────────┴───────────┴─────────────┴───────────┴────────┴─────┴────────────┘
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex status`
- return code: `0`
- stdout:
```text
AEX Enforcement Status

  Agents:             12
  Total Budget:       $12.008000
  Total Spent:        $0.544000
  Total Reserved:     $0.000000
  Remaining:          $11.464000

  Requests Served:    31
  Budget Denials:     10
  Rate Limit Denials: 0
  Policy Violations:  0
  Processes Killed:   0
  Active Processes:   0
  Daemon:             Running (PID 213164)
```
- stderr:
```text
<empty>
```

### `/home/lenovo/Documents/aex/aex_env/bin/python -m aex audit`
- return code: `0`
- stdout:
```text
AEX Audit — Invariant Verification

  ✅ spent_within_budget: PASS
  ✅ no_negative_values: PASS
  ✅ no_orphaned_reservations: PASS
  ✅ event_log_integrity: PASS
  ✅ spent_matches_events: PASS

All 5 invariant checks passed.
```
- stderr:
```text
<empty>
```

## Per-Agent Outcomes

| Agent | Budget USD | Requests | Success | Errors | 402 | 429 | Avg Latency (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| rw-20260220-112921-a00 | 1.200 | 5 | 4 | 1 | 0 | 1 | 0.87 |
| rw-20260220-112921-a01 | 1.200 | 5 | 3 | 2 | 0 | 2 | 0.90 |
| rw-20260220-112921-a02 | 1.200 | 5 | 3 | 2 | 0 | 2 | 0.89 |
| rw-20260220-112921-a03 | 1.200 | 5 | 3 | 2 | 0 | 2 | 0.88 |
| rw-20260220-112921-a04 | 1.200 | 5 | 3 | 2 | 0 | 2 | 0.88 |
| rw-20260220-112921-a05 | 1.200 | 5 | 3 | 2 | 0 | 2 | 0.78 |
| rw-20260220-112921-a06 | 1.200 | 5 | 3 | 2 | 0 | 2 | 0.83 |
| rw-20260220-112921-a07 | 1.200 | 5 | 3 | 2 | 0 | 2 | 0.86 |
| rw-20260220-112921-a08 | 1.200 | 5 | 3 | 2 | 0 | 2 | 0.88 |
| rw-20260220-112921-a09 | 1.200 | 5 | 3 | 2 | 0 | 2 | 0.86 |
| rw-20260220-112921-a10 | 0.004 | 5 | 0 | 5 | 5 | 0 | 0.00 |
| rw-20260220-112921-a11 | 0.004 | 5 | 0 | 5 | 5 | 0 | 0.00 |

## DB Governance Checks (created agents only)
- DB present: `True`
- Overspend count: `0`
- Negative-value count: `0`
- `budget.deny` events: `10`
- `RATE_LIMIT` events: `0`

## Verdict
AEX demonstrated stable kernel behavior for this live run: requests were served through OpenClaw to Groq, budget-denial controls triggered as expected, and no overspend/negative accounting was observed for created agents.

## Notes
- Tokens and API keys are redacted in this report.
- This run uses real provider traffic (Groq) via OpenClaw through AEX.