"""Real proxy endpoint checks against production."""

from __future__ import annotations

from ..client import ProdCheckClient
from ..models import CheckResult


def _chat_validator(response) -> tuple[bool, str]:
    payload = response.json()
    for key in ("id", "object", "model", "choices", "usage"):
        if key not in payload:
            return False, f"Missing chat field: {key}"
    if payload.get("object") != "chat.completion":
        return False, f"Unexpected object={payload.get('object')}"
    choices = payload.get("choices") or []
    if not choices:
        return False, "Empty choices"
    msg = choices[0].get("message", {})
    content = msg.get("content")
    return True, f"chat_ok content_len={len(str(content or ''))}"


def _responses_validator(response) -> tuple[bool, str]:
    payload = response.json()
    if "id" not in payload:
        return False, "Missing responses.id"
    if "output" not in payload:
        return False, "Missing responses.output"
    output = payload.get("output")
    size = len(output) if isinstance(output, list) else 1
    return True, f"responses_ok output_items={size}"


def _embeddings_validator(response) -> tuple[bool, str]:
    payload = response.json()
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return False, "Missing embeddings data"
    first = data[0]
    vec = first.get("embedding")
    if not isinstance(vec, list) or not vec:
        return False, "Missing embedding vector"
    return True, f"embeddings_ok dims={len(vec)}"


def _chat_body(model: str) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
        "max_tokens": 8,
        "temperature": 0,
    }


def _responses_body(model: str) -> dict:
    return {
        "model": model,
        "input": [{"role": "user", "content": "Reply with exactly: ok"}],
        "max_output_tokens": 8,
        "temperature": 0,
    }


def _embeddings_body(model: str) -> dict:
    return {
        "model": model,
        "input": "production embeddings probe",
    }


def run(
    client: ProdCheckClient,
    *,
    chat_model: str,
    embedding_model: str,
    passthrough_provider_key: bool,
    include_embeddings: bool = True,
) -> list[CheckResult]:
    checks: list[CheckResult] = [
        client.run_check(
            name="proxy_chat_completions_real",
            category="proxy",
            method="POST",
            path="/v1/chat/completions",
            auth=True,
            json_body=_chat_body(chat_model),
            validator=_chat_validator,
            passthrough_provider_key=passthrough_provider_key,
        ),
        client.run_check(
            name="proxy_responses_real",
            category="proxy",
            method="POST",
            path="/v1/responses",
            auth=True,
            json_body=_responses_body(chat_model),
            validator=_responses_validator,
            passthrough_provider_key=passthrough_provider_key,
        ),
    ]
    if include_embeddings:
        checks.append(
            client.run_check(
                name="proxy_embeddings_real",
                category="proxy",
                method="POST",
                path="/v1/embeddings",
                auth=True,
                json_body=_embeddings_body(embedding_model),
                validator=_embeddings_validator,
                passthrough_provider_key=passthrough_provider_key,
            )
        )

    # Add raw payload snippets to failing details for faster break triage.
    for result in checks:
        if not result.passed and result.status_code is not None:
            result.detail = f"{result.detail} model={chat_model if 'embeddings' not in result.name else embedding_model}"
    return checks
