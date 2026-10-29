"""The two endpoints: /mcp (everything) and /directory/mcp (the directory listing).

    python -m unittest tests.test_profiles      # from the repository root

Run it in a scratch copy of the repository, not in the deploy clone: loading the
backends may create ``<backend>/data/`` folders, and start.sh treats an existing
``data/index.db`` as already seeded and would not copy the baked index over it.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["DE_ELI_URL"] = "off"   # de-eli is a separate process; not needed here

import mcpcore  # noqa: E402
import server  # noqa: E402
import tool_titles  # noqa: E402

server.build()
DIRECTORY_TOOLS, DIRECTORY_TEXT, DIRECTORY_VIEWS = server.build_directory()
MAIN_TEXT = server.INSTRUCTIONS_HEADER + "\n\n" + "\n\n".join(server._instructions)


class Annotations(unittest.TestCase):
    def test_every_tool_has_title_and_hint(self):
        for tool in server._tools + DIRECTORY_TOOLS:
            public = tool.public()
            self.assertTrue(public.get("title"), tool.name)
            hints = public.get("annotations", {})
            self.assertEqual(hints.get("title"), public["title"], tool.name)
            self.assertTrue(hints.get("readOnlyHint") is True
                            or "destructiveHint" in hints, tool.name)
            self.assertLessEqual(len(tool.name), 64, tool.name)

    def test_titles_come_from_the_table(self):
        missing = [t.name for t in server._tools if t.name not in tool_titles.TITLES]
        self.assertEqual(missing, [], "add these to tool_titles.TITLES")

    def test_only_local_land_registry_writers_are_not_read_only(self):
        writers = [t.name for t in server._tools if not t.annotations.get("readOnlyHint")]
        self.assertTrue(set(writers) <= tool_titles.NOT_READ_ONLY, writers)
        self.assertTrue(all(t.annotations.get("readOnlyHint") is True for t in DIRECTORY_TOOLS))


class DirectoryProfile(unittest.TestCase):
    def test_land_registry_tools_stay_on_main_only(self):
        names = {t.name for t in DIRECTORY_TOOLS}
        self.assertFalse([n for n in names if n.startswith("tkgm_")])
        self.assertIn("status", names)
        self.assertIn("tr_mevzuat_madde_getir", names)
        main = {t.name for t in server._tools}
        self.assertEqual(names, {n for n in main if not n.startswith("tkgm_")})

    def test_directory_status_leaves_land_registry_out(self):
        status = next(t for t in DIRECTORY_TOOLS if t.name == "status")
        text = json.dumps(status.handler({}), ensure_ascii=False)
        self.assertNotIn("tkgm", text)
        self.assertEqual(status.handler({})["tools_exposed"], len(DIRECTORY_TOOLS))

    def test_directory_instructions_describe_rather_than_direct(self):
        self.assertTrue(DIRECTORY_TEXT.startswith("ArthurLegal MCP: read-only"))
        self.assertNotIn("MADDE ATFI KURALI", DIRECTORY_TEXT)
        self.assertNotIn("tkgm", DIRECTORY_TEXT.lower())

    def test_main_endpoint_keeps_its_rules(self):
        self.assertTrue(MAIN_TEXT.startswith("MADDE ATFI KURALI"))
        self.assertIn("MADDE ATFI KURALI", "\n".join(server._instructions))

    def test_directory_desk_shows_land_registry_as_desktop_only(self):
        if server.al_arayuz is None:
            self.skipTest("arayuz/ missing")
        main = next(t for t in server._tools if t.name == "al_masasi")
        directory = next(t for t in DIRECTORY_TOOLS if t.name == "al_masasi")
        self.assertEqual(main.handler({})["moduller"]["tapu"]["durum"], "claude")
        self.assertEqual(directory.handler({})["moduller"]["tapu"]["durum"], "masaustu")
        self.assertEqual(directory.meta, main.meta)
        self.assertFalse([n for n in directory.handler({})["yetenekler"] if n.startswith("tkgm_")])

    def test_directory_serves_the_same_views(self):
        if server.al_arayuz is None:
            self.skipTest("arayuz/ missing")
        self.assertEqual([r.uri for r in DIRECTORY_VIEWS], [r.uri for r in server._resources])
        uris = {r.uri for r in DIRECTORY_VIEWS}
        for tool in DIRECTORY_TOOLS:
            if tool.meta:
                self.assertIn(tool.meta["ui"]["resourceUri"], uris, tool.name)


class HttpRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main = mcpcore.Dispatcher(server._tools, "arthurlegal-mcp", server.__version__, MAIN_TEXT,
                                  resources=server._resources)
        directory = mcpcore.Dispatcher(DIRECTORY_TOOLS, "arthurlegal-mcp", server.__version__,
                                       DIRECTORY_TEXT, resources=DIRECTORY_VIEWS)
        handler = mcpcore._make_handler(main, {server.DIRECTORY_PATH: directory})
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def rpc(self, path, method, params=None):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                           "params": params or {}}).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def post(self, path, method, params=None):
        return self.rpc(path, method, params)["result"]

    def test_each_path_serves_its_own_tools_and_instructions(self):
        main = self.post("/mcp", "tools/list")["tools"]
        directory = self.post(server.DIRECTORY_PATH, "tools/list")["tools"]
        self.assertEqual(len(main), len(server._tools))
        self.assertEqual(len(directory), len(DIRECTORY_TOOLS))
        self.assertTrue(all("annotations" in t for t in directory))
        init = self.post(server.DIRECTORY_PATH + "/", "initialize",
                         {"protocolVersion": "2025-06-18"})
        self.assertTrue(init["instructions"].startswith("ArthurLegal MCP: read-only"))

    def test_directory_reads_the_desk_view(self):
        if not DIRECTORY_VIEWS:
            self.skipTest("arayuz/ missing")
        uri = "ui://arthurlegal/masa.html"
        out = self.post(server.DIRECTORY_PATH, "resources/read", {"uri": uri})
        self.assertEqual(out["contents"][0]["uri"], uri)
        self.assertIn("<html", out["contents"][0]["text"].lower())

    def test_directory_refuses_land_registry_calls(self):
        out = self.rpc(server.DIRECTORY_PATH, "tools/call",
                       {"name": "tkgm_baslangic", "arguments": {}})
        self.assertEqual(out.get("error", {}).get("code"), -32602, out)

    def test_unknown_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/elsewhere", "tools/list")
        self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
