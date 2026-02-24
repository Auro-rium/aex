import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aex.daemon.app.proxy import _build_embeddings_upstream


class EmbeddingsHardeningTests(unittest.TestCase):
    def test_dimensions_are_stripped_for_groq_by_default(self):
        model = SimpleNamespace(provider_model="text-embedding-3-small", provider="groq")
        body = {"input": "hello", "dimensions": 1536}
        upstream = _build_embeddings_upstream(body, model)
        self.assertNotIn("dimensions", upstream)

    def test_dimensions_are_forwarded_for_supported_provider(self):
        model = SimpleNamespace(provider_model="text-embedding-3-small", provider="openai")
        body = {"input": "hello", "dimensions": 1536}
        with patch.dict(os.environ, {"AEX_EMBEDDINGS_DIMENSIONS_UNSUPPORTED_PROVIDERS": "groq"}, clear=False):
            upstream = _build_embeddings_upstream(body, model)
        self.assertEqual(upstream.get("dimensions"), 1536)


if __name__ == "__main__":
    unittest.main()
