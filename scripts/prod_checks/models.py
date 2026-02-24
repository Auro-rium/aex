"""Shared models for production checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, UTC


@dataclass
class CheckResult:
    name: str
    passed: bool
    category: str
    method: str
    path: str
    status_code: int | None
    latency_ms: int | None
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunContext:
    base_url: str
    token: str
    chat_model: str
    embedding_model: str
    tenant_id: str | None = None
    project_id: str | None = None
    provider_api_key: str | None = None
    timeout_seconds: float = 45.0


@dataclass
class RunSummary:
    ts_utc: str
    base_url: str
    total: int
    passed: int
    failed: int
    checks: list[dict]

    @classmethod
    def from_results(cls, base_url: str, results: list[CheckResult]) -> "RunSummary":
        passed = sum(1 for r in results if r.passed)
        return cls(
            ts_utc=datetime.now(UTC).isoformat(),
            base_url=base_url,
            total=len(results),
            passed=passed,
            failed=len(results) - passed,
            checks=[r.to_dict() for r in results],
        )

    def to_dict(self) -> dict:
        return asdict(self)
