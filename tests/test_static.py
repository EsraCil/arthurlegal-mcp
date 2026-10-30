"""GET /favicon.ico and /logo.png answer with the ArthurLegal logo; nothing else moves.

The connector directory shows the favicon of the server's URL. These tests run the
real mcpcore handler on a local port, with what server.py registers at import.

    python -m unittest tests.test_static
"""

from __future__ import annotations

import http.client
import json
import os
import struct
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mcpcore  # noqa: E402
import server  # noqa: E402,F401  (registers the files)


def _read(name: str) -> bytes:
    with open(os.path.join(ROOT, "varlik", name), "rb") as fh:
        return fh.read()


class StaticFilesTest(unittest.TestCase):
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

    def _request(self, method: str, path: str, body: bytes = b""):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"} if body else {}
        conn.request(method, path, body=body or None, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def test_favicon_is_a_multi_size_icon(self) -> None:
        data = _read("favicon.ico")
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind), (0, 1))
        sizes = sorted((data[6 + 16 * i] or 256) for i in range(count))
        self.assertEqual(sizes, [16, 32, 48, 64, 128, 256])

    def test_logo_is_a_square_png(self) -> None:
        data = _read("logo.png")
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", data[16:24])
        self.assertEqual(width, height)

    def test_get_serves_the_files(self) -> None:
        for path, name, ctype in (("/favicon.ico", "favicon.ico", "image/x-icon"),
                                  ("/favicon.ico?v=2", "favicon.ico", "image/x-icon"),
                                  ("/logo.png", "logo.png", "image/png")):
            with self.subTest(path=path):
                resp, data = self._request("GET", path)
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.getheader("Content-Type"), ctype)
                self.assertEqual(data, _read(name))

    def test_head_answers_without_a_body(self) -> None:
        resp, data = self._request("HEAD", "/favicon.ico")
        self.assertEqual(resp.status, 200)
        self.assertEqual(int(resp.getheader("Content-Length")), len(_read("favicon.ico")))
        self.assertEqual(data, b"")

    def test_other_paths_are_unchanged(self) -> None:
        self.assertEqual(self._request("GET", "/health")[0].status, 200)
        resp, data = self._request("GET", "/")
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(data)["mcp_endpoint"], "/mcp")
        self.assertEqual(self._request("GET", "/mcp")[0].status, 405)
        self.assertEqual(self._request("GET", "/favicon.png")[0].status, 405)
        self.assertEqual(self._request("HEAD", "/")[0].status, 501)
        self.assertEqual(self._request("POST", "/favicon.ico", b"{}")[0].status, 404)

    def test_keep_alive_survives_a_favicon(self) -> None:
        # Without Content-Length the next request on the connection would hang.
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/favicon.ico")
        conn.getresponse().read()
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"}}}).encode()
        conn.request("POST", "/mcp", body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(resp.read())["id"], 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
