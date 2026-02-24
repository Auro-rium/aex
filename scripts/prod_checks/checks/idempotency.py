"""Idempotency replay/conflict checks."""

from __future__ import annotations

import secrets

from ..client import ProdCheckClient
from ..models import CheckResult


def _chat_body(model: str, user_text: str) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": user_text}],
        "max_tokens": 10,
        "temperature": 0,
    }


def _result(
    *,
    name: str,
    passed: bool,
    path: str,
    status_code: int | None,
    latency_ms: int | None,
    detail: str,
) -> CheckResult:
    return CheckResult(
        name=name,
        passed=passed,
        category="idempotency",
        method="POST",
        path=path,
        status_code=status_code,
        latency_ms=latency_ms,
        detail=detail,
    )


def run(client: ProdCheckClient, *, chat_model: str, passthrough_provider_key: bool) -> list[CheckResult]:
    path = "/v1/chat/completions"
    idem_key = f"prod-check-{secrets.token_hex(12)}"

    first_body = _chat_body(chat_model, "Reply with exactly: idempotency-pass")
    second_body_same = _chat_body(chat_model, "Reply with exactly: idempotency-pass")
    second_body_diff = _chat_body(chat_model, "Reply with exactly: different-payload")

    results: list[CheckResult] = []

    first_response, first_latency, first_error = client.request_raw(
        method="POST",
        path=path,
        auth=True,
        json_body=first_body,
        idempotency_key=idem_key,
        passthrough_provider_key=passthrough_provider_key,
    )
    if first_error or first_response is None:
        results.append(
            _result(
                name="idempotency_first_call",
                passed=False,
                path=path,
                status_code=None,
                latency_ms=first_latency,
                detail=f"Request error: {first_error}",
            )
        )
        results.append(
            _result(
                name="idempotency_replay_same_payload",
                passed=False,
                path=path,
                status_code=None,
                latency_ms=None,
                detail="Skipped because first idempotency call failed",
            )
        )
        results.append(
            _result(
                name="idempotency_conflict_diff_payload",
                passed=False,
                path=path,
                status_code=None,
                latency_ms=None,
                detail="Skipped because first idempotency call failed",
            )
        )
        return results

    first_status = first_response.status_code
    if first_status != 200:
        results.append(
            _result(
                name="idempotency_first_call",
                passed=False,
                path=path,
                status_code=first_status,
                latency_ms=first_latency,
                detail=f"Expected HTTP 200; body={first_response.text[:220]}",
            )
        )
        results.append(
            _result(
                name="idempotency_replay_same_payload",
                passed=False,
                path=path,
                status_code=None,
                latency_ms=None,
                detail="Skipped because first idempotency call was non-200",
            )
        )
        results.append(
            _result(
                name="idempotency_conflict_diff_payload",
                passed=False,
                path=path,
                status_code=None,
                latency_ms=None,
                detail="Skipped because first idempotency call was non-200",
            )
        )
        return results

    first_data = first_response.json()
    first_id = str(first_data.get("id", ""))
    results.append(
        _result(
            name="idempotency_first_call",
            passed=True,
            path=path,
            status_code=first_status,
            latency_ms=first_latency,
            detail=f"execution_id={first_id}",
        )
    )

    replay_response, replay_latency, replay_error = client.request_raw(
        method="POST",
        path=path,
        auth=True,
        json_body=second_body_same,
        idempotency_key=idem_key,
        passthrough_provider_key=passthrough_provider_key,
    )
    if replay_error or replay_response is None:
        results.append(
            _result(
                name="idempotency_replay_same_payload",
                passed=False,
                path=path,
                status_code=None,
                latency_ms=replay_latency,
                detail=f"Request error: {replay_error}",
            )
        )
    else:
        replay_status = replay_response.status_code
        if replay_status != 200:
            results.append(
                _result(
                    name="idempotency_replay_same_payload",
                    passed=False,
                    path=path,
                    status_code=replay_status,
                    latency_ms=replay_latency,
                    detail=f"Expected HTTP 200 replay; body={replay_response.text[:220]}",
                )
            )
        else:
            replay_data = replay_response.json()
            replay_id = str(replay_data.get("id", ""))
            same_id = bool(first_id and replay_id and first_id == replay_id)
            same_usage = replay_data.get("usage") == first_data.get("usage")
            passed = same_id or same_usage
            results.append(
                _result(
                    name="idempotency_replay_same_payload",
                    passed=passed,
                    path=path,
                    status_code=replay_status,
                    latency_ms=replay_latency,
                    detail=(
                        f"replay_id={replay_id} first_id={first_id} "
                        f"same_id={same_id} same_usage={same_usage}"
                    ),
                )
            )

    conflict_response, conflict_latency, conflict_error = client.request_raw(
        method="POST",
        path=path,
        auth=True,
        json_body=second_body_diff,
        idempotency_key=idem_key,
        passthrough_provider_key=passthrough_provider_key,
    )
    if conflict_error or conflict_response is None:
        results.append(
            _result(
                name="idempotency_conflict_diff_payload",
                passed=False,
                path=path,
                status_code=None,
                latency_ms=conflict_latency,
                detail=f"Request error: {conflict_error}",
            )
        )
    else:
        conflict_status = conflict_response.status_code
        passed = conflict_status == 409
        results.append(
            _result(
                name="idempotency_conflict_diff_payload",
                passed=passed,
                path=path,
                status_code=conflict_status,
                latency_ms=conflict_latency,
                detail=f"Expected HTTP 409 conflict; body={conflict_response.text[:220]}",
            )
        )

    return results
