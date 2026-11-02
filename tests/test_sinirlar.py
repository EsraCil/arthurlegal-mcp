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


if __name__ == "__main__":
    unittest.main()
