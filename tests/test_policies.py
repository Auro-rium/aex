import tempfile
import unittest
from unittest.mock import patch

from aex.policies import create_policy, list_policies, load_policy


class PolicyTests(unittest.TestCase):
    def test_create_and_load_policy(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ", {"AEX_POLICY_DIR": temp_dir}, clear=False
        ):
            created = create_policy(
                "prod_safe",
                {
                    "budget_usd": 50,
                    "allow_tools": ["search", "github"],
                    "deny_tools": ["shell", "db_write"],
                    "max_steps": 100,
                },
            )
            loaded = load_policy("prod_safe")

            self.assertEqual(created.policy_id, "prod_safe")
            self.assertEqual(loaded.budget_usd, 50.0)
            self.assertEqual(loaded.allow_tools, ("search", "github"))
            self.assertEqual(loaded.deny_tools, ("shell", "db_write"))
            self.assertEqual(loaded.max_steps, 100)

            items = list_policies()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].policy_id, "prod_safe")

    def test_allow_deny_overlap_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ", {"AEX_POLICY_DIR": temp_dir}, clear=False
        ):
            with self.assertRaises(ValueError):
                create_policy(
                    "bad_policy",
                    {
                        "allow_tools": ["search"],
                        "deny_tools": ["search"],
                    },
                )


if __name__ == "__main__":
    unittest.main()
