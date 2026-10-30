#!/usr/bin/env python3
"""es-boe-mcp — Spanish legislation (BOE) over MCP. No auth, standard library only.

    python server.py                                   # stdio
    python server.py --transport http --port 8000      # http://127.0.0.1:8000/mcp
    python server.py --transport http --host 0.0.0.0   # exposes an UNAUTHENTICATED server

Build the search index first (~20 requests, under a minute):

    python crawl.py
"""

from __future__ import annotations

import os
from typing import Any, Dict

from boe import BoeClient, BoeError
from mcpcore import McpError, Tool, run
from retrieval import Index, embeddings_status

__version__ = "1.1.0"

_client = BoeClient()
_index = Index()

INSTRUCTIONS = """Spanish legislation from the BOE (Boletín Oficial del Estado).

Call order
1. `search_legislation` to find the act (searches the local index).
2. `get_act` for metadata + status (no text), or `get_act_text` for the
   consolidated text with the same header. The text gives each provision in the
   version in force today; `as_of` gives it on another date. Long acts are
   paged: pass `offset` = `next_offset`.
3. `get_document` only when you deliberately want the text AS PUBLISHED.

Two rules that matter for Spanish law:

STATUS. Every act carries BOE's own signals: `estatus_derogacion`,
`estatus_anulacion` and `vigencia_agotada`. Report them. An act with
`vigencia_agotada: true` or `estatus_derogacion: S` is NOT current law.

CONSOLIDATED vs AS-PUBLISHED. `get_act_text` returns the consolidated text
(amendments applied). `get_document` returns the original publication and does
NOT include later amendments — it is flagged `consolidated: false`. Never cite
an as-published text as the law in force.

CITATIONS. Copy the `citation` field verbatim. Never construct a BOE id or an
official number yourself.

SEARCH SCOPE. The index holds titles, official numbers, issuing departments and
dates, and (when built with crawl.py --backfill-text) the first 8,000 characters
of each act's text in force: the preamble and the opening articles. Not the rest
of the text: a phrase inside article 348 bis will not be found by searching for
it; find the act, then read its text. `index_scope` in each response says which."""


def _scope() -> str:
    """What the index holds, as the crawl recorded it."""
    corpus = _index.get_state("corpus") or ""
    if "first" in corpus:
        return ("act metadata (title, number, department, dates) and the first 8,000 "
                "characters of each act's text in force, not the rest of the text")
    return "act metadata (title, number, department, dates), not article text"


def _t_search(args: Dict[str, Any]) -> Any:
    query = (args.get("query") or "").strip()
    if not query:
        raise McpError("query is required")
    if _index.count() == 0:
        raise McpError(
            "The index is empty — run `python crawl.py` before searching. "
            "get_act / get_act_text / get_document work without an index."
        )
    filters: Dict[str, Any] = {}
    for key in ("date_from", "date_to"):
        if args.get(key):
            filters[key] = args[key]
    if args.get("only_in_force"):
        filters["status"] = "vigente"
    result = _index.search(
        query,
        mode=args.get("mode", "hybrid"),
        limit=int(args.get("limit", 20)),
        filters=filters,
    )
    result["scope_note"] = (
        "Searches %s. Use get_act_text once you have identified the act." % _scope()
    )
    return result


def _t_get_act(args: Dict[str, Any]) -> Any:
    try:
        return _client.get_act(args["boe_id"])
    except (BoeError, KeyError) as exc:
        raise McpError(str(exc)) from exc


def _t_get_act_text(args: Dict[str, Any]) -> Any:
    try:
        return _client.get_text(
            args["boe_id"],
            max_chars=int(args.get("max_chars", 60000)),
            offset=int(args.get("offset", 0)),
            as_of=args.get("as_of", "") or "",
        )
    except (BoeError, KeyError, ValueError) as exc:
        raise McpError(str(exc)) from exc


def _t_get_document(args: Dict[str, Any]) -> Any:
    try:
        return _client.get_document(args["boe_id"], max_chars=int(args.get("max_chars", 60000)))
    except BoeError as exc:
        raise McpError(str(exc)) from exc


def _t_daily(args: Dict[str, Any]) -> Any:
    try:
        return _client.daily_summary(args["date"])
    except BoeError as exc:
        raise McpError(str(exc)) from exc


def _t_status(args: Dict[str, Any]) -> Any:
    return {
        "server": "es-boe-mcp",
        "version": __version__,
        "source": "BOE datos abiertos (www.boe.es) — public, no auth",
        "indexed_documents": _index.count(),
        "last_crawl": _index.get_state("last_crawl") or "never — run crawl.py",
        "index_scope": "consolidated legislation: " + _scope(),
        **embeddings_status(),
    }


_ID = {"type": "string", "description": "BOE identifier, e.g. BOE-A-2010-10544 (an act from a regional "
                                        "gazette keeps its own, e.g. DOGC-f-1997-90001)"}

TOOLS = [
    Tool(
        "search_legislation",
        "Search Spanish consolidated legislation by title, official number, "
        "issuing department or subject. Hybrid retrieval (BM25 + fuzzy, plus "
        "dense vectors when an embeddings backend is configured). IMPORTANT: the "
        "index covers act METADATA, not article text — use this to identify the "
        "act, then get_act_text to read it. Set only_in_force=true to keep only "
        "status 'vigente': it excludes acts BOE marks as spent (vigencia agotada) "
        "and acts whose consolidated text opens with an unqualified repeal or "
        "annulment already in effect (status 'derogada' / 'anulada'). A partial "
        "repeal ('Norma derogada, salvo …') stays 'vigente' — check the text "
        "before relying on a provision. Each result carries a "
        "verbatim `citation` — copy it, never build a BOE id yourself.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Spanish terms work best, e.g. 'sociedades de capital', 'sector eléctrico'."},
                "mode": {
                    "type": "string",
                    "enum": ["hybrid", "lexical", "semantic", "fuzzy"],
                    "default": "hybrid",
                    "description": "hybrid (default) fuses channels. semantic needs EMBEDDINGS_URL; check `retrieval.semantic` in the response.",
                },
                "limit": {"type": "integer", "default": 20},
                "only_in_force": {"type": "boolean", "default": False},
                "date_from": {"type": "string", "description": "ISO date lower bound on the act's date."},
                "date_to": {"type": "string", "description": "ISO date upper bound."},
            },
            "required": ["query"],
        },
        _t_search,
    ),
    Tool(
        "get_act",
        "Consolidated act metadata only — no text: title, rango, department, "
        "dates, subjects (materias) and the repeal/annulment/spent status flags. "
        "Use before quoting an act so you can state whether it is still in force.",
        {"type": "object", "properties": {"boe_id": _ID}, "required": ["boe_id"]},
        _t_get_act,
    ),
    Tool(
        "get_act_text",
        "CONSOLIDATED text of an act — amendments applied, each provision in the "
        "version in force today (or on `as_of`) — with its header: title, rango, "
        "dates, status flags and `citation`. Long acts come in windows of "
        "max_chars; continue with `offset` = `next_offset` (the Código Civil in "
        "force is ~900,000 characters).",
        {
            "type": "object",
            "properties": {
                "boe_id": _ID,
                "max_chars": {"type": "integer", "default": 60000},
                "offset": {"type": "integer", "default": 0,
                           "description": "Character offset to start from; use next_offset."},
                "as_of": {"type": "string",
                          "description": "YYYY-MM-DD: the text in force on that date. Default today."},
            },
            "required": ["boe_id"],
        },
        _t_get_act_text,
    ),
    Tool(
        "get_document",
        "The document AS PUBLISHED in the BOE (diario_boe). Later amendments are "
        "NOT applied — the response is flagged `consolidated: false`. Use only "
        "when the original wording is what you need; for current law use "
        "get_act_text.",
        {
            "type": "object",
            "properties": {
                "boe_id": _ID,
                "max_chars": {"type": "integer", "default": 60000},
            },
            "required": ["boe_id"],
        },
        _t_get_document,
    ),
    Tool(
        "get_daily_gazette",
        "Everything published in the BOE on one day. Useful for regulatory "
        "monitoring — e.g. a weekly sweep for new energy-sector rules.",
        {
            "type": "object",
            "properties": {"date": {"type": "string", "description": "YYYYMMDD or YYYY-MM-DD"}},
            "required": ["date"],
        },
        _t_daily,
    ),
    Tool(
        "server_status",
        "Index size, last crawl time and whether semantic search is active. Call "
        "this when search results look thin, to tell an empty index apart from a "
        "genuinely empty result set.",
        {"type": "object", "properties": {}},
        _t_status,
    ),
]


if __name__ == "__main__":
    run(TOOLS, name="es-boe-mcp", version=__version__, instructions=INSTRUCTIONS)
