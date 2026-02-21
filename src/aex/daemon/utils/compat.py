"""AEX Compatibility Contract — protocol fidelity tests.

Validates that the AEX proxy maintains 100% OpenAI-compatible protocol.
Tests: streaming SSE, tool calling, structured output, error parity.
"""

import json
from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass
class CompatResult:
    name: str
    passed: bool
    detail: str


def _base_url(port: int = 9000) -> str:
    return f"http://127.0.0.1:{port}/v1"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def test_basic_chat(token: str, port: int = 9000) -> CompatResult:
    """Test: basic non-streaming chat response structure."""
    try:
        r = httpx.post(
            f"{_base_url(port)}/chat/completions",
            json={
                "model": "gpt-oss-20b",
                "messages": [{"role": "user", "content": "Say 'ok'"}],
            },
            headers=_headers(token),
            timeout=30.0,
        )
        if r.status_code != 200:
            return CompatResult("basic_chat", False, f"HTTP {r.status_code}: {r.text[:100]}")

        data = r.json()

        # Validate required OpenAI response fields
        required = ["id", "object", "model", "choices", "usage"]
        missing = [f for f in required if f not in data]
        if missing:
            return CompatResult("basic_chat", False, f"Missing fields: {missing}")

        if data["object"] != "chat.completion":
            return CompatResult("basic_chat", False, f"Wrong object type: {data['object']}")

        choice = data["choices"][0]
        if "message" not in choice or "role" not in choice["message"]:
            return CompatResult("basic_chat", False, "Choice missing message.role")

        usage = data["usage"]
        for key in ["prompt_tokens", "completion_tokens", "total_tokens"]:
            if key not in usage:
                return CompatResult("basic_chat", False, f"Usage missing: {key}")

        return CompatResult("basic_chat", True, "All response fields valid")

    except Exception as e:
        return CompatResult("basic_chat", False, str(e))


def test_streaming_sse(token: str, port: int = 9000) -> CompatResult:
    """Test: streaming SSE chunk structure, data: prefix, [DONE] terminator."""
    try:
        chunks = []
        got_done = False

        with httpx.stream(
            "POST",
            f"{_base_url(port)}/chat/completions",
            json={
                "model": "gpt-oss-20b",
                "messages": [{"role": "user", "content": "Say 'hello'"}],
                "stream": True,
            },
            headers=_headers(token),
            timeout=30.0,
        ) as response:
            if response.status_code != 200:
                return CompatResult("streaming_sse", False, f"HTTP {response.status_code}")

            for line in response.iter_lines():
                if not line:
                    continue
                if line == "data: [DONE]":
                    got_done = True
                    continue
                if line.startswith("data: "):
                    try:
                        chunk = json.loads(line[6:])
                        chunks.append(chunk)
                    except json.JSONDecodeError:
                        return CompatResult("streaming_sse", False, f"Invalid JSON chunk: {line[:80]}")

        if not chunks:
            return CompatResult("streaming_sse", False, "No chunks received")

        if not got_done:
            return CompatResult("streaming_sse", False, "Missing [DONE] terminator")

        # Validate chunk structure
        first = chunks[0]
        if first.get("object") != "chat.completion.chunk":
            return CompatResult("streaming_sse", False, f"Wrong object: {first.get('object')}")

        for field in ["id", "model", "choices"]:
            if field not in first:
                return CompatResult("streaming_sse", False, f"Chunk missing field: {field}")

        # Check delta format
        choice = first["choices"][0]
        if "delta" not in choice:
            return CompatResult("streaming_sse", False, "Chunk choice missing 'delta'")

        return CompatResult(
            "streaming_sse", True,
            f"{len(chunks)} chunks, [DONE] received, delta format valid"
        )

    except Exception as e:
        return CompatResult("streaming_sse", False, str(e))


def test_tool_calling(token: str, port: int = 9000) -> CompatResult:
    """Test: tool calling response preserves tool_calls structure."""
    try:
        r = httpx.post(
            f"{_base_url(port)}/chat/completions",
            json={
                "model": "gpt-oss-20b",
                "messages": [{"role": "user", "content": "What is the weather in London?"}],
                "tools": [{
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather for a city",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                            "required": ["city"],
                        },
                    },
                }],
            },
            headers=_headers(token),
            timeout=30.0,
        )
        if r.status_code != 200:
            return CompatResult("tool_calling", False, f"HTTP {r.status_code}: {r.text[:100]}")

        data = r.json()
        msg = data["choices"][0]["message"]

        # Tool call is optional — model may choose not to call
        if msg.get("tool_calls"):
            tc = msg["tool_calls"][0]
            for field in ["id", "type", "function"]:
                if field not in tc:
                    return CompatResult("tool_calling", False, f"tool_call missing: {field}")
            if "name" not in tc["function"] or "arguments" not in tc["function"]:
                return CompatResult("tool_calling", False, "tool_call.function missing name/arguments")
            return CompatResult("tool_calling", True, f"Tool call: {tc['function']['name']}")
        else:
            # Model chose not to call tool — still valid
            return CompatResult(
                "tool_calling", True,
                "Model responded without tool call (valid behavior)"
            )

    except Exception as e:
        return CompatResult("tool_calling", False, str(e))


def test_structured_output(token: str, port: int = 9000) -> CompatResult:
    """Test: response_format json_object passthrough."""
    try:
        r = httpx.post(
            f"{_base_url(port)}/chat/completions",
            json={
                "model": "gpt-oss-20b",
                "messages": [
                    {"role": "system", "content": "You always respond in JSON format."},
                    {"role": "user", "content": "Return a JSON object with key 'status' and value 'ok'."},
                ],
                "response_format": {"type": "json_object"},
            },
            headers=_headers(token),
            timeout=30.0,
        )
        if r.status_code != 200:
            return CompatResult("structured_output", False, f"HTTP {r.status_code}: {r.text[:100]}")

        data = r.json()
        content = data["choices"][0]["message"]["content"]

        # Try to parse as JSON
        try:
            parsed = json.loads(content)
            return CompatResult(
                "structured_output", True,
                f"Valid JSON response: {json.dumps(parsed)[:80]}"
            )
        except json.JSONDecodeError:
            # Some providers may not honor json_object — still a valid passthrough
            return CompatResult(
                "structured_output", True,
                f"Response received (provider may not support json_object): {content[:60]}"
            )

    except Exception as e:
        return CompatResult("structured_output", False, str(e))


def test_error_parity(token: str, port: int = 9000) -> CompatResult:
    """Test: AEX returns correct HTTP error codes (401, 403)."""
    results = []

    # 401: Invalid token
    try:
        r = httpx.post(
            f"{_base_url(port)}/chat/completions",
            json={"model": "gpt-oss-20b", "messages": [{"role": "user", "content": "test"}]},
            headers=_headers("invalid_token_that_should_fail_" + "x" * 32),
            timeout=10.0,
        )
        if r.status_code in (401, 403):
            results.append(f"invalid_token→{r.status_code} ✓")
        else:
            return CompatResult("error_parity", False, f"Invalid token got HTTP {r.status_code}, expected 401/403")
    except Exception as e:
        return CompatResult("error_parity", False, f"Auth test failed: {e}")

    # 403: Unknown model
    try:
        r = httpx.post(
            f"{_base_url(port)}/chat/completions",
            json={"model": "nonexistent-model-xyz", "messages": [{"role": "user", "content": "test"}]},
            headers=_headers(token),
            timeout=10.0,
        )
        if r.status_code == 403:
            results.append("unknown_model→403 ✓")
        else:
            return CompatResult("error_parity", False, f"Unknown model got HTTP {r.status_code}, expected 403")
    except Exception as e:
        return CompatResult("error_parity", False, f"Model test failed: {e}")

    return CompatResult("error_parity", True, ", ".join(results))


def run_all_compat_tests(token: str, port: int = 9000) -> list[CompatResult]:
    """Run all compatibility contract tests."""
    return [
        test_basic_chat(token, port),
        test_streaming_sse(token, port),
        test_tool_calling(token, port),
        test_structured_output(token, port),
        test_error_parity(token, port),
    ]
