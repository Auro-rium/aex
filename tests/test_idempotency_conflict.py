import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from aex.daemon.control.admission import admit_request
from aex.daemon.ledger.budget import CachedExecutionResult


class IdempotencyConflictTests(unittest.TestCase):
    def test_conflict_is_raised_when_cached_hash_differs(self):
        route_plan = SimpleNamespace(
            provider_name="groq",
            base_url="https://example.invalid",
            upstream_path="/v1/chat/completions",
            route_hash="rhash",
        )
        model = SimpleNamespace(capabilities=SimpleNamespace(tools=True))
        cached = CachedExecutionResult(
            state="COMMITTED",
            request_hash="cached-hash",
            status_code=200,
            response_body={},
            error_body=None,
        )

        with patch("aex.daemon.control.admission.ensure_agent_can_execute"), patch(
            "aex.daemon.control.admission.resolve_route", return_value=(route_plan, None)
        ), patch("aex.daemon.control.admission.config_loader.get_model", return_value=model), patch(
            "aex.daemon.control.admission.execution_id_for_request",
            return_value=("exec-1", "new-hash"),
        ), patch(
            "aex.daemon.control.admission.get_execution_cache", return_value=cached
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(
                    admit_request(
                        endpoint="/v1/chat/completions",
                        body={"model": "gpt-oss-20b", "messages": [{"role": "user", "content": "x"}]},
                        headers={"idempotency-key": "k1"},
                        agent_info={"name": "agent1", "tenant_id": "default", "project_id": "default"},
                    )
                )

        self.assertEqual(ctx.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
