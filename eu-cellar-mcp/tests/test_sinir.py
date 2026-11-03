"""sparql: the answer read from CELLAR is capped; a small answer still parses.

Nothing here touches the network: urlopen is replaced by a fake response.

    python -m unittest discover -s tests -p test_sinir.py -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import cellar  # noqa: E402


class _Answer:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.asked = None

    def read(self, n: int = -1) -> bytes:
        self.asked = n
        return self.body if n is None or n < 0 else self.body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class SparqlSizeTest(unittest.TestCase):
    def test_oversized_answer_is_refused(self) -> None:
        answer = _Answer(b" " * 2048)
        with mock.patch.object(cellar, "SPARQL_MAX_BYTES", 1024), \
                mock.patch.object(cellar.urllib.request, "urlopen", return_value=answer):
            with self.assertRaises(cellar.CellarError):
                cellar._sparql("SELECT ?s WHERE { ?s ?p ?o }")
        self.assertEqual(answer.asked, 1025)

    def test_small_answer_parses(self) -> None:
        body = json.dumps({"results": {"bindings": [{"s": {"value": "x"}}]}}).encode("utf-8")
        with mock.patch.object(cellar.urllib.request, "urlopen", return_value=_Answer(body)):
            self.assertEqual(cellar._rows(cellar._sparql("SELECT ?s WHERE { ?s ?p ?o }")), [{"s": "x"}])


if __name__ == "__main__":
    unittest.main()
