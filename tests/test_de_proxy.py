"""The de-eli proxy must survive a de-eli restart.

A FastMCP server forgets its sessions when it restarts, and the MCP Streamable HTTP
spec answers a request that carries an unknown session id with HTTP 404. The client
is then expected to initialize again. The fake server below behaves exactly that way,
so these tests need no network and no de-eli install.

    python -m unittest tests.test_de_proxy
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock
import urllib.error
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server  # noqa: E402  (importing does not load any backend; build() does)


class FakeMcp:
    """Streamable-HTTP MCP server that forgets every session on restart()."""

    def __init__(self, handlers=None) -> None:
        self.sessions = set()
        self.handlers = dict(handlers or {})
        self.handlers.setdefault("de_echo", lambda args: {"echo": args})
        self.calls = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                sid = self.headers.get("Mcp-Session-Id")
                method = body.get("method")
                if method == "initialize":
                    new = uuid.uuid4().hex
                    outer.sessions.add(new)
                    return self._reply({"jsonrpc": "2.0", "id": body.get("id"), "result": {
                        "protocolVersion": "2025-06-18", "capabilities": {},
                        "serverInfo": {"name": "fake-de-eli", "version": "1"}}}, sid=new)
                if sid not in outer.sessions:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"Session not found")
                    return
                if method == "notifications/initialized":
                    self.send_response(202)
                    self.end_headers()
                    return
                if method == "tools/list":
                    return self._reply({"jsonrpc": "2.0", "id": body.get("id"), "result": {"tools": [
                        {"name": "de_echo", "description": "echo", "inputSchema": {"type": "object"}}]}})
                if method == "tools/call":
                    name, args = body["params"]["name"], body["params"]["arguments"]
                    outer.calls.append((name, args))
                    return self._reply({"jsonrpc": "2.0", "id": body.get("id"), "result": {
                        "structuredContent": outer.handlers[name](args)}})
                self.send_response(400)
                self.end_headers()

            def _reply(self, payload, sid=None):
                data = ("event: message\ndata: %s\n\n" % json.dumps(payload)).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                if sid:
                    self.send_header("Mcp-Session-Id", sid)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/mcp" % self.httpd.server_address[1]
        self.stopped = False
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def restart(self) -> None:
        self.sessions.clear()

    def stop(self) -> None:
        if not self.stopped:
            self.stopped = True
            self.httpd.shutdown()
            self.httpd.server_close()


class DeProxySessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeMcp()
        self.proxy = server._DeEliProxy(self.fake.url)
        self.proxy.connect()

    def tearDown(self) -> None:
        self.fake.stop()

    def test_call_works_on_a_fresh_session(self) -> None:
        self.assertEqual(self.proxy.call("de_echo", {"q": 1}), {"echo": {"q": 1}})

    def test_call_survives_a_de_eli_restart(self) -> None:
        self.assertEqual(self.proxy.call("de_echo", {"q": 1}), {"echo": {"q": 1}})
        self.fake.restart()  # the old session id is now unknown: the server answers 404
        self.assertEqual(self.proxy.call("de_echo", {"q": 2}), {"echo": {"q": 2}})

    def test_concurrent_calls_after_restart_open_one_session(self) -> None:
        self.fake.restart()
        results, errors = [], []

        def worker(i: int) -> None:
            try:
                results.append(self.proxy.call("de_echo", {"i": i}))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 8)
        self.assertEqual(len(self.fake.sessions), 1)

    def test_a_dead_backend_still_raises(self) -> None:
        self.fake.stop()
        with self.assertRaises(urllib.error.URLError):
            self.proxy.call("de_echo", {"q": 3})


class StatusProbeTest(unittest.TestCase):
    """`status` must report a German backend that stopped answering after boot."""

    def setUp(self) -> None:
        self.fake = FakeMcp()
        proxy = server._DeEliProxy(self.fake.url)
        proxy.connect()
        self._saved = server._de_proxy
        server._de_proxy = proxy

    def tearDown(self) -> None:
        server._de_proxy = self._saved
        self.fake.stop()

    def test_status_reports_live_backend_and_heals_a_lost_session(self) -> None:
        self.fake.restart()
        live = server._t_status({})["de_live"]
        self.assertTrue(live["reachable"])
        self.assertEqual(live["tools"], 1)
        self.assertEqual(live["session_renewals"], 1)

    def test_status_reports_dead_backend(self) -> None:
        self.fake.stop()
        out = server._t_status({})
        self.assertFalse(out["de_live"]["reachable"])
        self.assertIn("German backend", out["warning"])
        # The two gesetze-im-internet.de tools run in-process and keep working.
        self.assertIn("de_norm_getir", out["warning"])
        self.assertNotIn("every de_ tool", out["warning"])

    def test_status_names_the_build_that_answered(self) -> None:
        # v17, v18 and v19 all said "1.1.0"; the image tag tells them apart.
        saved = {k: os.environ.get(k) for k in ("FLY_IMAGE_REF", "FLY_MACHINE_ID", "FLY_REGION")}
        os.environ.update(FLY_IMAGE_REF="registry.fly.io/arthurlegal-mcp:onarim-20261009c",
                          FLY_MACHINE_ID="85e991b4657008", FLY_REGION="ams")
        try:
            out = server._t_status({})
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
        self.assertEqual(out["build"]["image"], "registry.fly.io/arthurlegal-mcp:onarim-20261009c")
        self.assertEqual(out["build"]["region"], "ams")
        self.assertNotIn("build", server._t_status({}))


def _act(abbr, published, name="Gesetz"):
    return {"name": name, "abbreviation": abbr, "alternateName": None,
            "exampleOfWork": {"datePublished": published}}


class ShapingTest(unittest.TestCase):
    """What the aggregator corrects in de-eli 0.5.4 output (observed live, 2026-10-09)."""

    def start(self, handlers):
        self.fake = FakeMcp(handlers)
        self.proxy = server._DeEliProxy(self.fake.url)
        self.proxy.connect()

    def tearDown(self) -> None:
        if getattr(self, "fake", None):
            self.fake.stop()

    def test_recent_changes_come_newest_first(self) -> None:
        ascending = [_act("A%d" % i, "2026-09-%02d" % (10 + i)) for i in range(6)]
        self.start({"de_recent_changes": lambda args: {"result": ascending}})
        out = server._de_call(self.proxy, "de_recent_changes", {"since_iso": "2026-09-01", "limit": 3})
        self.assertEqual(self.fake.calls[-1][1]["limit"], 300)   # widened, then cut
        self.assertEqual([a["abbreviation"] for a in out["result"]], ["A5", "A4", "A3"])

    def test_decision_citation_is_rebuilt_from_german_fields(self) -> None:
        german = {"courtName": "BGH", "gericht": "BGH", "datum": "2011-07-22", "decisionDate": None,
                  "aktenzeichen": "V ZR 245/09", "aktenzeichenListe": ["V ZR 245/09"], "fileNumbers": [],
                  "dokumentNummer": "JURE110015859", "documentNumber": None, "dokumenttyp": "Urteil",
                  "human_readable_citation": "BGH"}
        text = {"content": "<html>…</html>", "human_readable_citation": "BGH"}
        self.start({"de_get_decision": lambda args: dict(german),
                    "de_get_decision_text": lambda args: dict(text)})
        out = server._de_call(self.proxy, "de_get_decision", {"document_number": "JURE110015859"})
        self.assertEqual(out["human_readable_citation"], "BGH, Urteil vom 22.07.2011 - V ZR 245/09")
        self.assertEqual(out["documentNumber"], "JURE110015859")
        out = server._de_call(self.proxy, "de_get_decision_text", {"document_number": "JURE110015859"})
        self.assertEqual(out["human_readable_citation"], "BGH, Urteil vom 22.07.2011 - V ZR 245/09")

    def test_a_dated_citation_is_left_alone(self) -> None:
        good = {"human_readable_citation": "BGH, Urteil vom 22.07.2011 - V ZR 245/09"}
        self.start({"de_get_decision": lambda args: dict(good)})
        out = server._de_call(self.proxy, "de_get_decision", {"document_number": "X"})
        self.assertNotIn("citation_note", out)

    def test_dip_is_compacted_without_losing_records(self) -> None:
        items = [{"id": str(i), "titel": "T%d" % i, "datum": "2026-01-01", "text": "x" * 5000} for i in range(48)]
        self.start({"de_dip_search": lambda args: {"total_items": 48, "items": items, "cursor": "c"}})
        out = server._de_call(self.proxy, "de_dip_search", {"query": {"titel": "Bürgergeld"}})
        self.assertEqual(len(out["items"]), 10)
        self.assertEqual(len(out["more_items"]), 38)
        self.assertEqual(out["cursor"], "c")
        self.assertLess(len(out["items"][0]["text"]), 2100)

    def test_search_widens_the_pool_and_flags_a_missing_code(self) -> None:
        pool = [_act("X%d" % i, "2020-01-01") for i in range(50)]
        self.start({"de_search": lambda args: {"total_items": 19, "items": list(pool)}})
        out = server._de_call(self.proxy, "de_search", {"query": {"searchTerm": "BGB", "size": 5}})
        self.assertEqual(self.fake.calls[-1][1]["query"]["size"], 50)
        self.assertEqual(len(out["items"]), 5)
        self.assertIn("de_norm_getir", out["not_in_neuris"])

    def test_no_missing_code_note_when_the_act_is_there(self) -> None:
        self.start({"de_search": lambda args: {"items": [_act("GG", "1949-05-23")]}})
        out = server._de_call(self.proxy, "de_search", {"query": {"searchTerm": "GG", "size": 5}})
        self.assertNotIn("not_in_neuris", out)

    def test_a_title_word_is_not_taken_for_an_abbreviation(self) -> None:
        self.start({"de_search": lambda args: {"items": [_act("AktG", "1965-09-06", "Aktiengesetz")]}})
        out = server._de_call(self.proxy, "de_search", {"query": {"searchTerm": "Aktiengesetz", "size": 3}})
        self.assertNotIn("not_in_neuris", out)

    def test_stale_rii_description_is_corrected(self) -> None:
        stale = "complete for BGH - unlike NeuRIS's `/v1/rechtsprechung`, which is a small beta slice. Filters"
        fixed = stale
        for old in server._RII_STALE:
            fixed = fixed.replace(old, server._RII_CURRENT)
        self.assertNotIn("small beta slice", fixed)
        self.assertIn("84,474", fixed)

    def test_stale_oldp_description_is_corrected(self) -> None:
        # de-eli 0.5.4, de_oldp_case_search (live text, 2026-11-10).
        stale = ("It is the only source here that\ncovers STATE courts and the only one with full-text "
                 "search. Database ODbL v1.0;")
        fixed = stale.replace(server._OLDP_STALE, server._OLDP_CURRENT)
        self.assertNotIn("the only one with full-text search", fixed)
        self.assertIn("de_case_search", fixed)


class WarmResidentTest(unittest.TestCase):
    """After boot the resident vector copies are built in the background (1.3.0)."""

    def test_boot_builds_the_resident_copies(self) -> None:
        sys.path.insert(0, os.path.join(ROOT, "arthur-tr-hukuk-mcp"))
        import retrieval  # the copy the aggregator's backends share
        if retrieval._np is None:
            self.skipTest("numpy is not installed here")
        idx = retrieval.Index(os.path.join(tempfile.mkdtemp(), "index.db"))
        doc_id = idx.upsert({"ref": "a", "title": "t", "body": "b"})
        idx.db.execute("INSERT INTO vecs(doc_id, dim, vec, model) VALUES(?,?,?,?)",
                       (doc_id, 4, retrieval._pack([1.0, 0.0, 0.0, 0.0]), retrieval.embeddings_model()))
        idx.db.commit()
        fake = types.ModuleType("warm_fake_backend")
        fake._index = idx
        sys.modules["warm_fake_backend"] = fake
        entry = {"prefix": "warm", "module": "warm_fake_backend"}
        server._loaded.append(entry)
        try:
            with mock.patch.dict(os.environ, {"SEMANTIC_RESIDENT_MB": "1"}):
                thread = server._warm_resident()
                thread.join(10)
            self.assertIsNotNone(getattr(idx, "_resident", None))
            with mock.patch.dict(os.environ, {"SEMANTIC_RESIDENT_MB": "0"}):
                self.assertIsNone(server._warm_resident())
        finally:
            server._loaded.remove(entry)
            del sys.modules["warm_fake_backend"]
            idx._drop_resident()
            idx.db.close()


if __name__ == "__main__":
    unittest.main()
