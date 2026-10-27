"""MCP Apps views: the madde card reaches the host, and text-only clients see no change.

A host that renders MCP Apps finds the view through the tool's ``_meta.ui.resourceUri``
in ``tools/list`` and loads it with ``resources/read``. These tests drive the
Dispatcher directly, so they need no network and no backend index.

    python -m unittest tests.test_arayuz
"""

from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server  # noqa: E402
from mcpcore import Dispatcher, Resource, Tool  # noqa: E402

URI = "ui://arthurlegal/madde-karti.html"
SAMPLE = {
    "law": {"number": 6769, "title": "SINAİ MÜLKİYET KANUNU",
            "source_url": "https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=6769&MevzuatTur=1&MevzuatTertip=5"},
    "items": [{"ok": True, "madde_no": "120", "heading": "Çalışanın önalım hakkı",
               "text": "MADDE 120- (1) ...", "citation": "SINAİ MÜLKİYET KANUNU (Kanun No. 6769) m. 120"}],
}


def _call(dispatcher: Dispatcher, method: str, params: dict = None) -> dict:
    return dispatcher.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


class DispatcherResourcesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.view = Resource(URI, "Kart", "<html></html>", mime_type="text/html;profile=mcp-app",
                             meta={"ui": {"prefersBorder": False}})
        self.with_view = Tool("madde", "m", {"type": "object"}, lambda a: dict(SAMPLE),
                              meta={"ui": {"resourceUri": URI}})
        self.plain = Tool("duz", "d", {"type": "object"}, lambda a: dict(SAMPLE))
        self.d = Dispatcher([self.with_view, self.plain], name="t", version="0", resources=[self.view])

    def test_initialize_advertises_resources_only_when_there_are_some(self) -> None:
        caps = _call(self.d, "initialize")["result"]["capabilities"]
        self.assertIn("resources", caps)
        bare = Dispatcher([self.plain], name="t", version="0")
        self.assertNotIn("resources", _call(bare, "initialize")["result"]["capabilities"])

    def test_tools_list_carries_meta_only_for_the_tool_with_a_view(self) -> None:
        tools = {t["name"]: t for t in _call(self.d, "tools/list")["result"]["tools"]}
        self.assertEqual(tools["madde"]["_meta"]["ui"]["resourceUri"], URI)
        self.assertNotIn("_meta", tools["duz"])

    def test_resources_list_and_read(self) -> None:
        listed = _call(self.d, "resources/list")["result"]["resources"]
        self.assertEqual([r["uri"] for r in listed], [URI])
        content = _call(self.d, "resources/read", {"uri": URI})["result"]["contents"][0]
        self.assertEqual(content["mimeType"], "text/html;profile=mcp-app")
        self.assertEqual(content["text"], "<html></html>")
        self.assertFalse(content["_meta"]["ui"]["prefersBorder"])

    def test_unknown_resource_is_an_error_not_an_empty_read(self) -> None:
        response = _call(self.d, "resources/read", {"uri": "ui://yok"})
        self.assertEqual(response["error"]["code"], -32002)

    def test_tool_with_a_view_returns_the_same_object_as_text_and_as_data(self) -> None:
        result = _call(self.d, "tools/call", {"name": "madde", "arguments": {}})["result"]
        self.assertEqual(result["structuredContent"], SAMPLE)
        self.assertEqual(json.loads(result["content"][0]["text"]), SAMPLE)

    def test_tool_without_a_view_is_unchanged(self) -> None:
        result = _call(self.d, "tools/call", {"name": "duz", "arguments": {}})["result"]
        self.assertNotIn("structuredContent", result)


class ServerViewsTest(unittest.TestCase):
    """server._attach_views: carded tools, the al_masasi desk and the three views."""

    def setUp(self) -> None:
        self.saved_tools, self.saved_resources = list(server._tools), list(server._resources)
        server._tools[:] = [Tool(ad, "x", {"type": "object"}, lambda a: {})
                            for ad in ("tr_mevzuat_madde_getir", "tr_ictihat_ara", "status", "nl_search")]
        server._resources[:] = []

    def tearDown(self) -> None:
        server._tools[:] = self.saved_tools
        server._resources[:] = self.saved_resources

    def test_carded_tools_desk_and_views(self) -> None:
        server._attach_views()
        tools = {t.name: t for t in server._tools}
        self.assertEqual(tools["tr_mevzuat_madde_getir"].meta["ui"]["resourceUri"], URI)
        self.assertEqual(tools["tr_ictihat_ara"].meta["ui/resourceUri"], "ui://arthurlegal/ictihat-karti.html")
        self.assertIsNone(tools["nl_search"].meta)
        self.assertEqual(tools["al_masasi"].meta["ui"]["resourceUri"], "ui://arthurlegal/masa.html")
        self.assertEqual({r.uri for r in server._resources},
                         {URI, "ui://arthurlegal/ictihat-karti.html", "ui://arthurlegal/masa.html"})
        masa = tools["al_masasi"].handler({"bolum": "ictihat"})
        self.assertEqual((masa["ortam"], masa["bolum"]), ("uzak", "ictihat"))
        # Memory, masking and UYAP run on the lawyer's computer; this endpoint serves web and phone.
        self.assertEqual(masa["moduller"]["beyin"], {"durum": "masaustu"})
        self.assertEqual(masa["moduller"]["tapu"], {"durum": "claude"})
        self.assertEqual(masa["yetenekler"], ["status", "tr_ictihat_ara", "tr_mevzuat_madde_getir"])

    def test_views_are_whole_pages(self) -> None:
        server._attach_views()
        for r in server._resources:
            with self.subTest(uri=r.uri):
                self.assertEqual(r.mime_type, "text/html;profile=mcp-app")
                self.assertNotIn("/*AL:", r.text)
                self.assertIn("https://unpkg.com/@modelcontextprotocol/ext-apps@", r.text)
                self.assertIn("https://unpkg.com", r.meta["ui"]["csp"]["resourceDomains"])
                for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML"):
                    self.assertNotIn(forbidden, r.text)

    def test_without_arayuz_the_server_stays_text_only(self) -> None:
        saved = server.al_arayuz
        server.al_arayuz = None
        try:
            server._attach_views()
        finally:
            server.al_arayuz = saved
        self.assertEqual(server._resources, [])
        self.assertNotIn("al_masasi", [t.name for t in server._tools])
        self.assertTrue(all(t.meta is None for t in server._tools))


if __name__ == "__main__":
    unittest.main()
