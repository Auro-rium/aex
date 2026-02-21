# control - v2.0

Admission pipeline responsibilities:
1. enforce lifecycle gate (`READY` required)
2. derive deterministic `execution_id` + `request_hash`
3. idempotency cache and in-flight collision handling
4. route resolution (endpoint + model -> provider path)
5. rate limit check (RPM/TPM)
6. policy evaluation (kernel + plugins)
7. budget reservation (`reserve_budget_v2`)

Outputs:
- admission decision and routed request body
- cached terminal response for duplicate execution IDs
