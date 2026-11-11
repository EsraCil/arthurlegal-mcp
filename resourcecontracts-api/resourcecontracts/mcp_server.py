"""Dependency-free MCP server for the ResourceContracts.org API.

Implements the Model Context Protocol (JSON-RPC 2.0) over two transports using
only the Python standard library, so it runs on a stock Python 3.9 with no pip
installs — unlike the official ``mcp`` SDK, which requires Python 3.10+ (mirrors
the ``eqanun`` MCP server).

- **stdio**: line-delimited JSON-RPC on stdin/stdout (local desktop clients).
- **Streamable HTTP**: a single ``/mcp`` endpoint (POST) for remote clients /
  UI connectors (e.g. Copilot Studio). No authentication.

Supported methods: ``initialize``, ``notifications/initialized``, ``ping``,
``tools/list``, ``tools/call``. Tools wrap ``resourcecontracts.ResourceContractsClient``.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

from .client import ResourceContractsClient, ResourceContractsError
from .retrieval import embeddings_status, semantic_rerank

SERVER_NAME = "resourcecontracts-api"
SERVER_VERSION = "0.1.0"

# Protocol revisions we can speak; we echo the client's if recognised.
_SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
_DEFAULT_PROTOCOL = "2025-06-18"

_client = ResourceContractsClient()


# --------------------------------------------------------------------------- #
# Tool definitions: JSON Schema + handler. One source of truth for both        #
# transports.                                                                   #
# --------------------------------------------------------------------------- #
_SEARCH_FILTER_KEYS = (
    "country", "resource", "year", "contract_type", "document_type",
    "language", "company_name", "corporate_group", "annotation_category",
)


def _search_kwargs(args: Dict[str, Any]) -> Dict[str, Any]:
    kw = {k: args.get(k) for k in _SEARCH_FILTER_KEYS}
    if "annotated" in args:
        kw["annotated"] = bool(args.get("annotated"))
    if "sortby" in args:
        kw["sortby"] = args.get("sortby")
        kw["order"] = args.get("order", "desc")
    return kw


# Contract work is the case semantic ranking was made for. A lawyer looking for
# a stabilisation clause or a change-of-control trigger is describing a CONCEPT;
# the contract that contains it may use none of those words in its name, and the
# upstream index scores mostly on the title and party names.
_POOL_MULTIPLIER = 4
_POOL_MAX = 120
# A small page still reranks a real pool. For a one-word query the upstream gives every hit the same
# score (2026-11-10, "mining": 5,126 hits, all 0.45166105), so its order says nothing; per_page=3 left
# 12 candidates, none of them a mining contract (PSAs, timber concessions). The first ones in its order
# sit at positions 24-31.
_POOL_MIN = 40


def _t_search_contracts(args: Dict[str, Any]) -> Any:
    per_page = int(args.get("per_page", 20))
    # An empty query browses: nothing to rerank, so the page is the page (a wider pool came back whole).
    rank = bool(args.get("rerank", True)) and bool((args.get("query") or "").strip())
    pool = min(max(per_page * _POOL_MULTIPLIER, per_page, _POOL_MIN), _POOL_MAX) if rank else per_page

    out = _client.search(
        args.get("query", ""),
        from_=int(args.get("from", 0)),
        per_page=pool,
        group=args.get("group", "metadata"),
        **_search_kwargs(args),
    )
    if not rank or not isinstance(out, dict):
        return out

    ranked = semantic_rerank(
        args.get("query", ""),
        out.get("results") or [],
        fields=("name", "resource", "contract_type", "document_type"),
        limit=per_page,
    )
    out["results"] = ranked["results"]
    out["per_page"] = len(ranked["results"])
    ranking = {"method": ranked["method"], "candidates_considered": pool,
               "note": ranked.get("note") or ranked.get("warning")}
    if "model" in ranked:
        ranking["model"] = ranked["model"]
    out["ranking"] = ranking
    out["ranking_warning"] = (
        "Reordered locally by relevance to the query. `total` is still the "
        "upstream count. Ranking uses contract metadata (name, resource, type) "
        "-- not clause text; use get_contract_text or get_contract_annotations "
        "to check whether a specific clause is actually present."
    )
    return out


def _t_count_contracts(args: Dict[str, Any]) -> Any:
    return {"total": _client.count(args.get("query", ""), **_search_kwargs(args))}


def _t_get_contract_metadata(args: Dict[str, Any]) -> Any:
    return _client.get_metadata(args["contract_id"])


def _t_get_contract_text(args: Dict[str, Any]) -> Any:
    cid = int(args["contract_id"])
    start_page = int(args.get("start_page", 1))
    page_count = int(args.get("page_count", 10))
    total_pages = _client.get_page_count(cid)
    text = _client.get_fulltext(cid, start_page=start_page, page_count=page_count)
    end = start_page + page_count
    return {
        "contract_id": cid,
        "start_page": start_page,
        "page_count": page_count,
        "total_pages": total_pages or None,
        "next_page": end if (total_pages and end <= total_pages) else None,
        "returned_chars": len(text),
        "text": text,
    }


def _t_get_contract_annotations(args: Dict[str, Any]) -> Any:
    page = args.get("page")
    return _client.get_annotations(
        args["contract_id"], page=int(page) if page is not None else None
    )


def _t_list_countries(args: Dict[str, Any]) -> Any:
    return _client.list_countries()


def _t_list_resources(args: Dict[str, Any]) -> Any:
    return _client.list_resources()


def _t_list_years(args: Dict[str, Any]) -> Any:
    return _client.list_years()


def _t_list_annotation_categories(args: Dict[str, Any]) -> Any:
    return _client.list_annotation_categories()


_STR = {"type": "string"}
_INT = {"type": "integer"}

_SEARCH_PROPS = {
    "query": {"type": "string", "description": "Free-text query; empty browses all."},
    "country": {"type": "string", "description": "ISO alpha-2 country code, e.g. 'az'."},
    "resource": {"type": "string", "description": "Resource name, e.g. 'Hydrocarbons', 'Gold'."},
    "year": {"type": "integer", "description": "Contract signature year."},
    "contract_type": {"type": "string", "description": "e.g. 'Production or Profit Sharing Agreement'."},
    "document_type": {"type": "string", "description": "e.g. 'Company-State Contract'."},
    "language": {"type": "string", "description": "Contract language code, e.g. 'en'."},
    "company_name": _STR,
    "corporate_group": _STR,
    "annotation_category": {"type": "string", "description": "Restrict to an annotated category."},
    "annotated": {"type": "boolean", "description": "Only contracts that have annotations."},
    "sortby": {"type": "string", "description": "Best-effort sort field, e.g. 'year'."},
    "order": {"type": "string", "enum": ["asc", "desc"], "default": "desc"},
}


def _t_server_status(args: Dict[str, Any]) -> Any:
    """What this server is, and whether semantic ranking is actually live."""
    return {
        "server": "resourcecontracts-api",
        "source": "ResourceContracts.org (NRGI/CCSI) - public, no auth, CC BY-SA 4.0",
        "mode": "passthrough + local reranking (no local corpus)",
        "known_upstream_quirks": [
            'Ranking uses contract METADATA (name, resource, type), not clause text. Use get_contract_text or get_contract_annotations to confirm a clause is actually present.',
            'Content is CC BY-SA 4.0 - attribution and share-alike are required.',
        ],
        **embeddings_status(),
    }


TOOLS: List[Dict[str, Any]] = [
    {
        "name": "search_contracts",
        "description": (
            "Search the ResourceContracts.org repository of petroleum and mining "
            "contracts. Combine a free-text query with filters (country, resource, "
            "year, contract_type, document_type, language, company_name, "
            "corporate_group, annotation_category, annotated). Returns total, "
            "country/year/resource facets (distinct values among the fetched "
            "candidates, not counts), and results (id, name, year_signed, "
            "contract_type, resource, countries, language). Use id with "
            "get_contract_metadata / get_contract_text / get_contract_annotations."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **_SEARCH_PROPS,
                "from": {"type": "integer", "default": 0, "description": "Pagination offset."},
                "per_page": {"type": "integer", "default": 20},
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
                "group": {"type": "string", "default": "metadata",
                          "description": "Sub-docs to embed: metadata|text|annotations."},
            },
        },
        "handler": _t_search_contracts,
    },
    {
        "name": "count_contracts",
        "description": "Return only the number of contracts matching a query/filters (cheap).",
        "inputSchema": {"type": "object", "properties": dict(_SEARCH_PROPS)},
        "handler": _t_count_contracts,
    },
    {
        "name": "get_contract_metadata",
        "description": (
            "Full structured metadata for one contract by id: name, countries, "
            "resource, contract_type, document_type, year_signed, number_of_pages, "
            "government_entity, participation (companies + shares), concession, "
            "source_url."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"contract_id": _INT},
            "required": ["contract_id"],
        },
        "handler": _t_get_contract_metadata,
    },
    {
        "name": "get_contract_text",
        "description": (
            "Return a contract's OCR/text as clean plain text, a window of PDF "
            "pages at a time (start_page + page_count; one PDF page per source "
            "request). Response reports total_pages and next_page (null at end). "
            "For targeted clause lookup prefer get_contract_annotations; use this "
            "to read passages. Some contracts are scanned and may carry OCR artefacts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contract_id": _INT,
                "start_page": {"type": "integer", "default": 1, "description": "First PDF page (1-based)."},
                "page_count": {"type": "integer", "default": 10, "description": "How many pages to return."},
            },
            "required": ["contract_id"],
        },
        "handler": _t_get_contract_text,
    },
    {
        "name": "get_contract_annotations",
        "description": (
            "Return the expert-curated key-clause annotations for a contract "
            "(category, category_key, article_reference, page_no, text, quote). "
            "These are the platform's extractions of key terms — arbitration, "
            "governing law, stabilization, term, environmental / local-content "
            "obligations — the highest-value signal for legal review."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contract_id": _INT,
                "page": {"type": "integer",
                         "description": "Optional: only annotations on this PDF page. Omit for ALL annotations."},
            },
            "required": ["contract_id"],
        },
        "handler": _t_get_contract_annotations,
    },
    {
        "name": "list_countries",
        "description": "List countries (ISO alpha-2 code) with contract counts.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _t_list_countries,
    },
    {
        "name": "list_resources",
        "description": "List resources/commodities (e.g. Hydrocarbons, Gold, Copper) with counts.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _t_list_resources,
    },
    {
        "name": "list_years",
        "description": "List contract signature years with counts.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _t_list_years,
    },
    {
        "name": "list_annotation_categories",
        "description": "List the annotation category taxonomy (key clause types).",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _t_list_annotation_categories,
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


def _public_tools() -> List[Dict[str, Any]]:
    return [
        {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
        for t in TOOLS
    ]


# --------------------------------------------------------------------------- #
# JSON-RPC dispatch                                                            #
# --------------------------------------------------------------------------- #
def _ok(msg_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _negotiate_protocol(requested: Optional[str]) -> str:
    if requested in _SUPPORTED_PROTOCOLS:
        return requested
    return _DEFAULT_PROTOCOL


def dispatch(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle one JSON-RPC message. Returns a response dict, or None for
    notifications (no id)."""
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}
    is_notification = "id" not in message

    if method == "initialize":
        proto = _negotiate_protocol(params.get("protocolVersion"))
        return _ok(msg_id, {
            "protocolVersion": proto,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method in ("notifications/initialized", "initialized"):
        return None  # notification, no response

    if method == "ping":
        return _ok(msg_id, {})

    if method == "tools/list":
        return _ok(msg_id, {"tools": _public_tools()})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        tool = _TOOLS_BY_NAME.get(name)
        if tool is None:
            return _err(msg_id, -32602, f"unknown tool: {name}")
        try:
            result = tool["handler"](arguments)
            text = json.dumps(result, ensure_ascii=False, indent=2)
            return _ok(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except (ResourceContractsError, ValueError, KeyError, TypeError) as exc:
            return _ok(msg_id, {
                "content": [{"type": "text", "text": f"error: {exc}"}],
                "isError": True,
            })

    if is_notification:
        return None
    return _err(msg_id, -32601, f"method not found: {method}")


def _handle_payload(payload: Any) -> Tuple[Optional[Any], bool]:
    """Process a parsed JSON-RPC payload (single or batch).

    Returns (response_or_None, had_requests). ``response`` is a dict for a
    single message, a list for a batch, or None when there were only
    notifications.
    """
    if isinstance(payload, list):
        responses = [r for r in (dispatch(m) for m in payload) if r is not None]
        return (responses or None), bool(responses)
    resp = dispatch(payload)
    return resp, resp is not None


# --------------------------------------------------------------------------- #
# stdio transport                                                             #
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
            out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out.flush()


# --------------------------------------------------------------------------- #
# Streamable HTTP transport (no auth)                                          #
# --------------------------------------------------------------------------- #
_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Mcp-Session-Id, MCP-Protocol-Version, Authorization, Accept",
    "Access-Control-Expose-Headers": "Mcp-Session-Id",
}


class _MCPHandler(BaseHTTPRequestHandler):
    server_version = f"{SERVER_NAME}/{SERVER_VERSION}"
    # The MCP endpoint path (kept small; matched loosely below).
    endpoint = "/mcp"

    def _send(self, status: int, body: Optional[bytes] = None,
              content_type: str = "application/json",
              extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        for k, v in _CORS_HEADERS.items():
            self.send_header(k, v)
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        if body is not None:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body is not None:
            self.wfile.write(body)

    def _send_sse(self, obj: Any, extra: Optional[Dict[str, str]] = None) -> None:
        """Send a single JSON-RPC message as one SSE event, then close.

        Streamable-HTTP clients that prefer text/event-stream (e.g. some
        Copilot Studio / connector clients) get the response this way; both
        this and the application/json path are spec-compliant.
        """
        self.send_response(200)
        for k, v in _CORS_HEADERS.items():
            self.send_header(k, v)
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        data = json.dumps(obj, ensure_ascii=False)
        self.wfile.write(("data: " + data + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def _path_ok(self) -> bool:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        return path in (self.endpoint, "/")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204)

    def do_GET(self) -> None:  # noqa: N802
        # We do not push server-initiated messages; SSE stream not offered.
        self._send(405, b'{"error":"method not allowed; use POST"}')

    def do_DELETE(self) -> None:  # noqa: N802
        # Session termination — accept and succeed.
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

        # New session id on initialize (lenient: not strictly enforced after).
        extra: Dict[str, str] = {}
        is_init = (isinstance(payload, dict) and payload.get("method") == "initialize")
        if is_init:
            extra["Mcp-Session-Id"] = os.urandom(16).hex()

        resp, had_requests = _handle_payload(payload)
        if not had_requests:
            # Only notifications/responses -> 202 Accepted, no body.
            self._send(202, extra=extra or None)
            return
        # Respond as SSE if the client prefers it, else plain JSON. Both are
        # allowed by the Streamable HTTP spec for a POST containing requests.
        if "text/event-stream" in self.headers.get("Accept", ""):
            self._send_sse(resp, extra=extra or None)
        else:
            body = json.dumps(resp, ensure_ascii=False).encode("utf-8")
            self._send(200, body, extra=extra or None)

    def log_message(self, fmt: str, *args: Any) -> None:  # silence default logging
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
    sys.stderr.write(
        f"{SERVER_NAME} MCP (Streamable HTTP, no auth) on "
        f"http://{host}:{port}/mcp\n"
    )
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
