# auth - v2.0

Authentication behavior:
- bearer token required
- hashed token lookup primary (`token_hash`)
- raw token lookup fallback for backward compatibility
- token TTL validation
- token scope returned to execution path (`execution` vs `read-only`)
