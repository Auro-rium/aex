"""HTTP client helpers for production check runs."""

from __future__ import annotations

import time
from typing import Any

import httpx

from .models import CheckResult, RunContext


def _trim_text(text: str, limit: int = 220) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


class ProdCheckClient:
    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self._client = httpx.Client(
            base_url=ctx.base_url.rstrip("/"),
            timeout=ctx.timeout_seconds,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def _headers(
        self,
        *,
        auth: bool,
        idempotency_key: str | None = None,
        passthrough_provider_key: bool = False,
        auth_token_override: str | None = None,
    ) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if auth:
            token = auth_token_override if auth_token_override is not None else self.ctx.token
            headers["Authorization"] = f"Bearer {token}"
        if self.ctx.tenant_id:
            headers["X-AEX-Tenant-Id"] = self.ctx.tenant_id
        if self.ctx.project_id:
            headers["X-AEX-Project-Id"] = self.ctx.project_id
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if passthrough_provider_key and self.ctx.provider_api_key:
            headers["x-aex-provider-key"] = self.ctx.provider_api_key
        return headers

    def run_check(
        self,
        *,
        name: str,
        category: str,
        method: str,
        path: str,
        auth: bool,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        passthrough_provider_key: bool = False,
        auth_token_override: str | None = None,
        expect_status: set[int] | None = None,
        validator: callable | None = None,
    ) -> CheckResult:
        started = time.perf_counter()
        status_code: int | None = None
        try:
            response = self._client.request(
                method=method.upper(),
                url=path,
                headers=self._headers(
                    auth=auth,
                    idempotency_key=idempotency_key,
                    passthrough_provider_key=passthrough_provider_key,
                    auth_token_override=auth_token_override,
                ),
                json=json_body,
            )
            status_code = response.status_code
            latency_ms = int((time.perf_counter() - started) * 1000)

            expected = expect_status if expect_status is not None else {200}
            if status_code not in expected:
                return CheckResult(
                    name=name,
                    passed=False,
                    category=category,
                    method=method.upper(),
                    path=path,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    detail=f"Unexpected HTTP {status_code}; expected {sorted(expected)}; body={_trim_text(response.text)}",
                )

            if validator is None:
                return CheckResult(
                    name=name,
                    passed=True,
                    category=category,
                    method=method.upper(),
                    path=path,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    detail=f"HTTP {status_code}",
                )

            passed, detail = validator(response)
            return CheckResult(
                name=name,
                passed=bool(passed),
                category=category,
                method=method.upper(),
                path=path,
                status_code=status_code,
                latency_ms=latency_ms,
                detail=detail,
            )
        except Exception as exc:
            return CheckResult(
                name=name,
                passed=False,
                category=category,
                method=method.upper(),
                path=path,
                status_code=status_code,
                latency_ms=int((time.perf_counter() - started) * 1000),
                detail=f"Request error: {exc}",
            )

    def request_raw(
        self,
        *,
        method: str,
        path: str,
        auth: bool,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        passthrough_provider_key: bool = False,
        auth_token_override: str | None = None,
    ) -> tuple[httpx.Response | None, int | None, str | None]:
        started = time.perf_counter()
        try:
            response = self._client.request(
                method=method.upper(),
                url=path,
                headers=self._headers(
                    auth=auth,
                    idempotency_key=idempotency_key,
                    passthrough_provider_key=passthrough_provider_key,
                    auth_token_override=auth_token_override,
                ),
                json=json_body,
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            return response, latency_ms, None
        except Exception as exc:
            return None, int((time.perf_counter() - started) * 1000), str(exc)
