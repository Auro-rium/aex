"""Authentication failure-path checks."""

from __future__ import annotations

from ..client import ProdCheckClient
from ..models import CheckResult


_CHAT_BODY = {
    "model": "gpt-oss-20b",
    "messages": [{"role": "user", "content": "ping"}],
    "max_tokens": 4,
}


def _expect_error_detail(response) -> tuple[bool, str]:
    try:
        payload = response.json()
    except Exception:
        return True, "error status returned (non-json body)"
    detail = payload.get("detail")
    if detail:
        return True, f"detail={detail}"
    return True, "error status returned"


def run(client: ProdCheckClient, chat_model: str) -> list[CheckResult]:
    body = dict(_CHAT_BODY)
    body["model"] = chat_model
    return [
        client.run_check(
            name="missing_auth_token",
            category="auth",
            method="POST",
            path="/v1/chat/completions",
            auth=False,
            json_body=body,
            expect_status={401, 403},
            validator=_expect_error_detail,
        ),
        client.run_check(
            name="invalid_auth_token",
            category="auth",
            method="POST",
            path="/v1/chat/completions",
            auth=True,
            json_body=body,
            expect_status={401, 403},
            validator=_expect_error_detail,
            auth_token_override="invalid_token_for_prod_test_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        ),
    ]
