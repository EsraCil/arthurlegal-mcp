"""A JSON-RPC batch is capped at mcpcore._MAX_BATCH; a small batch still answers every call.

Runs the real mcpcore handler on a local port, like tests/test_static.py.

    python -m unittest tests.test_sinirlar
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mcpcore  # noqa: E402


class BatchLimitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        dispatcher = mcpcore.Dispatcher([], name="test", version="0")
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), mcpcore._make_handler(dispatcher))
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _post(self, payload):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/mcp", body=json.dumps(payload).encode("utf-8"),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read() or b"null")
        conn.close()
        return resp.status, data

    def test_small_batch_is_answered(self) -> None:
        calls = [{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(3)]
        status, data = self._post(calls)
        self.assertEqual(status, 200)
        self.assertEqual(sorted(r["id"] for r in data), [0, 1, 2])

    def test_oversized_batch_is_refused(self) -> None:
        calls = [{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(mcpcore._MAX_BATCH + 1)]
        status, data = self._post(calls)
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], -32600)


class LoopbackOriginTest(unittest.TestCase):
    """Bound to loopback, requests naming another site in Host or Origin are refused."""

    @classmethod
    def setUpClass(cls) -> None:
        dispatcher = mcpcore.Dispatcher([], name="test", version="0")
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                        mcpcore._make_handler(dispatcher, loopback_only=True))
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _post(self, headers):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/mcp", body=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
                     headers=dict({"Content-Type": "application/json"}, **headers))
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return resp.status

    def test_local_client_is_answered(self) -> None:
        self.assertEqual(self._post({}), 200)
        self.assertEqual(self._post({"Host": "localhost:%d" % self.port}), 200)
        self.assertEqual(self._post({"Origin": "http://127.0.0.1:%d" % self.port}), 200)
        self.assertEqual(self._post({"Host": "[::1]:%d" % self.port}), 200)

    def test_foreign_host_or_origin_is_refused(self) -> None:
        self.assertEqual(self._post({"Host": "saldirgan.example:%d" % self.port}), 403)
        self.assertEqual(self._post({"Origin": "https://saldirgan.example"}), 403)
        self.assertEqual(self._post({"Origin": "null"}), 403)
        self.assertEqual(self._post({"Host": "localhost.saldirgan.example"}), 403)

    def test_bind_address_decides(self) -> None:
        for host in ("127.0.0.1", "localhost", "::1", "[::1]"):
            self.assertTrue(mcpcore._is_loopback_bind(host), host)
        for host in ("0.0.0.0", "::", "10.0.0.5"):
            self.assertFalse(mcpcore._is_loopback_bind(host), host)

    def test_vendored_copies_match(self) -> None:
        with open(os.path.join(ROOT, "mcpcore.py"), "rb") as fh:
            root = fh.read()
        for name in sorted(os.listdir(ROOT)):
            copy = os.path.join(ROOT, name, "mcpcore.py")
            if os.path.isfile(copy):
                with open(copy, "rb") as fh:
                    self.assertEqual(fh.read(), root, "%s/mcpcore.py differs from ./mcpcore.py" % name)


class ScholarCompareLimitTest(unittest.TestCase):
    """compare_jurisdictions: repeated codes collapse, the list and the page size are capped."""

    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, os.path.join(ROOT, "lex-scholar-api"))
        from lexscholar import client
        cls.client_mod = client

    def _client(self):
        c = self.client_mod.LexScholarClient()
        c.asked = []

        def search(query, jurisdiction=None, limit=20, **kw):
            c.asked.append((jurisdiction, limit))
            return {"sources_queried": [], "returned": 0, "results": []}
        c.search = search
        return c

    def test_codes_are_deduplicated_and_page_size_capped(self) -> None:
        c = self._client()
        out = c.compare_jurisdictions("force majeure", ["fr", "FR", " fr ", "br"], limit_per=500)
        self.assertEqual(out["jurisdictions_requested"], ["FR", "BR"])
        self.assertEqual(sorted(c.asked), [("BR", 20), ("FR", 20)])

    def test_too_many_jurisdictions_are_refused_before_any_search(self) -> None:
        c = self._client()
        codes = ["A%s" % chr(65 + i) for i in range(self.client_mod._MAX_JURISDICTIONS + 1)]
        with self.assertRaises(Exception):
            c.compare_jurisdictions("x", codes)
        self.assertEqual(c.asked, [])


class QueryEmbeddingSizeTest(unittest.TestCase):
    """A query sent to the embeddings provider is clipped; documents are left as given."""

    def _copies(self):
        import importlib.util
        for i, rel in enumerate(("arthur-tr-hukuk-mcp/retrieval.py", "eqanun-api/eqanun/retrieval.py")):
            spec = importlib.util.spec_from_file_location("retrieval_kopya_%d" % i, os.path.join(ROOT, rel))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            yield rel, mod

    def test_long_query_is_clipped_before_sending(self) -> None:
        from unittest import mock

        for rel, mod in self._copies():
            sent = []

            class _Answer:
                def __init__(self, n):
                    self.n = n

                def read(self):
                    return json.dumps({"data": [{"embedding": [0.0]}] * self.n}).encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

            def urlopen(req, timeout=None):
                payload = json.loads(req.data.decode("utf-8"))
                sent.append(payload["input"])
                return _Answer(len(payload["input"]))

            with mock.patch.object(mod.urllib.request, "urlopen", urlopen):
                mod._embed(["ç" * 50000], input_type="query")
                mod._embed(["d" * 5000], input_type="document")
            self.assertEqual(len(sent[0][0]), mod.QUERY_EMBED_CHARS, rel)
            self.assertEqual(len(sent[1][0]), 5000, rel)


if __name__ == "__main__":
    unittest.main()
