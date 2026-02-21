# runtime - v2.0

Crash recovery sweep for non-terminal executions:
- release expired `RESERVED` tickets
- fail orphaned `RESERVING`/`DISPATCHED` flows

Goal:
- keep ledger/accounting invariants closed after process interruptions.
