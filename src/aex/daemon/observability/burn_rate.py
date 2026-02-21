"""Burn-rate modeling from committed ledger events."""

from __future__ import annotations

from datetime import datetime, timedelta, UTC


def _parse(ts: str) -> datetime | None:
    try:
        value = datetime.fromisoformat(ts)
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value
    except Exception:
        return None


def estimate_burn_windows(events: list[dict], now: datetime | None = None) -> dict[str, int]:
    """Return micro-units/sec burn estimate across standard windows."""
    now = now or datetime.now(UTC)
    windows = {
        "1m": timedelta(minutes=1),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
    }
    out: dict[str, int] = {}

    for key, delta in windows.items():
        cutoff = now - delta
        total = 0
        for ev in events:
            ts = _parse(ev.get("timestamp", ""))
            if not ts or ts < cutoff:
                continue
            total += int(ev.get("cost_micro", 0) or 0)
        seconds = max(1, int(delta.total_seconds()))
        out[key] = total // seconds

    return out
