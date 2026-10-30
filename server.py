#!/usr/bin/env python3
"""arthurlegal-mcp — every ArthurLegal jurisdiction server behind one endpoint.

    python server.py                                 # stdio
    python server.py --transport http --port 8900    # http://127.0.0.1:8900/mcp

One connector instead of ten. The point is not tidiness: quick-tunnel URLs change
on every restart, so ten connectors meant ten addresses to re-enter by hand each
time. One endpoint means one.

How it is put together, and why
-------------------------------
The nine standard-library servers are **loaded in-process**, not proxied. Their
tool handlers are called directly, so aggregation costs nothing measurable — and
the whole thing ships as a single container with no network between the parts.
de-eli runs on FastMCP and is async, so it is proxied over HTTP when reachable
(measured at ~15 ms against a ~1,400 ms search: about 1%).

**Every tool is prefixed with its jurisdiction.** This is not cosmetic. Across
the ten servers `get_act` means five different things, `search_legislation`
three, `search_acts` three. Merging them unprefixed would route a Spanish query
to a Finnish server and answer confidently with the wrong country's law — the
one failure mode this whole package exists to prevent.

**The nine per-server `server_status` tools collapse into one.** Nine tools that
each answer "am I healthy" is nine ways to ask the same question; `status`
answers it once for everything, and reports which backends failed to load rather
than quietly serving fewer tools.

Each backend keeps its own SQLite index — `INDEX_PATH` is set per backend during
load — so consolidating changes nothing about what each jurisdiction knows.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcpcore import Dispatcher, McpError, Resource, Tool, run  # noqa: E402
from tool_titles import annotate  # noqa: E402

__version__ = "1.6.0"

# The Claude connector directory listing points here, not at /mcp. Same server,
# narrower profile: the land-registry tools stay on /mcp only, and the
# instructions describe the tools instead of directing Claude. Every other tool
# is the same object on both paths.
DIRECTORY_PATH = "/directory/mcp"
DIRECTORY_EXCLUDED_PREFIXES: Tuple[str, ...] = ("tkgm",)

HERE = os.path.dirname(os.path.abspath(__file__))
# Deployed bundle: every backend sits next to this file.
ROOT = HERE
AL = HERE

# prefix, directory, module name, human label. The prefix is the jurisdiction the
# tool answers for, so a reader of a tool name always knows which law they are in.
STDLIB_BACKENDS = [
    # Türkiye loads FIRST on purpose: every backend does `import retrieval` by bare
    # name and Python caches the first copy. arthur-tr-hukuk-mcp's retrieval.py is the
    # same module plus dotless-ı query expansion, so letting it win costs the
    # others nothing and keeps Turkish queries working.
    ("tr", os.path.join(AL, "arthur-tr-hukuk-mcp"), "srv_tr",
     "🇹🇷 Türkiye — içtihat + mevzuat + 8 düzenleyici kurum + Resmî Gazete"),
    ("nl", os.path.join(ROOT, "nl-rechtspraak-mcp"), "srv_nl", "🇳🇱 Hollanda — içtihat + mevzuat"),
    ("pl", os.path.join(ROOT, "pl-sejm-mcp"), "srv_pl", "🇵🇱 Polonya — mevzuat"),
    ("at", os.path.join(ROOT, "at-ris-mcp"), "srv_at", "🇦🇹 Avusturya — mevzuat + içtihat"),
    ("ie", os.path.join(ROOT, "ie-statutebook-mcp"), "srv_ie", "🇮🇪 İrlanda — Act'ler"),
    ("fi", os.path.join(ROOT, "fi-finlex-mcp"), "srv_fi", "🇫🇮 Finlandiya — mevzuat"),
    ("es", os.path.join(ROOT, "es-boe-mcp"), "srv_es", "🇪🇸 İspanya — mevzuat"),
    ("uk", os.path.join(ROOT, "uk-legislation-mcp"), "srv_uk",
     "🇬🇧 Birleşik Krallık — mevzuat + işlenmemiş tadiller"),
    ("eu", os.path.join(ROOT, "eu-cellar-mcp"), "srv_eu",
     "🇪🇺 AB — mevzuat + CJEU içtihadı (CELLAR)"),
    ("jp", os.path.join(ROOT, "jp-egov-mcp"), "srv_jp", "🇯🇵 Japonya — mevzuat"),
    ("gleif", os.path.join(ROOT, "gleif-mcp"), "srv_gleif",
     "🌍 Tüzel kişi kimliği + grup yapısı (LEI)"),
    # Not a jurisdiction and not a corpus. It fetches single parcels from TKGM Parsel
    # Sorgu through one rate-limited gate (tkgm_canli: one request in flight per machine,
    # at most 30 a minute for all users and machines together, backs off on 429/503,
    # stops on 403; see tkgm-mcp/docs/MANIFESTO.md). Under the HTTP transport it refuses
    # file paths.
    ("tkgm", os.path.join(ROOT, "tkgm-mcp"), "srv_tkgm",
     "🇹🇷 Tapu-kadastro — canlı parsel (TKGM Parsel Sorgu, dakikada en çok 30 istek), rapor, kroki"),
    # German statute text from gesetze-im-internet.de. NeuRIS, behind the proxied
    # de-eli tools, lacks the core codes in its test phase (BGB, HGB, StGB, ZPO ...),
    # so the text of a norm comes from here: same de_ prefix, different tool names.
    ("de", os.path.join(ROOT, "de-gii-mcp"), "srv_de_gii",
     "🇩🇪 Almanya — kanun metni (gesetze-im-internet.de)"),
]

# The three older servers expose TOOLS as dicts with a "handler" key rather than
# mcpcore.Tool objects, and live one package deep.
LEGACY_BACKENDS = [
    ("az", os.path.join(AL, "eqanun-api"), "eqanun.mcp_server", "🇦🇿 Azerbaycan — mevzuat"),
    ("scholar", os.path.join(AL, "lex-scholar-api"), "lexscholar.mcp_server", "🌍 Hukuk doktrini"),
    ("contracts", os.path.join(AL, "resourcecontracts-api"), "resourcecontracts.mcp_server",
     "🌍 İmzalı sözleşme emsali"),
]

# de-eli is async/FastMCP; proxied rather than imported. Its tools already carry a
# de_ prefix upstream, so they are passed through unrenamed.
DE_ELI_URL = os.environ.get("DE_ELI_URL", "http://127.0.0.1:8790/mcp")

_loaded: List[Dict[str, Any]] = []
_failed: List[Dict[str, str]] = []
_tools: List[Tool] = []
_instructions: List[str] = []
# The same sections with their prefix, so a profile can leave one out.
_instruction_parts: List[Tuple[str, str]] = []


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def _prefixed(prefix: str, name: str) -> str:
    return "%s_%s" % (prefix, name)


def _load_stdlib(prefix: str, directory: str, modname: str, label: str) -> None:
    """Import a v1.6.0 server and adopt its tools under a prefix.

    Each of these imports ``mcpcore``, ``retrieval`` and its own client by bare
    name, so the directory has to be on ``sys.path`` during exec. INDEX_PATH is
    set per backend, which is what keeps each jurisdiction pointed at its own
    SQLite index instead of sharing one.
    """
    sys.path.insert(0, directory)
    previous_index = os.environ.get("INDEX_PATH")
    os.environ["INDEX_PATH"] = os.path.join(directory, "data", "index.db")
    try:
        spec = importlib.util.spec_from_file_location(
            modname, os.path.join(directory, "server.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[modname] = module
        spec.loader.exec_module(module)

        count = 0
        for tool in module.TOOLS:
            if tool.name in ("server_status", "status"):
                continue          # replaced by the aggregate `status` tool
            _tools.append(Tool(
                _prefixed(prefix, tool.name),
                "[%s] %s" % (label, tool.description),
                tool.input_schema,
                tool.handler,
            ))
            count += 1
        text = getattr(module, "INSTRUCTIONS", "")
        if text:
            _instructions.append("### %s  (araç öneki: `%s_`)\n%s" % (label, prefix, text))
            _instruction_parts.append((prefix, _instructions[-1]))
        _loaded.append({"prefix": prefix, "label": label, "tools": count,
                        "mode": "in-process", "module": modname})
    except Exception as exc:  # noqa: BLE001 - one bad backend must not sink the rest
        _failed.append({"prefix": prefix, "label": label,
                        "error": "%s: %s" % (type(exc).__name__, exc)})
    finally:
        if directory in sys.path:
            sys.path.remove(directory)
        if previous_index is None:
            os.environ.pop("INDEX_PATH", None)
        else:
            os.environ["INDEX_PATH"] = previous_index


def _load_legacy(prefix: str, directory: str, dotted: str, label: str) -> None:
    """Import one of the older dict-shaped servers and adopt its tools."""
    sys.path.insert(0, directory)
    try:
        module = importlib.import_module(dotted)
        count = 0
        for entry in module.TOOLS:
            if entry["name"] in ("server_status", "status"):
                continue
            _tools.append(Tool(
                _prefixed(prefix, entry["name"]),
                "[%s] %s" % (label, entry["description"]),
                entry["inputSchema"],
                entry["handler"],
            ))
            count += 1
        _loaded.append({"prefix": prefix, "label": label, "tools": count,
                        "mode": "in-process", "module": dotted})
    except Exception as exc:  # noqa: BLE001
        _failed.append({"prefix": prefix, "label": label,
                        "error": "%s: %s" % (type(exc).__name__, exc)})
    finally:
        if directory in sys.path:
            sys.path.remove(directory)


# --------------------------------------------------------------------------- #
# Favicon and logo
# --------------------------------------------------------------------------- #
# The Claude connector directory, and Claude itself, show the favicon of the
# server's URL next to its name. GET /favicon.ico answered 405 here, so the listing
# fell back to a generic icon. mcpcore serves whatever is in STATIC_FILES; the
# files sit in varlik/, which the Dockerfile copies with everything else. A missing
# file leaves only its own path unanswered: no tool depends on it.
_VARLIK = os.path.join(HERE, "varlik")
_STATIC = {
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/logo.png": ("logo.png", "image/png"),
}


def _register_static() -> None:
    import mcpcore  # the module `run` came from; the backends import the same one
    for path, (filename, ctype) in _STATIC.items():
        try:
            with open(os.path.join(_VARLIK, filename), "rb") as fh:
                mcpcore.STATIC_FILES[path] = (ctype, fh.read())
        except OSError as exc:
            sys.stderr.write("arthurlegal-mcp: %s not served (%s)\n" % (path, exc))


_register_static()


# --------------------------------------------------------------------------- #
# de-eli proxy
# --------------------------------------------------------------------------- #
class _DeEliProxy:
    """Minimal MCP client for the one backend that cannot be imported.

    FastMCP hands out a session id on initialize that later calls must echo, and
    answers in SSE frames. Both are handled here so the rest of the aggregator
    does not have to know de-eli is different.

    A session does not outlive the de-eli process. The MCP Streamable HTTP spec
    answers a request carrying an unknown session id with HTTP 404, after which
    the client must initialize again. Before this was handled, one de-eli restart
    left all fifteen de_ tools failing with "Not Found" until the aggregator itself
    restarted (observed live on 2026-10-09).
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.session: Optional[str] = None
        self.tools: List[Dict[str, Any]] = []
        self.renewals = 0
        self._lock = threading.Lock()

    def _post(self, payload: dict, timeout: float = 90.0):
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        if self.session:
            req.add_header("Mcp-Session-Id", self.session)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace"), dict(resp.headers)

    @staticmethod
    def _unwrap(raw: str) -> dict:
        import re
        m = re.search(r"^data:\s*(\{.*)$", raw, re.M)
        return json.loads(m.group(1) if m else raw)

    def connect(self, timeout: float = 20.0) -> None:
        raw, headers = self._post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "arthurlegal-mcp", "version": __version__}},
        }, timeout)
        self.session = next((v for k, v in headers.items()
                             if k.lower() == "mcp-session-id"), None)
        self._unwrap(raw)
        if self.session:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout)
        raw, _ = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, timeout)
        self.tools = self._unwrap(raw).get("result", {}).get("tools", []) or []

    def _renewing(self, attempt: Callable[[], Any]) -> Any:
        """Run ``attempt``; on an unknown session (404) open a new one and retry once.

        The lock makes concurrent callers that all hit the 404 share one new
        session instead of each opening their own. Anything other than a 404 --
        a refused connection, a timeout, a 5xx -- is not a session problem and is
        raised unchanged.
        """
        stale = self.session
        try:
            return attempt()
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
        with self._lock:
            if self.session == stale:
                self.session = None
                self.connect()
                self.renewals += 1
        return attempt()

    def ping(self, timeout: float = 10.0) -> int:
        """A tools/list round trip through the current session; returns the tool count."""
        def attempt() -> int:
            raw, _ = self._post({"jsonrpc": "2.0", "id": 4, "method": "tools/list"}, timeout)
            return len(self._unwrap(raw).get("result", {}).get("tools", []) or [])
        return self._renewing(attempt)

    def call(self, name: str, arguments: dict) -> Any:
        return self._renewing(lambda: self._call_once(name, arguments))

    def _call_once(self, name: str, arguments: dict) -> Any:
        raw, _ = self._post({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": name, "arguments": arguments}})
        result = self._unwrap(raw).get("result", {})
        data = result.get("structuredContent")
        if data is not None:
            return data
        content = result.get("content") or [{}]
        text = content[0].get("text", "")
        try:
            return json.loads(text)
        except ValueError:
            return text


# --------------------------------------------------------------------------- #
# de-eli result shaping
# --------------------------------------------------------------------------- #
# Search tools whose candidates are reordered by meaning, with the fields that
# describe a candidate. NeuRIS returns matches in its own order, not by
# relevance, so the pool is widened first: reranking only the first page would
# put the best of the wrong five on top.
_DE_RERANK = {
    "de_search": ("name", "abbreviation", "alternateName"),
    "de_case_search": ("headline", "titleLine", "courtName", "otherLongText"),
    "de_oldp_case_search": ("_snippets", "court_name", "decision_type"),
}
_DE_POOL = 50
_DIP_KEEP = 10
_DIP_FIELD_CHARS = 2000
_DE_DATED = re.compile(r"\d{2}\.\d{2}\.\d{4}")
_RII_STALE = (
    "unlike NeuRIS's `/v1/rechtsprechung`, which is a small beta slice",
    "unlike NeuRIS's `/v1/case-law`, which is a small beta slice",
)
_RII_CURRENT = (
    "like NeuRIS's `/v1/rechtsprechung` (de_case_search), which since 2026 carries the same "
    "courts from 2010 with full-text search (84,474 decisions on 2026-10-09)")


def _de_call(proxy: _DeEliProxy, name: str, args: Dict[str, Any]) -> Any:
    """Call a de-eli tool and correct what de-eli 0.5.4 gets wrong or leaves raw."""
    if name == "de_recent_changes":
        return _de_recent_changes(proxy, args)
    if name in _DE_RERANK:
        return _de_reranked(proxy, name, args)
    out = proxy.call(name, args)
    if name == "de_dip_search":
        return _de_compact_dip(out)
    if name in ("de_get_decision", "de_get_decision_text"):
        return _de_decision_citation(proxy, name, args, out)
    return out


def _de_reranked(proxy: _DeEliProxy, name: str, args: Dict[str, Any]) -> Any:
    args = json.loads(json.dumps(args or {}))
    query = args.get("query") or {}
    text = (query.get("searchTerm") or query.get("text") or "").strip()
    want = None
    if text and name != "de_oldp_case_search":   # OLDP pages are fixed at ten
        want = int(query.get("size") or 20)
        query["size"] = max(want, _DE_POOL)
        args["query"] = query
    out = proxy.call(name, args)
    if not isinstance(out, dict) or not text:
        return out
    items = out.get("items") or []
    if name == "de_oldp_case_search":
        for item in items:
            item["_snippets"] = re.sub(r"<[^>]+>", "", " ".join(item.get("snippets") or []))
    try:
        import retrieval  # the copy the bundled backends share; holds the embeddings client
        ranked = retrieval.semantic_rerank(text, items, fields=_DE_RERANK[name], limit=want or 0)
        out["items"] = ranked["results"]
        out["ranking"] = {k: v for k, v in ranked.items() if k != "results"}
    except Exception as exc:  # noqa: BLE001 - ranking is a bonus; the hits are not
        out["items"] = items[:want] if want else items
        out["ranking"] = {"method": "none", "warning": "reranking failed (%s: %s); upstream order kept."
                          % (type(exc).__name__, exc)}
    for item in out["items"]:
        item.pop("_snippets", None)
    if want and len(items) > want:
        out["ranking"]["pool"] = len(items)
    if name == "de_search":
        _de_missing_act_hint(text, out)
    return out


def _de_missing_act_hint(term: str, out: Dict[str, Any]) -> None:
    """Say so when an abbreviation is not in NeuRIS, instead of letting unrelated acts stand in.

    NeuRIS's test-phase dataset (about 5,500 acts) lacks the core codes: BGB,
    HGB, StGB, ZPO, StPO, AO, UrhG, GWB and InsO all return 0 for
    /v1/legislation?abbreviation= (checked 2026-10-09).
    """
    # Only abbreviation-shaped terms (BGB, StGB, InsO, GmbHG): a title word such as
    # "Aktiengesetz" is no claim that an act by that abbreviation exists.
    if " " in term or len(term) > 12 or sum(ch.isupper() for ch in term) < 2:
        return
    for item in out.get("items") or []:
        names = (item.get("abbreviation"), item.get("name"), item.get("alternateName"))
        if any((n or "").casefold() == term.casefold() for n in names):
            return
    out["not_in_neuris"] = (
        "No act abbreviated %r in these NeuRIS results. NeuRIS is in its test phase and lacks core "
        "codes (BGB, HGB, StGB, ZPO, StPO, AO, UrhG, GWB, InsO). Get the consolidated text from "
        "gesetze-im-internet.de: de_norm_getir(kanun=%r, norm=...)." % (term, term))


def _de_recent_changes(proxy: _DeEliProxy, args: Dict[str, Any]) -> Any:
    """Newest first, as the tool promises.

    de-eli asks NeuRIS for sort=date, which is ascending, and cuts to `limit`, so
    it returned the OLDEST acts since the date. Fetch the 300 NeuRIS allows, order
    by publication date, then cut.
    """
    want = max(1, min(int((args or {}).get("limit") or 50), 300))
    out = proxy.call("de_recent_changes", dict(args or {}, limit=300))
    items = out.get("result") if isinstance(out, dict) else out
    if not isinstance(items, list):
        return out
    items.sort(key=lambda item: ((item.get("exampleOfWork") or {}).get("datePublished") or ""),
               reverse=True)
    shaped: Dict[str, Any] = {"result": items[:want], "order": "newest first, by datePublished"}
    if len(items) >= 300:
        shaped["warning"] = ("300 acts since since_iso, which is NeuRIS's cap: newer acts beyond "
                             "them may be missing. Use a later since_iso.")
    return shaped


def _de_compact_dip(out: Any) -> Any:
    """DIP has no page size: one `vorgang` search returned 48 full records (63,846 characters).

    Keep the first ten whole, list the rest by id, title and date so the cursor
    still lines up, and clip long text fields.
    """
    if not isinstance(out, dict):
        return out
    items = out.get("items") or []
    for item in items:
        for key, value in list(item.items()):
            if isinstance(value, str) and len(value) > _DIP_FIELD_CHARS:
                item[key] = value[:_DIP_FIELD_CHARS] + " …[truncated]"
    if len(items) > _DIP_KEEP:
        out["items"] = items[:_DIP_KEEP]
        out["more_items"] = [
            {k: item.get(k) for k in ("id", "titel", "datum", "dokumentnummer", "dokumentart",
                                      "vorgangstyp") if item.get(k) is not None}
            for item in items[_DIP_KEEP:]]
        out["compacted"] = ("First %d records in full, the other %d by id, title and date; "
                            "open any of them with de_dip_get_document."
                            % (_DIP_KEEP, len(items) - _DIP_KEEP))
    return out


def _de_decision_citation(proxy: _DeEliProxy, name: str, args: Dict[str, Any], out: Any) -> Any:
    """Rebuild the citation that NeuRIS's renamed fields broke.

    /v1/rechtsprechung/{nr} answers a single decision with German keys
    (gericht, dokumenttyp, datum, aktenzeichen). de-eli 0.5.4 reads the English
    ones, finds them empty and cites a Federal Court of Justice judgment as just
    "BGH" (observed 2026-10-09 on JURE110015859).
    """
    if not isinstance(out, dict) or _DE_DATED.search(out.get("human_readable_citation") or ""):
        return out
    meta = out
    if name == "de_get_decision_text":
        try:
            meta = proxy.call("de_get_decision", {"document_number": (args or {}).get("document_number")})
        except Exception:  # noqa: BLE001 - keep the text, without a better citation
            return out
        if not isinstance(meta, dict):
            return out
    court = meta.get("courtName") or meta.get("gericht") or ""
    kind = meta.get("documentType") or meta.get("dokumenttyp") or "Entscheidung"
    day = meta.get("decisionDate") or meta.get("datum") or ""
    files = (meta.get("fileNumbers") or meta.get("aktenzeichenListe")
             or ([meta["aktenzeichen"]] if meta.get("aktenzeichen") else []))
    if not (court and day):
        return out
    if re.match(r"\d{4}-\d{2}-\d{2}$", day):
        day = "%s.%s.%s" % (day[8:10], day[5:7], day[:4])
    citation = "%s, %s vom %s" % (court, kind, day)
    if files:
        citation += " - " + ", ".join(files)
    out["human_readable_citation"] = citation
    for key, value in (("documentNumber", meta.get("dokumentNummer")), ("decisionDate", meta.get("datum")),
                       ("fileNumbers", files), ("documentType", meta.get("dokumenttyp"))):
        if not out.get(key) and value:
            out[key] = value
    out["citation_note"] = ("Citation rebuilt from NeuRIS's German fields (gericht, dokumenttyp, datum, "
                            "aktenzeichen); de-eli 0.5.4 reads English ones that single decisions no "
                            "longer fill.")
    return out


_de_proxy: Optional[_DeEliProxy] = None


def _load_de_eli(url: str) -> None:
    global _de_proxy
    label = "🇩🇪 Almanya — mevzuat + içtihat"
    proxy = _DeEliProxy(url)
    try:
        proxy.connect()
    except Exception as exc:  # noqa: BLE001
        _failed.append({"prefix": "de", "label": label,
                        "error": "proxy unreachable at %s (%s)" % (url, exc),
                        "hint": "de-eli is a separate process; start it or set DE_ELI_URL."})
        return
    _de_proxy = proxy

    count = 0
    for spec in proxy.tools:
        name = spec.get("name", "")
        if name in ("de_ranking_status",):
            continue
        # de-eli already namespaces its tools with de_; renaming would break the
        # identifiers its own instructions and this package's guides refer to.
        def make(tool_name: str) -> Callable[[Dict[str, Any]], Any]:
            def handler(args: Dict[str, Any]) -> Any:
                try:
                    return _de_call(proxy, tool_name, args)
                except urllib.error.URLError as exc:
                    raise McpError(
                        "de-eli backend unreachable (%s). The other jurisdictions "
                        "are unaffected." % exc.reason) from exc
            return handler
        description = spec.get("description", "")
        for stale in _RII_STALE:
            description = description.replace(stale, _RII_CURRENT)
        _tools.append(Tool(name, "[%s] %s" % (label, description),
                           spec.get("inputSchema", {"type": "object", "properties": {}}),
                           make(name)))
        count += 1
    _loaded.append({"prefix": "de", "label": label, "tools": count,
                    "mode": "proxy → %s" % url})


# --------------------------------------------------------------------------- #
def _warm_resident() -> Optional[threading.Thread]:
    """Build the resident vector copies in the background after boot.

    The first semantic query on each index used to build its copy itself: 10-22 s
    for the first callers after the 2026-10-10 deploy, while the files were still
    cold. Warming is an optimisation; a failure only means a query builds it later.
    """
    import retrieval  # the copy every bundled backend shares
    if retrieval._np is None or retrieval._resident_budget() <= 0:
        return None
    model = retrieval.embeddings_model()
    scan_max = int(os.environ.get("SEMANTIC_SCAN_MAX", "50000"))

    def warm() -> None:
        for entry in list(_loaded):
            mod = sys.modules.get(entry.get("module", ""))
            idx = getattr(mod, "_index", None) if mod is not None else None
            if idx is None and mod is not None and callable(getattr(mod, "index", None)):
                try:
                    idx = mod.index()
                except Exception:  # noqa: BLE001
                    idx = None
            if not isinstance(idx, retrieval.Index):
                continue
            try:
                row = idx.db.execute("SELECT dim FROM vecs WHERE model = ? LIMIT 1", (model,)).fetchone()
                if row:
                    idx._resident_for(model, int(row[0]), scan_max)
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write("resident warm-up %s: %s\n" % (entry.get("prefix"), exc))

    thread = threading.Thread(target=warm, name="resident-warmup", daemon=True)
    thread.start()
    return thread


def _runtime() -> Dict[str, Any]:
    """Which build answered and how much memory it holds.

    Three deploys in a row reported version 1.1.0 (2026-10-09), so a measurement
    could not tell which one it had measured; the image tag can. Memory is read
    from /proc, where there is one (the hosted machines run Linux).
    """
    out: Dict[str, Any] = {}
    if os.environ.get("FLY_IMAGE_REF"):
        out["build"] = {"image": os.environ["FLY_IMAGE_REF"],
                        "machine": os.environ.get("FLY_MACHINE_ID", ""),
                        "region": os.environ.get("FLY_REGION", "")}
    memory: Dict[str, int] = {}
    for path, key, name in (("/proc/self/status", "VmRSS:", "process_rss_mb"),
                            ("/proc/meminfo", "MemTotal:", "machine_total_mb"),
                            ("/proc/meminfo", "MemAvailable:", "machine_available_mb")):
        try:
            with open(path) as fh:
                for line in fh:
                    if line.startswith(key):
                        memory[name] = int(line.split()[1]) // 1024
                        break
        except (OSError, ValueError, IndexError):
            pass
    if memory:
        out["memory"] = memory
    return out


def _status(exclude: Tuple[str, ...] = (), exposed: Optional[int] = None) -> Any:
    """One health answer for the whole package, or for a profile that leaves
    the ``exclude`` prefixes out."""
    loaded = [e for e in _loaded if e["prefix"] not in exclude]
    failed = [f for f in _failed if f["prefix"] not in exclude]
    prefixes = list(dict.fromkeys(e["prefix"] for e in loaded))
    out: Dict[str, Any] = {
        "server": "arthurlegal-mcp",
        "version": __version__,
        **_runtime(),
        "backends_loaded": loaded,
        "tools_exposed": len(_tools) if exposed is None else exposed,
        "note": "Every tool is prefixed with its jurisdiction (%s). Across the underlying "
                "servers `get_act` means six different things, so the prefix is "
                "what keeps a Spanish question from being answered with Finnish law."
                % ", ".join("%s_" % p for p in prefixes),
    }
    if failed:
        out["backends_failed"] = failed
        out["warning"] = (
            "%d backend did not load. Its jurisdiction is UNAVAILABLE — not empty. "
            "Do not read a missing result as 'no such law'." % len(failed)
        )
    # Ask each in-process backend what it knows about itself.
    detail = {}
    for entry in loaded:
        mod = sys.modules.get(entry.get("module", ""))
        if mod is None:
            continue
        fn = getattr(mod, "_t_status", None) or getattr(mod, "_t_server_status", None)
        if callable(fn):
            try:
                detail[entry["prefix"]] = fn({})
            except Exception as exc:  # noqa: BLE001
                detail[entry["prefix"]] = {"error": str(exc)}
    if detail:
        out["backend_status"] = detail
    # de-eli is a separate process, so "loaded at boot" says nothing about now.
    # Probe it live: a dead or wedged German backend must show up here instead of
    # as fifteen tools that each fail on their own.
    if _de_proxy is not None:
        try:
            out["de_live"] = {"reachable": True, "tools": _de_proxy.ping(),
                              "session_renewals": _de_proxy.renewals}
        except Exception as exc:  # noqa: BLE001
            out["de_live"] = {"reachable": False, "error": "%s: %s" % (type(exc).__name__, exc)}
            out["warning"] = (out.get("warning", "") + " The German backend is loaded but "
                              "not answering: every de_ tool will fail until it is back.").strip()
    return out


def _t_status(args: Dict[str, Any]) -> Any:
    return _status()


def build() -> None:
    for prefix, directory, modname, label in STDLIB_BACKENDS:
        _load_stdlib(prefix, directory, modname, label)
    for prefix, directory, dotted, label in LEGACY_BACKENDS:
        _load_legacy(prefix, directory, dotted, label)
    if DE_ELI_URL and DE_ELI_URL.lower() != "off":
        _load_de_eli(DE_ELI_URL)

    _tools.append(Tool(
        "status",
        "Health of every jurisdiction behind this endpoint: which backends "
        "loaded, how many documents each has indexed, what date range was "
        "crawled, and whether semantic search is live. Call it when results look "
        "thin — it distinguishes an unavailable jurisdiction from a genuinely "
        "empty result set.",
        {"type": "object", "properties": {}},
        _t_status,
    ))
    _attach_views()
    for tool in _tools:
        annotate(tool)


# --------------------------------------------------------------------------- #
# MCP Apps: ArthurLegal Masası and the in-chat cards
# --------------------------------------------------------------------------- #
# The views live in arayuz/ (a copy; their home is the private arthurlegal-app4all
# repository, synced with its sunucu/esitle.py). A host that renders MCP Apps
# (Claude on the web, desktop and mobile) draws the card beside the call; every
# other client keeps reading the same JSON text, so no tool contract changes.
# Without arayuz/ the server runs as before, text only.
sys.path.insert(0, os.path.join(HERE, "arayuz"))
try:
    import al_arayuz  # noqa: E402
except ImportError:
    al_arayuz = None

# The tools the desk calls directly. Modules that run on the lawyer's computer
# (memory, masking, UYAP) are marked "masaustu" here: this endpoint serves the web
# and phone, where they do not exist by design. Land registry goes through Claude
# (the tkgm_ tools below are on this endpoint).
MASA_TOOLS = ("tr_mevzuat_madde_getir", "tr_mevzuat_ara", "tr_mevzuat_icindekiler", "tr_ictihat_ara", "tr_ictihat_getir",
              "tr_kurum_karari_ara", "tr_kurum_karari_getir", "tr_semantik_ara", "tr_belge_getir",
              "tr_spk_bulten_icinde_ara", "status")
REMOTE_MODULES = {
    "tapu": {"durum": "claude"}, "beyin": {"durum": "masaustu"}, "mask": {"durum": "masaustu"},
    "uyap": {"durum": "masaustu"}, "donna": {"durum": "hazir"},
}
# The directory profile has no tkgm_ tools, so the desk shows land registry as
# available in the ArthurLegal install instead of handing Claude a parcel request.
DIRECTORY_MODULES = dict(REMOTE_MODULES, tapu={"durum": "masaustu"})

_resources: List[Resource] = []


def _attach_views() -> None:
    """Give carded tools their _meta, add al_masasi and register the views.

    A tool whose backend failed to load is simply absent, so it gets no card; the
    desk then reports that source as unavailable instead of pointing at nothing.
    """
    if al_arayuz is None:
        return
    for tool in _tools:
        meta = al_arayuz.arac_meta(tool.name)
        if meta:
            tool.meta = meta
    tanim = al_arayuz.masa_araci()
    able = [t.name for t in _tools if t.name in MASA_TOOLS]
    _tools.append(Tool(
        tanim["name"], tanim["description"], tanim["inputSchema"],
        lambda args: al_arayuz.masa_cevabi("uzak", able, REMOTE_MODULES, args.get("bolum")),
        meta=al_arayuz.arac_meta("al_masasi")))
    for k in al_arayuz.kaynaklar():
        _resources.append(Resource(k["uri"], k["name"], k["text"], mime_type=k["mimeType"],
                                   description=k["description"], meta=k["_meta"]))


INSTRUCTIONS_HEADER = """MADDE ATFI KURALI: Bir kanun maddesini (not, sözleşme, protokol, dilekçe veya karar gövdesi dâhil) yazmadan önce tr_mevzuat_madde_getir(number="6769", madde_no="120") ile metnini ve başlığını çekin; yanıttaki citation alanını birebir kullanın ve maddeye yüklediğiniz içeriğin (hakkın sahibi, şart, süre, sonuç) başlık ve metinle örtüştüğünü kontrol edin. Çekilemeyen maddeyi ezberden yazmayın; açıkça "UYARI: veri çekilemedi, teyidiniz gerekli: https://www.mevzuat.gov.tr/" yazın.

CANLI VERİ: Araç veri getiremezse (hata, `unavailable`, `upstream_blocked`, onay yok) hafızadan doldurmayın; aynı UYARI satırını aracın `source_url`'si ya da kaynağın resmî giriş sayfasıyla yazın; bağlantı uydurulmaz, "[doğrulayın]" etiketi kullanılmaz.

ArthurLegal — 15 yargı çevresi tek uçta.

ARAÇ ÖNEKLERİ. Her araç ait olduğu yargı çevresinin önekini taşır:
`tr_` Türkiye (içtihat, mevzuat, EPDK/Rekabet/SPK/BDDK/KVKK/BTK/GİB/Sigorta Tahkim,
Resmî Gazete, semantik arşiv; KİK/Sayıştay/TÜRKPATENT/İSTAÇ YOK — resmi uçları cevap vermiyor) · `nl_` Hollanda · `pl_` Polonya ·
`at_` Avusturya · `ie_` İrlanda · `fi_` Finlandiya · `es_` İspanya · `uk_` Birleşik Krallık ·
`eu_` AB (CELLAR) · `jp_` Japonya · `az_` Azerbaycan · `de_` Almanya ·
`gleif_` tüzel kişi kimliği (LEI) · `scholar_` doktrin · `contracts_` sözleşme emsali ·
`tkgm_` tapu-kadastro: parseli TKGM Parsel Sorgu'dan canlı getirir (il/ilçe/mahalle + ada/parsel,
koordinat ya da yer adı; dosya İSTEMEYİN), rapor, ölçü, kroki; ilk canlı çağrıda onay kartı döner,
önce `tkgm_baslangic`.

TÜRKİYE İÇİN GİRİŞ NOKTASI: karmaşık Türk hukuku sorusunda önce `tr_hukuk_arastirma_rehberi`
(hangi soru için hangi araç), sonra `tr_kurum_listesi` (8 kurumun filtreleri).

Bu kozmetik değil: `get_act` alttaki sunucularda altı ayrı şeydir; önek, İspanyol
sorusunun Fin mevzuatıyla cevaplanmasını engeller.

DURUM. Sonuçlar ince görünürse **önce `status`** (yüklenen yargı çevreleri, indeks, taranan
aralık, semantik arama): yüklenememiş bir yargı çevresi ile gerçekten boş bir sonuç kümesi
farklı şeylerdir.

Aşağıda her yargı çevresinin kendi kuralları var. Bunlar tavsiye değil, o
hukukun doğru alıntılanması için gereken disiplinlerdir.
"""


# Directory profile: describes what the tools return, in English, and gives no
# orders to Claude (the directory rejects instructions that tell Claude how to
# behave). The facts are the same as in INSTRUCTIONS_HEADER.
DIRECTORY_INSTRUCTIONS_HEADER = """ArthurLegal MCP: read-only legal research across twelve jurisdictions, plus legal-entity identity (LEI), open-access legal scholarship and published resource contracts, behind one endpoint.

TOOL PREFIXES. Each tool name starts with the jurisdiction or source it answers for:
`tr_` Türkiye (case law, legislation, Constitutional Court, Official Gazette, eight regulators, a local semantic archive) · `nl_` Netherlands · `pl_` Poland · `at_` Austria · `ie_` Ireland · `fi_` Finland · `es_` Spain · `uk_` United Kingdom · `eu_` EU law (CELLAR) · `jp_` Japan · `az_` Azerbaijan · `de_` Germany · `gleif_` legal-entity identity (LEI) · `scholar_` open-access legal scholarship · `contracts_` petroleum and mining contracts (ResourceContracts.org).
The prefix is part of the meaning: `get_act` is a different operation in different jurisdictions.

CITATIONS. Results carry a `citation` field built from the official record, and a `source_url` where the source provides one; both are meant to be quoted as returned. For a Turkish statutory article, `tr_mevzuat_madde_getir` returns the article text, its heading and a ready citation in one call.

UNAVAILABLE SOURCES. When an official source cannot be reached, a tool returns an error or a result marked `unavailable` / `upstream_blocked`, together with the official source page. That means the answer is missing, not that no such law or decision exists. `status` shows which jurisdictions are loaded and how much each has indexed.

INTERACTIVE VIEWS. In clients that render MCP Apps, `tr_mevzuat_madde_getir` shows its result as an article card, `tr_ictihat_ara` as a list of decisions, and `al_masasi` opens the ArthurLegal desk (Turkish interface: legislation, case law and regulator decisions in one panel). Other clients receive the same data as text.

ENTRY POINTS. `tr_hukuk_arastirma_rehberi` maps Turkish research questions to the right tools; `tr_kurum_listesi` lists the regulators and their filters.

Each section below describes one jurisdiction's sources and citation conventions."""


def _directory_excluded(name: str) -> bool:
    return any(name.startswith(p + "_") for p in DIRECTORY_EXCLUDED_PREFIXES)


def _directory_section(prefix: str, text: str) -> str:
    # The Turkish section opens with the citation rule as an order to Claude;
    # the directory header states the same fact as a description instead.
    if prefix == "tr":
        text = re.sub(r"(?s)^(### [^\n]*\n)MADDE ATFI KURALI:.*?\n\n", r"\1", text, count=1)
    return text


def build_directory() -> Tuple[List[Tool], str, List[Resource]]:
    """Tools, instructions and views for DIRECTORY_PATH. Call after build()."""
    tools = [t for t in _tools
             if t.name not in ("status", "al_masasi") and not _directory_excluded(t.name)]
    main_status = next(t for t in _tools if t.name == "status")
    status = Tool(
        "status", main_status.description, main_status.input_schema,
        lambda args: _status(DIRECTORY_EXCLUDED_PREFIXES, exposed=len(tools)),
    )
    annotate(status)
    tools.append(status)
    desk = next((t for t in _tools if t.name == "al_masasi"), None)
    if desk is not None:
        able = [t.name for t in tools if t.name in MASA_TOOLS]
        directory_desk = Tool(
            desk.name, desk.description, desk.input_schema,
            lambda args: al_arayuz.masa_cevabi("uzak", able, DIRECTORY_MODULES, args.get("bolum")),
            meta=desk.meta)
        annotate(directory_desk)
        tools.append(directory_desk)
    sections = [_directory_section(prefix, text) for prefix, text in _instruction_parts
                if prefix not in DIRECTORY_EXCLUDED_PREFIXES]
    return tools, DIRECTORY_INSTRUCTIONS_HEADER + "\n\n" + "\n\n".join(sections), list(_resources)


if __name__ == "__main__":
    build()
    _warm_resident()
    text = INSTRUCTIONS_HEADER + "\n\n" + "\n\n".join(_instructions)
    directory_tools, directory_text, directory_views = build_directory()
    sys.stderr.write("arthurlegal-mcp: %d backend, %d araç (%s: %d)"
                     % (len(_loaded), len(_tools), DIRECTORY_PATH, len(directory_tools)))
    if _failed:
        sys.stderr.write(" (%d backend yüklenemedi: %s)"
                         % (len(_failed), ", ".join(f["prefix"] for f in _failed)))
    sys.stderr.write("\n")
    directory = Dispatcher(directory_tools, name="arthurlegal-mcp", version=__version__,
                           instructions=directory_text, resources=directory_views)
    routes = {DIRECTORY_PATH: directory}
    # Denetim logu yalnız ayrı test uygulamasında açılır (README: "Denetim logu"). AUDIT_LOG=1
    # değilken bu blok hiç çalışmaz: araçlar sarılmaz, oturum kimliği verilmez, yanıtlar aynı
    # kalır. Canlı uygulamada AUDIT_LOG=1 yok sayılır. Açıkken dizin yolu sunulmaz: oradaki
    # araçlar sarılmaz, çağrıları kayda girmezdi.
    if os.environ.get("AUDIT_LOG") == "1":
        import denetim
        if denetim.canli_uygulamada():
            sys.stderr.write("arthurlegal-mcp: AUDIT_LOG=1 canlı uygulamada yok sayıldı "
                             "(FLY_APP_NAME=%s); denetim logu KAPALI\n" % denetim.CANLI_UYGULAMA)
        else:
            _tools[:] = denetim.etkinlestir(_tools, denetim.arka_uc_surumleri(_loaded), __version__)
            routes = {}
            sys.stderr.write("arthurlegal-mcp: denetim logu AÇIK (AUDIT_LOG=1)\n")
            sys.stderr.write("arthurlegal-mcp: %s sunulmuyor (denetim logu açıkken yalnız /mcp)\n"
                             % DIRECTORY_PATH)
    run(_tools, name="arthurlegal-mcp", version=__version__, instructions=text,
        resources=_resources, routes=routes)
