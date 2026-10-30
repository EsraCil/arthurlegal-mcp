#!/usr/bin/env python3
"""at-ris-mcp — Austrian federal law and case law over MCP. No auth, stdlib only.

    python server.py                                 # stdio
    python server.py --transport http --port 8000    # http://127.0.0.1:8000/mcp

No crawl step: RIS searches its own corpus. What this server adds is **ranking** —
RIS returns correct hits in alphabetical order, so its first page is close to
useless without local reranking.
"""

from __future__ import annotations

from typing import Any, Dict, List

from mcpcore import McpError, Tool, run
from retrieval import embeddings_status, semantic_rerank
from ris import CASELAW_APPS, LEGISLATION_APPS, RisClient, RisError

__version__ = "1.0.0"

_client = RisClient()

INSTRUCTIONS = """Austrian law from RIS (Rechtsinformationssystem des Bundes), the
official database of the Federal Chancellery.

Coverage: consolidated federal law (BrKons), the authentic gazette (BgblAuth),
provincial law (LrKons), and the case law of the OGH, VwGH, VfGH, BVwG and LVwG.

Two things to know before you read results:

RANKING. RIS itself does not rank — it returns hits alphabetically. This server
reranks by relevance before answering, so the ordering you see is this server's,
not RIS's. `total_upstream` is RIS's true hit count; you are seeing the reranked top of
one page of it.

RECHTSSATZ ≠ JUDGMENT. Much of the OGH corpus consists of *Rechtssätze* — legal
propositions abstracted from a line of decisions. A result with
`doc_type: "Rechtssatz"` is not a judgment; it carries a `decisions` list of the
actual cases that applied it. Cite one of those when you need a judgment.

CITATIONS. Copy the `citation` and `ecli` fields verbatim. Never construct an
Austrian docket number (Geschäftszahl) or an ECLI yourself.

POINT-IN-TIME. `as_of` on legislation search returns the law as it stood on that
date — use it whenever the question concerns a past transaction."""


def _apps_doc(mapping: Dict[str, str]) -> str:
    return " · ".join("%s = %s" % (k, v) for k, v in mapping.items())


def _limited(results: List[Dict[str, Any]], args: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The reranked page, cut to `limit` when one is given."""
    limit = args.get("limit")
    return results[:max(1, min(int(limit), 100))] if limit else results


def _t_search_legislation(args: Dict[str, Any]) -> Any:
    query = (args.get("terms") or args.get("title") or "").strip()
    try:
        raw = _client.search_legislation(
            terms=args.get("terms", ""),
            title=args.get("title", ""),
            application=args.get("application", "BrKons"),
            as_of=args.get("as_of", ""),
            page_size=int(args.get("page_size", 20)),
            page=int(args.get("page", 1)),
        )
    except RisError as exc:
        raise McpError(str(exc)) from exc
    # The act title is the same for every paragraph of one act, so ranking on
    # it alone tied them all; the provision and its keywords tell them apart.
    ranked = semantic_rerank(query, raw["results"],
                             fields=("title", "section", "keywords", "long_title"))
    results = _limited(ranked["results"], args)
    return {
        "total_upstream": raw["total"],
        "returned": len(results),
        "ranking": {
            "method": ranked["method"],
            "note": ranked.get("note") or ranked.get("warning"),
            "why": "RIS returns hits alphabetically, not by relevance.",
        },
        "results": results,
    }


def _t_search_caselaw(args: Dict[str, Any]) -> Any:
    terms = (args.get("terms") or "").strip()
    try:
        raw = _client.search_caselaw(
            terms=terms,
            application=args.get("application", "Justiz"),
            date_from=args.get("date_from", ""),
            date_to=args.get("date_to", ""),
            page_size=int(args.get("page_size", 20)),
            page=int(args.get("page", 1)),
        )
    except RisError as exc:
        raise McpError(str(exc)) from exc
    ranked = semantic_rerank(terms, raw["results"],
                             fields=("docket", "norms", "legal_areas", "court"))
    results = _limited(ranked["results"], args)
    return {
        "total_upstream": raw["total"],
        "returned": len(results),
        "ranking": {
            "method": ranked["method"],
            "note": ranked.get("note") or ranked.get("warning"),
            "why": "RIS returns hits alphabetically, not by relevance.",
        },
        "note": "Results with doc_type 'Rechtssatz' are legal propositions, not "
                "judgments; see their `decisions` list.",
        "results": results,
    }


def _t_fetch(args: Dict[str, Any]) -> Any:
    try:
        return _client.fetch(args["url"], max_chars=int(args.get("max_chars", 60000)),
                             raw=bool(args.get("raw", False)))
    except (RisError, KeyError) as exc:
        raise McpError(str(exc)) from exc


def _t_status(args: Dict[str, Any]) -> Any:
    return {
        "server": "at-ris-mcp",
        "version": __version__,
        "source": "RIS OGD API v2.6 (data.bka.gv.at) — public, no auth",
        "mode": "passthrough + local reranking (no local corpus)",
        "legislation_applications": LEGISLATION_APPS,
        "caselaw_applications": CASELAW_APPS,
        "known_upstream_quirks": [
            "RIS does not rank results; it returns them alphabetically.",
            "The `Kurztitel` parameter is silently ignored — passing it returns "
            "the whole corpus (441,066 hits) instead of an error. This server "
            "never sends it; use `title` (Titel) instead.",
            "API v2.5 was retired and now 404s; this client uses v2.6.",
            "Page sizes are 10, 20, 50 or 100 only; anything else fails RIS's "
            "schema validation. `limit` cuts the reranked page instead.",
            "RIS reports a rejected request inside an HTTP 200 "
            "(OgdSearchResult.Error); this server raises it rather than "
            "showing zero hits. LrKons lives under the Landesrecht endpoint.",
        ],
        **embeddings_status(),
    }


_PAGE = {
    "page_size": {
        "type": "integer", "enum": [10, 20, 50, 100], "default": 20,
        "description": "Results per RIS page. RIS accepts only 10, 20, 50 or 100 "
                       "(verified 2026-10-09). For fewer, keep a page size and set `limit`.",
    },
    "page": {"type": "integer", "default": 1, "description": "1-indexed page number."},
    "limit": {
        "type": "integer", "minimum": 1, "maximum": 100,
        "description": "Return only the best N of the reranked page, e.g. 5.",
    },
}

TOOLS = [
    Tool(
        "search_legislation",
        "Search Austrian legislation. Use `title` for a known act name (precise) "
        "and `terms` for full-text search across the body (broad). Each result "
        "is one provision (`section`: '§ 1295', 'Art. 8'; empty for the act-level "
        "entry) in one version (`valid_from`/`valid_to`, empty = in force). "
        "Results are reranked locally by relevance because RIS returns them "
        "alphabetically. "
        "`as_of` (YYYY-MM-DD) gives the point-in-time version — use it whenever "
        "the question concerns a past transaction. Applications: "
        + _apps_doc(LEGISLATION_APPS),
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Act title, e.g. 'Aktiengesetz', 'Gaswirtschaftsgesetz'."},
                "terms": {"type": "string", "description": "Full-text terms. German works best."},
                "application": {
                    "type": "string",
                    "enum": sorted(LEGISLATION_APPS),
                    "default": "BrKons",
                },
                "as_of": {"type": "string", "description": "YYYY-MM-DD — the law as it stood on this date."},
                **_PAGE,
            },
        },
        _t_search_legislation,
    ),
    Tool(
        "search_caselaw",
        "Search Austrian case law across the supreme courts. Results are reranked "
        "locally (RIS returns them alphabetically). IMPORTANT: many OGH results "
        "are `Rechtssatz` records — legal propositions distilled from a line of "
        "decisions, each carrying the list of decisions that applied it. They are "
        "not judgments; cite the underlying decision. Courts: "
        + _apps_doc(CASELAW_APPS),
        {
            "type": "object",
            "properties": {
                "terms": {"type": "string", "description": "German legal terms, e.g. 'Konkurrenzklausel', 'Energieabgabe'."},
                "application": {
                    "type": "string",
                    "enum": sorted(CASELAW_APPS),
                    "default": "Justiz",
                },
                "date_from": {"type": "string", "description": "Decision date lower bound, YYYY-MM-DD."},
                "date_to": {"type": "string", "description": "Decision date upper bound, YYYY-MM-DD."},
                **_PAGE,
            },
            "required": ["terms"],
        },
        _t_search_caselaw,
    ),
    Tool(
        "fetch_document",
        "Fetch the full text of a RIS document as readable text: extracted from "
        "the HTML or XML file, without page head, CSS, screen-reader duplicates "
        "or page headers. Takes a URL from a search result's `formats` (html, "
        "xml; pdf and rtf are read from the html file), its `url`, or a "
        "Rechtssatz decision's `url` — RIS web pages refuse automated clients, "
        "so those are read from the document file. raw=true returns the file "
        "as served. Only ris.bka.gv.at and ogd.ris.bka.gv.at URLs are accepted.",
        {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "A URL taken from a search result's `formats` or `url` field."},
                "max_chars": {"type": "integer", "default": 60000},
                "raw": {"type": "boolean", "default": False, "description": "Return the file as served (markup) instead of extracted text."},
            },
            "required": ["url"],
        },
        _t_fetch,
    ),
    Tool(
        "server_status",
        "What this server talks to, which court/corpus codes are valid, and the "
        "known upstream quirks worth defending against.",
        {"type": "object", "properties": {}},
        _t_status,
    ),
]


if __name__ == "__main__":
    run(TOOLS, name="at-ris-mcp", version=__version__, instructions=INSTRUCTIONS)
