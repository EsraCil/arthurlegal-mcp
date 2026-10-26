#!/usr/bin/env python3
"""eu-cellar-mcp — EU law over MCP, through CELLAR. No auth, stdlib only.

    python server.py                                 # stdio
    python server.py --transport http --port 8080    # http://127.0.0.1:8080/mcp

No crawl step and no local index: CELLAR is queried live. What this server adds
is the route itself -- eur-lex.europa.eu's document paths return a JavaScript
shell, so the obvious way to fetch an EU act returns a page that is not the act.
"""
from __future__ import annotations

from typing import Any, Dict

from cellar import LANGS, POOL_CAP, CellarClient, CellarError
from mcpcore import McpError, Tool, run
from retrieval import embeddings_status, semantic_rerank

__version__ = "1.0.0"

_client = CellarClient()

# Candidates handed to the reranker: the head of the prior-ordered pool, as many
# as semantic_rerank embeds in one call. The answer is cut to `limit` after.
RERANK_POOL = 200

INSTRUCTIONS = """EU law from CELLAR, the Publications Office's semantic
repository — the machine-readable layer behind EUR-Lex. Regulations, directives,
decisions, and the case law of the Court of Justice and General Court.

WHY NOT EUR-LEX DIRECTLY. Its document paths (`legal-content/`, `search.html`,
`eli/`) return a JavaScript shell. Fetching one gets you the Official Journal
date index for every request — content that parses cleanly and is not the
document you asked for. Never cite text obtained that way. CELLAR is the route
that works, and it is what these tools use.

CELEX IS THE KEY, AND IT IS NOT GUESSABLE. A CELEX number encodes sector, year,
type and running number (32016R0679 = GDPR). Never construct one: a wrong CELEX
resolves to a different instrument, not to an error. Get it from `search_eu_law` or
from the user.

CONSOLIDATED ≠ ORIGINAL. A CELEX beginning with 0 and carrying a date suffix
(02010D0146-20161217) is a *consolidated* text — the act as amended to that
date, prepared for information and without legal force. The Official Journal
version is the authentic one. `consolidated: true` marks these; say which you
used.

LANGUAGE IS NOT A RENDERING OPTION. Each language is a separate expression that
either exists or does not. If a document has no expression in your language the
tool says so rather than falling back silently — an EU act in the wrong language
is still the right law, but a translation this server invented would not be.

CASE LAW IS HERE TOO. CELEX numbers beginning 6 are judgments (62023CJ0258).
Search returns both legislation and case law; read the CELEX prefix before
treating a result as a statute.

IN FORCE. `in_force` comes from CELLAR's own flag where present. It is not a
substitute for checking amendments — a regulation in force may have been
substantially amended.

CITATIONS. Copy `title`, `celex`, `eli` and `source_url` verbatim."""

TOOLS = [
    Tool(
        name="search_eu_law",
        description=(
            "Search EU legislation and case law by words in the title: every "
            "word must appear (punctuation and one- or two-letter words such as "
            "'v' or 'EU' are ignored). Returns CELEX numbers, which every other "
            "tool needs, one entry per CELEX. Order: binding legislation, then "
            "the Courts' judgments/opinions/orders, then consolidated texts, "
            "then other documents (preparatory acts, OJ case notices, "
            "questions); within each, titles containing the query as a phrase "
            "first, then local relevance ranking."),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "words appearing in the title, e.g. 'general data protection regulation', 'Schrems'"},
                "language": {"type": "string", "enum": sorted(LANGS), "default": "en"},
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                "limit": {"type": "integer", "default": 10, "maximum": 100},
            },
            "required": ["query"],
        },
        handler=lambda a: _t_search(a),
    ),
    Tool(
        name="get_metadata",
        description=(
            "Title, date, ELI and in-force flag for one CELEX, in one language. "
            "Fails loudly when the document has no expression in that language."),
        input_schema={
            "type": "object",
            "properties": {
                "celex": {"type": "string", "description": "e.g. 32016R0679"},
                "language": {"type": "string", "enum": sorted(LANGS), "default": "en"},
            },
            "required": ["celex"],
        },
        handler=lambda a: _t_metadata(a),
    ),
    Tool(
        name="get_document_text",
        description=(
            "Authentic full text for one CELEX, paginated by characters. Resolves "
            "the CELEX to its Cellar work, picks the latest manifestation in the "
            "requested language and fetches it. GDPR is ~350,000 characters — "
            "follow `next_offset`."),
        input_schema={
            "type": "object",
            "properties": {
                "celex": {"type": "string"},
                "language": {"type": "string", "enum": sorted(LANGS), "default": "en"},
                "offset": {"type": "integer", "default": 0},
                "max_chars": {"type": "integer", "default": 20000, "maximum": 100000},
            },
            "required": ["celex"],
        },
        handler=lambda a: _t_text(a),
    ),
    Tool(
        name="sparql",
        description=(
            "Run a SELECT query against the CELLAR endpoint. The escape hatch "
            "for questions the other tools do not cover — relationships between "
            "acts, EuroVoc subjects, procedural history."),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "SPARQL SELECT"},
                "limit": {"type": "integer", "default": 50, "maximum": 500},
            },
            "required": ["query"],
        },
        handler=lambda a: _t_sparql(a),
    ),
    Tool(
        name="server_status",
        description="Upstream reachability and whether semantic reranking is live.",
        input_schema={"type": "object", "properties": {}},
        handler=lambda a: _t_status(a),
    ),
]


def _t_search(args: Dict[str, Any]) -> Any:
    query = args.get("query", "")
    limit = max(1, min(int(args.get("limit", 10)), 100))
    try:
        raw = _client.search(query, args.get("language", "en"),
                             args.get("date_from", ""), args.get("date_to", ""),
                             limit=RERANK_POOL)
    except CellarError as exc:
        raise McpError(str(exc)) from exc
    # Rerank the whole candidate pool, then restore the prior (group, phrase):
    # the sort is stable, so relevance decides the order inside each tier.
    ranked = semantic_rerank(query, raw["results"], fields=("title",))
    results = sorted(ranked["results"], key=lambda d: d["_tier"])[:limit]
    for item in results:
        item.pop("_tier", None)
    out = {
        "query": query,
        "language": raw["language"],
        "returned": len(results),
        "candidates": raw["matched"],
        "ranking": {"method": ranked["method"],
                    "note": ranked.get("note") or ranked.get("warning"),
                    "order": "group (legislation, case_law, consolidated, other), "
                             "then whole-phrase title matches, then relevance"},
        "scope_note": ("Title search only — this does not search the body of "
                       "acts. Matched words: %s. CELEX prefix 3 = legislation, "
                       "6 = case law, 0 with a date suffix = consolidated text."
                       % ", ".join(raw["words"])),
        "results": results,
    }
    if raw["truncated"]:
        out["candidates_note"] = (
            "At least %d titles contain these words; only the first %d CELLAR "
            "returned (in no particular order) were ranked. Add words or a date "
            "range to narrow the search." % (POOL_CAP, POOL_CAP))
    return out


def _t_metadata(args: Dict[str, Any]) -> Any:
    try:
        return _client.metadata(args["celex"], args.get("language", "en"))
    except (CellarError, KeyError) as exc:
        raise McpError(str(exc)) from exc


def _t_text(args: Dict[str, Any]) -> Any:
    try:
        out = _client.text(args["celex"], args.get("language", "en"),
                           int(args.get("offset", 0)), int(args.get("max_chars", 20000)))
    except (CellarError, KeyError) as exc:
        raise McpError(str(exc)) from exc
    if out.get("consolidated"):
        out["warning"] = (
            "This is a CONSOLIDATED text — the act as amended to the date in its "
            "CELEX, prepared for information and without legal force. Cite the "
            "Official Journal version for anything binding.")
    return out


def _t_sparql(args: Dict[str, Any]) -> Any:
    try:
        return _client.raw_sparql(args["query"], int(args.get("limit", 50)))
    except (CellarError, KeyError) as exc:
        raise McpError(str(exc)) from exc


def _t_status(args: Dict[str, Any]) -> Any:
    out: Dict[str, Any] = {
        "server": "eu-cellar-mcp",
        "version": __version__,
        "upstream": "https://publications.europa.eu/webapi/rdf/sparql",
        "auth": "none",
        "coverage": "EU legislation and CJEU/General Court case law, 24 official languages",
        "index": "none — CELLAR is queried live; there is no local corpus to go stale",
        "note": ("eur-lex.europa.eu document paths return a JavaScript shell and "
                 "are never used by this server."),
    }
    try:
        probe = _client.metadata("32016R0679", "en")
        out["upstream_reachable"] = True
        out["probe"] = probe["celex"] + " resolved"
    except CellarError as exc:
        out["upstream_reachable"] = False
        out["error"] = str(exc)
    out.update(embeddings_status())
    return out


if __name__ == "__main__":
    run(TOOLS, name="eu-cellar-mcp", version=__version__, instructions=INSTRUCTIONS)
