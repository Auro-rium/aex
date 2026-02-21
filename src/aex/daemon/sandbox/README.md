# sandbox - v2.0

Tool execution isolation.

Components:
- capability token mint/verify (signed, TTL-bound)
- plugin registry + manifest verification
- isolated runner (bwrap when available, bounded fallback)

Controls:
- allowed fs paths
- network policy
- output size cap
- process resource limits
