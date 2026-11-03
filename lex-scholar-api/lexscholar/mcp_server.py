"""Dependency-free MCP server exposing the federation through ONE endpoint.

Same stdlib JSON-RPC implementation as the sibling ``eqanun`` and
``resourcecontracts`` servers, so it runs on a stock Python 3.9 with no pip
installs and no ``mcp`` SDK.

The design point: nine upstream indexes, **one** ``/mcp`` endpoint and one
connector. ``source`` is a tool parameter, not another deployment — adding a
tenth index later changes nothing for the agent or the Copilot Studio connector.

Transports: stdio (desktop clients) and Streamable HTTP at ``/mcp`` (remote
connectors), no authentication.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

from ._http import LexScholarError
from .client import LexScholarClient
from .retrieval import embeddings_status, semantic_rerank

SERVER_NAME = "lex-scholar-api"
SERVER_VERSION = "0.1.0"

_SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
_DEFAULT_PROTOCOL = "2025-06-18"

_client = LexScholarClient()


# --------------------------------------------------------------------------- #
# Tools                                                                        #
# --------------------------------------------------------------------------- #
# Nine indexes, nine relevance models, one merged list. Each source ranks
# sensibly against its own corpus, but "rank 1 in DOAJ" and "rank 1 in OpenAlex"
# are not comparable, so the merged head is close to arbitrary. Pulling a wider
# pool and scoring every candidate against the query with one model is what
# makes the federated list coherent.
_POOL_MULTIPLIER = 3
_POOL_MAX = 120


def _t_search_articles(a: Dict[str, Any]) -> Any:
    limit = int(a.get("limit", 20))
    rank = bool(a.get("rerank", True))
    pool = min(max(limit * _POOL_MULTIPLIER, limit), _POOL_MAX) if rank else limit

    out = _client.search(
        a.get("query", ""),
        source=a.get("source", "auto"),
        jurisdiction=a.get("jurisdiction"),
        language=a.get("language"),
        year_from=a.get("year_from"),
        year_to=a.get("year_to"),
        peer_reviewed_only=bool(a.get("peer_reviewed_only", False)),
        open_access_only=bool(a.get("open_access_only", False)),
        full_text_only=bool(a.get("full_text_only", False)),
        limit=pool,
        max_sources=a.get("max_sources"),
    )
    if not rank or not isinstance(out, dict):
        return out

    ranked = semantic_rerank(
        a.get("query", ""),
        out.get("results") or [],
        fields=("title", "abstract", "journal"),
        limit=limit,
    )
    out["results"] = ranked["results"]
    out["returned"] = len(ranked["results"])
    ranking = {"method": ranked["method"], "candidates_considered": pool,
               "note": ranked.get("note") or ranked.get("warning")}
    if "model" in ranked:
        ranking["model"] = ranked["model"]
    out["ranking"] = ranking
    out["ranking_warning"] = (
        "Results were reordered locally across all sources. Each index ranks "
        "only against its own corpus, so the raw federated order is not "
        "comparable between sources. Pass rerank=false for the unmerged order."
    )
    return out


def _t_compare_jurisdictions(a: Dict[str, Any]) -> Any:
    return _client.compare_jurisdictions(
        a["query"],
        list(a.get("jurisdictions") or []),
        limit_per=int(a.get("limit_per", 5)),
        peer_reviewed_only=bool(a.get("peer_reviewed_only", False)),
        year_from=a.get("year_from"),
        year_to=a.get("year_to"),
    )


def _t_get_article(a: Dict[str, Any]) -> Any:
    # NB: the MCP *tool* is exposed as get_scholarship_article (renamed to avoid
    # colliding with DergiPark's tools in a multi-MCP agent), but the client
    # method it calls is still LexScholarClient.get_article.
    extra = {k: a[k] for k in ("collection",) if a.get(k)}
    return _client.get_article(a["source"], a["id"], **extra)


def _t_get_article_fulltext(a: Dict[str, Any]) -> Any:
    extra = {k: a[k] for k in ("collection",) if a.get(k)}
    return _client.get_fulltext(
        a["source"], a["id"],
        offset=int(a.get("offset", 0)),
        max_chars=int(a.get("max_chars", 20000)),
        **extra,
    )


def _t_resolve_doi(a: Dict[str, Any]) -> Any:
    return _client.resolve_doi(a["doi"])


def _t_list_sources(a: Dict[str, Any]) -> Any:
    return {"sources": _client.list_sources()}


_STR = {"type": "string"}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean", "default": False}

_FILTERS = {
    "jurisdiction": {"type": "string",
                     "description": "ISO alpha-2 country code, e.g. FR, BR, ID, TR, AZ. "
                                    "Routes to that country's native index."},
    "language": {"type": "string", "description": "ISO language code, e.g. es, fr, pt, tr."},
    "year_from": _INT,
    "year_to": _INT,
    "peer_reviewed_only": {"type": "boolean", "default": False,
                           "description": "Keep only sources/records that guarantee peer review. "
                                          "Excludes preprints and US student-edited law reviews."},
    "open_access_only": _BOOL,
}


def _t_server_status(args: Dict[str, Any]) -> Any:
    """What this server is, and whether semantic ranking is actually live."""
    return {
        "server": "lex-scholar-api",
        "source": "nine open-access legal scholarship indexes - public, no auth",
        "mode": "passthrough + local reranking (no local corpus)",
        "known_upstream_quirks": [
            'Nine indexes each rank against their own corpus, so the merged order is not comparable between sources. Results are rescored locally with one model; see `ranking.method`.',
            "Query in the language of the target jurisdiction - these indexes match the article's own words.",
            'Scholarship is a SECONDARY source; never cite it as the law.',
        ],
        **embeddings_status(),
    }


TOOLS: List[Dict[str, Any]] = [
    {
        "name": "search_legal_scholarship",
        "description": (
            "Search open-access legal scholarship across nine indexes through one "
            "endpoint. A deterministic router picks the 2-3 sources that fit the "
            "question (language, jurisdiction, topic) instead of querying all of "
            "them, and the response always reports sources_queried, "
            "sources_skipped (with reasons) and routing_reasons. Results are "
            "normalized, deduplicated across sources and re-ranked. Use "
            "peer_reviewed_only=true when the answer must rest on peer-reviewed "
            "work. Set source to a specific index (doaj, hal, scielo, dialnet, "
            "openaire, lawreviewcommons, openalex) or 'all' to override routing. "
            "IMPORTANT: query in the LANGUAGE OF THE TARGET JURISDICTION — these "
            "indexes match the article's own words, so 'force majeure' returns "
            "nothing from Brazil while 'forca maior' or 'caso fortuito' does. For "
            "a non-English jurisdiction, search the local legal term (and repeat "
            "in English only if you also want anglophone commentary)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free text. Quote a phrase to force it."},
                "source": {"type": "string", "default": "auto",
                           "description": "auto (default) | all | a source name | comma-separated list"},
                "full_text_only": {"type": "boolean", "default": False,
                                   "description": "Only sources that can return body text (SciELO, HAL)."},
                "limit": {"type": "integer", "default": 20},
                "rerank": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Reorder results by relevance before returning them. "
                        "Semantic when an embeddings backend is configured, BM25 "
                        "otherwise; `ranking.method` in the response says which ran. "
                        "Set false to inspect the raw upstream order."
                    ),
                },
                "max_sources": {"type": "integer", "description": "Cap how many indexes are queried."},
                **_FILTERS,
            },
        },
        "handler": _t_search_articles,
    },
    {
        "name": "compare_jurisdictions",
        "description": (
            "Run one legal question across several jurisdictions at once and get "
            "the answers grouped by country rather than merged. Each jurisdiction "
            "is routed to its own native index (Brazil to SciELO, France to HAL, "
            "Spain to Dialnet, Indonesia and Poland to DOAJ, Azerbaijan to "
            "OpenAlex), which is what makes comparative work possible in one call. "
            "Reports which jurisdictions returned nothing instead of hiding them. "
            "IMPORTANT: one English query will under-report non-anglophone "
            "jurisdictions, because each index matches the article's own language "
            "('force majeure' finds nothing in Brazil; 'forca maior' does). For a "
            "fair comparison, run this once per language group using the local "
            "legal term, or treat an empty group as 'not found in this language', "
            "never as 'no scholarship exists'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": _STR,
                "jurisdictions": {"type": "array", "items": {"type": "string"}, "maxItems": 12,
                                  "description": "ISO alpha-2 codes, e.g. ['FR','BR','ID','TR']; at most 12"},
                "limit_per": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
                "peer_reviewed_only": {"type": "boolean", "default": False},
                "year_from": _INT,
                "year_to": _INT,
            },
            "required": ["query", "jurisdictions"],
        },
        "handler": _t_compare_jurisdictions,
    },
    {
        "name": "get_scholarship_article",
        "description": "Full normalized metadata for one article, by source and id "
                       "(ids come from search_legal_scholarship results).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": _STR, "id": _STR,
                "collection": {"type": "string", "description": "SciELO only, e.g. scl, chl, mex."},
            },
            "required": ["source", "id"],
        },
        "handler": _t_get_article,
    },
    {
        "name": "get_scholarship_fulltext",
        "description": (
            "Return an article's body text as clean plain text, paginated by "
            "characters (offset + max_chars). SciELO serves real full text; other "
            "sources may only expose a PDF link, in which case the abstract plus "
            "pdf_url/landing_url are returned and 'note' says so. Never claims "
            "text it could not fetch."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": _STR, "id": _STR,
                "collection": _STR,
                "offset": {"type": "integer", "default": 0},
                "max_chars": {"type": "integer", "default": 20000},
            },
            "required": ["source", "id"],
        },
        "handler": _t_get_article_fulltext,
    },
    {
        "name": "resolve_doi",
        "description": "Resolve a DOI to authoritative metadata (Crossref) plus its "
                       "best legally-free copy (Unpaywall).",
        "inputSchema": {"type": "object", "properties": {"doi": _STR}, "required": ["doi"]},
        "handler": _t_resolve_doi,
    },
    {
        "name": "list_sources",
        "description": "Capability card for every index: auth model, whether peer review "
                       "is guaranteed, full-text support, jurisdictions, volume, cost, and "
                       "the live OpenAlex rate-limit budget.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _t_list_sources,
    },
    {
        "name": "server_status",
        "description": (
            "What this server talks to, whether semantic ranking is "
            "currently live, and the upstream quirks worth defending "
            "against. Call it when results look wrong, to tell a degraded "
            "ranking channel apart from a genuinely empty result set."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _t_server_status,
    },
]

_TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}

# Pre-rename tool names. Still accepted on tools/call, deliberately NOT
# advertised in tools/list.
#
# A client that registered this server before the rename keeps calling the old
# name, and nothing here can invalidate that cache: Copilot Studio stores the
# tool name when the tool is added and only re-reads it if the tool is removed
# and re-added. Answering the old name keeps such a client working, while
# tools/list stays clean so nothing new registers a name that collides with
# DergiPark's search_articles.
_ALIASES = {
    "search_articles": "search_legal_scholarship",
    "get_article": "get_scholarship_article",
    "get_article_fulltext": "get_scholarship_fulltext",
}


def _resolve_tool(name: Any) -> Optional[Dict[str, Any]]:
    """Look a tool up by its current name, then by a pre-rename alias."""
    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        tool = _TOOLS_BY_NAME.get(_ALIASES.get(name, ""))
    return tool


def _public_tools() -> List[Dict[str, Any]]:
    return [{"name": t["name"], "description": t["description"],
             "inputSchema": t["inputSchema"]} for t in TOOLS]


# --------------------------------------------------------------------------- #
# JSON-RPC dispatch                                                            #
# --------------------------------------------------------------------------- #
def _ok(msg_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _negotiate(requested: Optional[str]) -> str:
    return requested if requested in _SUPPORTED_PROTOCOLS else _DEFAULT_PROTOCOL


def dispatch(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle one JSON-RPC message; None for notifications."""
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}
    is_notification = "id" not in message

    if method == "initialize":
        return _ok(msg_id, {
            "protocolVersion": _negotiate(params.get("protocolVersion")),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _ok(msg_id, {})
    if method == "tools/list":
        return _ok(msg_id, {"tools": _public_tools()})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = _resolve_tool(name)
        if tool is None:
            return _err(msg_id, -32602, "unknown tool: %s" % name)
        try:
            result = tool["handler"](args)
            text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
            return _ok(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except (LexScholarError, ValueError, KeyError, TypeError) as exc:
            return _ok(msg_id, {"content": [{"type": "text", "text": "error: %s" % exc}],
                                "isError": True})
    if is_notification:
        return None
    return _err(msg_id, -32601, "method not found: %s" % method)


def _handle_payload(payload: Any) -> Tuple[Optional[Any], bool]:
    if isinstance(payload, list):
        responses = [r for r in (dispatch(m) for m in payload) if r is not None]
        return (responses or None), bool(responses)
    resp = dispatch(payload)
    return resp, resp is not None


# --------------------------------------------------------------------------- #
# stdio transport                                                              #
# --------------------------------------------------------------------------- #
def run_stdio() -> None:
    """Serve MCP over line-delimited JSON-RPC on stdin/stdout."""
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            out.write(json.dumps(_err(None, -32700, "parse error")) + "\n")
            out.flush()
            continue
        resp, _ = _handle_payload(payload)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False, default=str) + "\n")
            out.flush()


# --------------------------------------------------------------------------- #
# Streamable HTTP transport (no auth)                                          #
# --------------------------------------------------------------------------- #
_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers":
        "Content-Type, Mcp-Session-Id, MCP-Protocol-Version, Authorization, Accept",
    "Access-Control-Expose-Headers": "Mcp-Session-Id",
}


class _MCPHandler(BaseHTTPRequestHandler):
    server_version = "%s/%s" % (SERVER_NAME, SERVER_VERSION)
    endpoint = "/mcp"

    def _send(self, status: int, body: Optional[bytes] = None,
              content_type: str = "application/json",
              extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        for k, v in _CORS.items():
            self.send_header(k, v)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        if body is not None:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body is not None:
            self.wfile.write(body)

    def _send_sse(self, obj: Any, extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(200)
        for k, v in _CORS.items():
            self.send_header(k, v)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False, default=str) +
                          "\n\n").encode("utf-8"))
        self.wfile.flush()

    def _path_ok(self) -> bool:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        return path in (self.endpoint, "/")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204)

    def do_GET(self) -> None:  # noqa: N802
        self._send(405, b'{"error":"method not allowed; use POST"}')

    def do_DELETE(self) -> None:  # noqa: N802
        self._send(204)

    def do_POST(self) -> None:  # noqa: N802
        if not self._path_ok():
            self._send(404, b'{"error":"not found; POST to /mcp"}')
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            self._send(400, json.dumps(_err(None, -32700, "parse error")).encode("utf-8"))
            return

        extra: Dict[str, str] = {}
        if isinstance(payload, dict) and payload.get("method") == "initialize":
            extra["Mcp-Session-Id"] = os.urandom(16).hex()

        resp, had_requests = _handle_payload(payload)
        if not had_requests:
            self._send(202, extra=extra or None)
            return
        if "text/event-stream" in self.headers.get("Accept", ""):
            self._send_sse(resp, extra=extra or None)
        else:
            self._send(200, json.dumps(resp, ensure_ascii=False, default=str).encode("utf-8"),
                       extra=extra or None)

    def log_message(self, fmt: str, *args: Any) -> None:
        pass


class _SingleBindHTTPServer(ThreadingHTTPServer):
    """Refuse to start when the port is already served.

    ``HTTPServer`` sets ``allow_reuse_address = 1``. On Windows that lets a
    SECOND process bind a port another server is already listening on, and
    connections keep going to the first one — so a restarted server silently
    serves stale code while looking healthy. Turning it off makes the second
    start fail loudly with "address already in use", which is the honest answer.
    """

    allow_reuse_address = False


def run_http(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Serve MCP over Streamable HTTP (no auth) at http://host:port/mcp."""
    httpd = _SingleBindHTTPServer((host, port), _MCPHandler)
    sys.stderr.write("%s MCP (Streamable HTTP, no auth) on http://%s:%d/mcp\n"
                     % (SERVER_NAME, host, port))
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
