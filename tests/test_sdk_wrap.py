import os
import tempfile
import unittest
from unittest.mock import patch

from aex import AEX, enable, login, wrap


class _DummyAgent:
    def run(self, *, max_steps=None):
        return {
            "max_steps": max_steps,
            "policy_id": os.getenv("AEX_POLICY_ID"),
            "token": os.getenv("AEX_AGENT_TOKEN"),
        }


class SDKWrapTests(unittest.TestCase):
    def test_wrap_injects_policy_context_and_max_steps(self):
        with patch.dict("os.environ", {"AEX_API_KEY": "test-token"}, clear=False):
            wrapped = AEX.wrap(
                _DummyAgent(),
                policy={
                    "policy_id": "prod_safe",
                    "budget_usd": 50,
                    "allow_tools": ["search", "github"],
                    "deny_tools": ["shell"],
                    "max_steps": 123,
                },
            )
            result = wrapped.run()

        self.assertEqual(result["max_steps"], 123)
        self.assertEqual(result["policy_id"], "prod_safe")
        self.assertEqual(result["token"], "test-token")

    def test_wrap_requires_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            wrapped = AEX.wrap(_DummyAgent(), policy={"policy_id": "prod_safe"})
            with self.assertRaises(RuntimeError):
                wrapped.run()

    def test_top_level_wrap_function(self):
        with patch.dict("os.environ", {"AEX_API_KEY": "test-token"}, clear=False):
            wrapped = wrap(_DummyAgent(), policy={"policy_id": "prod_safe"})
            result = wrapped.run()
        self.assertEqual(result["token"], "test-token")

    def test_enable_sets_proxy_env(self):
        with patch.dict("os.environ", {}, clear=True):
            exported = enable(
                base_url="https://aex-cloud.app",
                api_key="aex-inline-token",
                tenant="acme",
                project="prod",
                monkey_patch=False,
            )
        self.assertEqual(exported["AEX_ENABLE"], "1")
        self.assertEqual(exported["AEX_MODE"], "proxy")
        self.assertEqual(exported["OPENAI_BASE_URL"], "https://aex-cloud.app/v1")
        self.assertEqual(exported["AEX_API_KEY"], "aex-inline-token")
        self.assertEqual(exported["AEX_TENANT"], "acme")
        self.assertEqual(exported["AEX_PROJECT"], "prod")

    def test_wrap_allows_inline_api_key_one_liner(self):
        with patch.dict("os.environ", {}, clear=True):
            wrapped = wrap(
                _DummyAgent(),
                api_key="aex-inline-token",
                base_url="https://aex-production.up.railway.app",
                tenant="acme",
                project="prod",
                monkey_patch=False,
            )
            result = wrapped.run()
        self.assertEqual(result["token"], "aex-inline-token")

    def test_login_then_wrap_without_repeating_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = os.path.join(tmpdir, "sdk_profile.json")
            with patch.dict("os.environ", {"AEX_PROFILE_PATH": profile_path}, clear=True):
                exported = login(
                    api_key="persisted-aex-token",
                    base_url="https://aex-production.up.railway.app",
                    tenant="acme",
                    project="prod",
                )
                self.assertEqual(exported["AEX_API_KEY"], "persisted-aex-token")
                wrapped = wrap(_DummyAgent(), monkey_patch=False)
                result = wrapped.run()
        self.assertEqual(result["token"], "persisted-aex-token")


if __name__ == "__main__":
    unittest.main()
