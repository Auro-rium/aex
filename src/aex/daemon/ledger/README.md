# ledger - v2.0

Accounting and audit core.

Lifecycle:
- reserve: create/reuse execution identity and reservation ticket
- dispatch: transition to in-flight
- commit: CAS `RESERVED -> COMMITTED` exactly once
- release: idempotent failure path `RESERVED -> RELEASED`

Audit:
- append hash-chained events (`prev_hash`, `event_hash`)
- replay helpers validate chain integrity and balance consistency
