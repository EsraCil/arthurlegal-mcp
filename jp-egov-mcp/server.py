#!/usr/bin/env python3
"""jp-egov-mcp — Japanese statute law over MCP. No auth, stdlib only.

    python server.py                                 # stdio
    python server.py --transport http --port 8060    # http://127.0.0.1:8060/mcp

No crawl step: e-Gov searches its own full text. What this server adds is the
in-force verdict -- assembled from fields the raw API leaves the caller to
combine, one of which means the opposite of what its name suggests.
"""
from __future__ import annotations

import threading
from typing import Any, Dict

from egov import EgovClient, EgovError
from mcpcore import McpError, Tool, run
from retrieval import embeddings_status, semantic_rerank

__version__ = "1.1.0"

_client = EgovClient()

INSTRUCTIONS = """Japanese statute law from e-Gov 法令API, the official database
of the Digital Agency. Consolidated text of Acts, Cabinet Orders and Ministerial
Ordinances.

SCOPE: LEGISLATION ONLY. No case law. Japanese judgments are not here — if the
answer turns on how a court has applied a provision, say that it was not
checked. Do not present a statutory text as settled practice.

IN-FORCE STATUS IS COMPUTED, AND ONE FIELD LIES. `status.in_force` is derived
from `current_revision_status` (CurrentEnforced) and `repeal_status` ("None"
when never repealed). The raw field named `remain_in_force` does NOT mean the
law is in force: it is 残存効力, whether an already-repealed law keeps residual
effect through transitional provisions. Live statutes carry False there. Both
inputs are reported next to the verdict so you can check it.

FUTURE AMENDMENTS. `amendment_scheduled_enforcement_date` marks a change that is
promulgated but not yet in force. A provision can be current today and different
next April. When that field is set, say so rather than presenting the future
text as current law.

POINT IN TIME. `as_of` (YYYY-MM-DD) returns the revision in force on that date —
use it when advising on a past transaction. e-Gov keeps these revisions only
from 2017-04-01; an earlier date is refused, and the tool says so rather than
falling back to current text.

LANGUAGE. The corpus is Japanese. Search in Japanese: 会社法, 電気事業, 労働契約.
Romanised or English queries mostly fail; the local reranker cannot compensate
for terms the upstream keyword index never matched.

TITLE FIRST. `search_laws` puts statutes whose title or abbreviation contains the
keyword first (`match`: "title"), the exact title or abbreviation on top: 個人情報の保護,
独禁法, 労基法. Full-text matches follow (`match`: "text"). The full-text index runs in
law-number order, so on its own a phrase many statutes quote buries the statute itself.

ARTICLE NUMBERS. `get_article` takes the arabic number ("331" for 第三百三十一条).
Inserted articles carry underscore form ("331_2" for 第三百三十一条の二).

CITATIONS. Copy `citation`, `law_num` and `source_url` verbatim. Never construct
a law number (法令番号) or a law_id."""

TOOLS = [
    Tool(
        name="search_laws",
        description=(
            "Keyword search across Japanese statutes. Statutes whose title or "
            "abbreviation contains the keyword come first (exact title on top), then "
            "full-text matches with the sentences that matched; each with its in-force "
            "status. Query in Japanese. NOTE: the page size counts matching "
            "SENTENCES, not statutes -- 3 sentences may all sit inside one Act. "
            "Raise max_sentences to widen the statute spread; the response "
            "reports both counts."),
        input_schema={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "Japanese search term"},
                "max_sentences": {"type": "integer", "default": 30, "maximum": 100,
                                  "description": "matching sentences per page, not statutes"},
                "offset": {"type": "integer", "default": 0},
            },
            "required": ["keyword"],
        },
        handler=lambda a: _t_search(a),
    ),
    Tool(
        name="list_laws",
        description="Browse the statute list with metadata and in-force status.",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20, "maximum": 100},
                "offset": {"type": "integer", "default": 0},
            },
        },
        handler=lambda a: _t_list(a),
    ),
    Tool(
        name="get_law",
        description=(
            "Metadata and in-force status for one statute by law_id "
            "(e.g. 417AC0000000086 = 会社法). No text — use get_law_text."),
        input_schema={
            "type": "object",
            "properties": {
                "law_id": {"type": "string"},
                "as_of": {"type": "string",
                          "description": "YYYY-MM-DD; revision in force that day (2017-04-01 or later)"},
            },
            "required": ["law_id"],
        },
        handler=lambda a: _t_get(a),
    ),
    Tool(
        name="get_law_text",
        description=(
            "Consolidated full text, paginated by characters. Large statutes run "
            "to hundreds of thousands of characters; follow `next_offset`."),
        input_schema={
            "type": "object",
            "properties": {
                "law_id": {"type": "string"},
                "as_of": {"type": "string"},
                "offset": {"type": "integer", "default": 0},
                "max_chars": {"type": "integer", "default": 20000, "maximum": 100000},
            },
            "required": ["law_id"],
        },
        handler=lambda a: _t_text(a),
    ),
    Tool(
        name="get_article",
        description=(
            "One article by arabic number — '331' for 第三百三十一条, '331_2' for "
            "第三百三十一条の二. Prefer this over fetching the whole statute."),
        input_schema={
            "type": "object",
            "properties": {
                "law_id": {"type": "string"},
                "article": {"type": "string"},
                "as_of": {"type": "string"},
            },
            "required": ["law_id", "article"],
        },
        handler=lambda a: _t_article(a),
    ),
    Tool(
        name="server_status",
        description="Upstream reachability and whether semantic reranking is live.",
        input_schema={"type": "object", "properties": {}},
        handler=lambda a: _t_status(a),
    ),
]


def _t_search(args: Dict[str, Any]) -> Any:
    keyword = args.get("keyword", "")
    offset = int(args.get("offset", 0))
    side: Dict[str, Any] = {"results": []}
    waiter = None
    if offset == 0:                      # title matches lead the first page only
        def titles_call() -> None:
            try:
                side.update(_client.search_titles(keyword))
            except EgovError as exc:     # the full-text page still answers
                side["error"] = str(exc)
        waiter = threading.Thread(target=titles_call, daemon=True)
        waiter.start()                   # both e-Gov calls in flight at once
    try:
        raw = _client.search(keyword, int(args.get("max_sentences", 30)), offset)
    except EgovError as exc:
        raise McpError(str(exc)) from exc
    if waiter is not None:
        waiter.join()
    titles, title_error = side, side.get("error", "")
    ranked = semantic_rerank(keyword, raw["results"], fields=("title", "abbrev", "category"))
    by_id = {r["law_id"]: r for r in ranked["results"]}
    first = []
    for r in titles["results"]:
        hit = by_id.pop(r["law_id"], None)
        first.append(dict(r, match="title", matches=hit["matches"] if hit else []))
    rest = [dict(r, match="text") for r in ranked["results"] if r["law_id"] in by_id]
    out = {
        "total_upstream": raw["total"],
        "sentence_count": raw["sentence_count"],
        "statutes_on_this_page": raw["statutes_on_this_page"],
        "next_offset": raw["next_offset"],
        "title_matches": len(first),
        "returned": len(first) + len(rest),
        "ranking": {"method": "title first, then %s" % ranked["method"],
                    "note": ("Statutes whose title or abbreviation contains the keyword come first "
                             "(exact title or abbreviation, then Acts before orders, unrepealed before "
                             "repealed, shorter title first); full-text matches follow. "
                             + (ranked.get("note") or ranked.get("warning") or ""))},
        "scope_note": "Legislation only — e-Gov holds no case law.",
        "results": first + rest,
    }
    if title_error:
        out["title_search_error"] = title_error
    return out


def _t_list(args: Dict[str, Any]) -> Any:
    try:
        return _client.list_laws(int(args.get("limit", 20)), int(args.get("offset", 0)))
    except EgovError as exc:
        raise McpError(str(exc)) from exc


def _t_get(args: Dict[str, Any]) -> Any:
    try:
        rec = _client.get_law(args["law_id"], args.get("as_of", ""))
    except (EgovError, KeyError) as exc:
        raise McpError(str(exc)) from exc
    rec.pop("_full_text", None)
    return rec


def _t_text(args: Dict[str, Any]) -> Any:
    try:
        return _client.get_text(args["law_id"], args.get("as_of", ""),
                                int(args.get("offset", 0)), int(args.get("max_chars", 20000)))
    except (EgovError, KeyError) as exc:
        raise McpError(str(exc)) from exc


def _t_article(args: Dict[str, Any]) -> Any:
    try:
        return _client.get_article(args["law_id"], args["article"], args.get("as_of", ""))
    except (EgovError, KeyError) as exc:
        raise McpError(str(exc)) from exc


def _t_status(args: Dict[str, Any]) -> Any:
    out: Dict[str, Any] = {
        "server": "jp-egov-mcp",
        "version": __version__,
        "upstream": "https://laws.e-gov.go.jp/api/2",
        "auth": "none",
        "coverage": "Japanese Acts, Cabinet Orders and Ministerial Ordinances — no case law",
        "index": "none — e-Gov is queried live; there is no local corpus to go stale",
    }
    try:
        probe = _client.list_laws(limit=1)
        out["upstream_reachable"] = True
        out["laws_available"] = probe["total"]
    except EgovError as exc:
        out["upstream_reachable"] = False
        out["error"] = str(exc)
    out.update(embeddings_status())
    return out


if __name__ == "__main__":
    run(TOOLS, name="jp-egov-mcp", version=__version__, instructions=INSTRUCTIONS)
