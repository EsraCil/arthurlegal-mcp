#!/usr/bin/env python3
"""pl-sejm-mcp — Polish legislation (Dz.U. / M.P.) over MCP. No auth, stdlib only.

    python server.py                                 # stdio
    python server.py --transport http --port 8000    # http://127.0.0.1:8000/mcp

Optional, for subject search across acts:

    python crawl.py --from 2015 --to 2026
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from mcpcore import McpError, Tool, run
from retrieval import Index, embeddings_status, semantic_rerank
from sejm import (PUBLISHERS, REFERENCES_LIMIT, TITLE_POOL, SejmClient, SejmError,
                  in_force_from_status, rank_title_matches)

__version__ = "1.1.0"

_client = SejmClient()
_index = Index()

INSTRUCTIONS = """Polish legislation from the Sejm's ELI API — Dziennik Ustaw
(Journal of Laws) and Monitor Polski.

STATUS IS PART OF THE CITATION. Every act carries `in_force` — the API's own
`inForce` flag (`IN_FORCE` / `NOT_IN_FORCE`) — and `status`, the publisher's
label. The label is often about consolidation, not validity: the Civil Code is
`akt posiada tekst jednolity` ("has a consolidated text") and in force. Read
`in_force` for validity and report both. `in_force: null` means the API did not
say; the response then carries `in_force_warning` — do not guess. Citing a
Polish act without its status is an incomplete citation, the same discipline the
Azerbaijani e-qanun source follows.

AMENDMENTS. `get_act` returns a `references` graph with the publisher's own
relation names — `Akty zmieniające` (amending acts), `Akty uchylone` (repealed
acts), `Akty wykonawcze` (implementing acts), `Inf. o tekście jednolitym`
(consolidated-text notices). Each relation is cut to `references_limit` entries;
`references_total` gives the full counts. An act can be in force and still have
been amended many times; check the graph before treating a text as current.

TWO SEARCHES, DIFFERENT REACH.
- `search_by_title` hits the live API but matches TITLES ONLY. Acts the title
  names come first, in force before repealed (`title_match: exact title` is
  the act bearing the name), then amending acts and notices, newest first.
- `search_indexed` hits the local index and covers titles plus the Sejm's own
  subject keywords — use it for "which act governs X".
Neither searches article text: most acts are PDF-only (`textHTML: false`), so
there is no HTML body to index. For article-level work, open the act's PDF.

CITATIONS. Copy `display_address` (e.g. `Dz.U. 2024 poz. 1984`) and `citation`
verbatim. Never construct a Dz.U. number."""


def _t_search_title(args: Dict[str, Any]) -> Any:
    title = (args.get("title") or "").strip()
    limit = max(1, min(int(args.get("limit", 20)), 100))
    offset = max(0, int(args.get("offset", 0)))
    common = dict(
        publisher=args.get("publisher", ""),
        year=args.get("year"),
        act_type=args.get("act_type", ""),
        in_force_only=bool(args.get("in_force_only")),
    )
    try:
        if not title or offset >= TITLE_POOL:
            # A listing by year/type, or a page past the ranked pool: upstream
            # order and paging, exactly as the API gives them.
            result = _client.search(title=title, limit=limit, offset=offset, **common)
            if title:
                result["ranking"] = {
                    "method": "upstream order (newest first)",
                    "note": "offset is past the %d title matches that are ranked; "
                            "narrow with year or act_type instead." % TITLE_POOL,
                }
            return result
        pool = _client.search_pool(title, **common)
    except SejmError as exc:
        raise McpError(str(exc)) from exc

    # The Sejm returns title matches newest first, so a code with a hundred
    # amending acts comes last. Rank the whole pool, then page through it:
    # a base order (cosine when an embeddings backend is up, BM25 otherwise),
    # then the structural tier, which is what actually separates the Code from
    # acts that merely name it.
    base = semantic_rerank(title, pool["results"], fields=("title",))
    ranked = rank_title_matches(title, base["results"])
    window = []
    for act in ranked[offset: offset + limit]:
        item = {k: v for k, v in act.items() if k not in ("_upstream_pos", "_rerank_score")}
        window.append(item)
    ranking: Dict[str, Any] = {
        "method": base.get("method", "none"),
        "ranked": len(ranked),
        "order": "acts the title names (in force first; then exact title, title "
                 "starts with query, title contains query) > amending acts and "
                 "notices (newest first) > words matching elsewhere. See "
                 "`title_match` on each result.",
    }
    if pool["count"] > len(ranked):
        ranking["pool_note"] = (
            "Ranked the newest %d of %d upstream matches. An older act outside them "
            "is not in these results — narrow with year or act_type."
            % (len(ranked), pool["count"]))
    for key in ("note", "warning"):
        if base.get(key):
            ranking[key] = base[key]
    return {
        "count": pool["count"],
        "returned": len(window),
        "scope": "TITLE MATCH ONLY — the Sejm search endpoint does not read "
                 "act bodies. Use search_indexed for body/keyword search.",
        "ranking": ranking,
        "results": window,
    }


def _indexed_in_force(doc: Optional[Dict[str, Any]]) -> Optional[bool]:
    """In force for one index row.

    Rows from a crawl that recorded the API flag carry ``in_force_api`` and
    their ``in_force`` is the flag. Older rows hold an ``in_force`` computed as
    "status == obowiązujący", which is wrong for every act with a consolidated
    text, so for those only the status label is read.
    """
    if not doc:
        return None
    meta = doc.get("meta") or {}
    if "in_force_api" in meta:
        value = meta.get("in_force")
        return value if isinstance(value, bool) else None
    return in_force_from_status(doc.get("status") or "")


# An act in force goes ahead of the repealed acts ranked just above it. With
# 2015-2026 indexed, a question about a regulation met the 2018 version it replaced,
# that version's amendments and the consolidated-text notice, which a meaning search
# rightly ranks as the same subject. On the 2026-10-10 measurement set: rank-1
# answers 50% -> 83% for questions in plain words, keyword queries unchanged, and a
# lift of 1.1, 1.2 or 1.5 gave the same order. Fusion scores sit close together at
# the top (rank 1 and rank 3 differ by 3%), so x1.1 moves an act in force up past
# repealed acts ranked up to about six places above it; a repealed act that matches
# both by keyword and by meaning still leads.
IN_FORCE_LIFT = 1.1


def _t_search_indexed(args: Dict[str, Any]) -> Any:
    query = (args.get("query") or "").strip()
    if not query:
        raise McpError("query is required")
    if _index.count() == 0:
        raise McpError(
            "The local index is empty — run `python crawl.py --from 2015 --to 2026`. "
            "search_by_title works without it (titles only)."
        )
    limit = max(1, int(args.get("limit", 20)))
    in_force_only = bool(args.get("in_force_only"))
    filters: Dict[str, Any] = {}
    for key in ("date_from", "date_to"):
        if args.get(key):
            filters[key] = args[key]
    # In force is not one status label (the Civil Code's is "akt posiada tekst
    # jednolity"), so it cannot be an equality filter on the index. Over-fetch
    # and keep the rows whose in-force reading is True.
    out = _index.search(
        query,
        mode=args.get("mode", "hybrid"),
        limit=limit * 5 if in_force_only else limit * 3,
        filters=filters,
    )
    kept, dropped_unknown, dropped_repealed = [], 0, 0
    for result in out["results"]:
        flag = _indexed_in_force(_index.get(result["ref"]))
        result["in_force"] = flag
        if in_force_only and flag is not True:
            if flag is None:
                dropped_unknown += 1
            else:
                dropped_repealed += 1
            continue
        kept.append(result)
    # A date range asks about the law of a period, not of today: no lift then
    # (the second 2026-10-10 set, weighted to 2015-2021, lost a repealed target to it).
    historical = bool(filters.get("date_from") or filters.get("date_to"))
    if not in_force_only and not historical:
        order = sorted(enumerate(kept), key=lambda p: (
            -p[1]["score"] * (IN_FORCE_LIFT if p[1]["in_force"] is True else 1.0), p[0]))
        kept = [r for _, r in order]
        out["in_force_order"] = ("Acts in force go ahead of repealed acts ranked up to about six places "
                                 "above them (score x%.1f); each result's `in_force` says which it is."
                                 % IN_FORCE_LIFT)
    out["results"] = kept[:limit]
    out["total"] = len(out["results"])
    if in_force_only:
        out["in_force_filter"] = {
            "excluded_not_in_force": dropped_repealed,
            "excluded_unknown": dropped_unknown,
            "note": "Kept acts whose in-force reading is true. Unknown means the "
                    "index row carries no flag and its label does not settle it "
                    "(e.g. 'bez statusu'); a re-crawl records the API flag.",
        }
    out["coverage"] = _index.get_state("coverage") or "unknown — check server_status"
    out["scope_note"] = (
        "Covers titles, act types, issuing bodies and the Sejm's subject "
        "keywords — NOT article text."
    )
    return out


def _t_get_act(args: Dict[str, Any]) -> Any:
    try:
        return _client.get_act(
            args["publisher"], int(args["year"]), int(args["pos"]),
            references_limit=int(args.get("references_limit", REFERENCES_LIMIT)),
            relation=args.get("relation", "") or "",
            references_offset=int(args.get("references_offset", 0)),
        )
    except (SejmError, KeyError, ValueError) as exc:
        raise McpError(str(exc)) from exc


def _t_get_text(args: Dict[str, Any]) -> Any:
    try:
        return _client.get_text(
            args["publisher"], int(args["year"]), int(args["pos"]),
            fmt=args.get("fmt", "html"), max_chars=int(args.get("max_chars", 60000)),
        )
    except (SejmError, KeyError, ValueError) as exc:
        raise McpError(str(exc)) from exc


def _t_list_year(args: Dict[str, Any]) -> Any:
    try:
        return _client.list_year(
            args.get("publisher", "DU"), int(args["year"]),
            limit=int(args.get("limit", 50)), offset=int(args.get("offset", 0)),
        )
    except (SejmError, KeyError, ValueError) as exc:
        raise McpError(str(exc)) from exc


def _index_updates() -> Dict[str, Any]:
    """Updates applied at boot on top of the baked index (indeks_ek.py at the repository root)."""
    try:
        value = json.loads(_index.get_state("ek") or "null")
    except ValueError:
        return {}
    return {"index_updates": value} if isinstance(value, dict) else {}


def _t_status(args: Dict[str, Any]) -> Any:
    return {
        "server": "pl-sejm-mcp",
        "version": __version__,
        "source": "api.sejm.gov.pl/eli — public, no auth, ELI-compliant",
        "publishers": PUBLISHERS,
        "indexed_documents": _index.count(),
        "index_coverage": _index.get_state("coverage") or "not crawled",
        "last_crawl": _index.get_state("last_crawl") or "never — run crawl.py",
        "index_scope": "titles + act type + issuing body + Sejm subject keywords "
                       "(not article text)",
        **_index_updates(),
        **embeddings_status(),
    }


_LOC = {
    "publisher": {"type": "string", "enum": sorted(PUBLISHERS), "default": "DU"},
    "year": {"type": "integer"},
    "pos": {"type": "integer", "description": "Position within the year (the 'poz.' number)."},
}

TOOLS = [
    Tool(
        "search_by_title",
        "Search Polish acts by TITLE via the live Sejm API. Precise when you know "
        "the act's name ('Prawo energetyczne', 'Kodeks spółek handlowych'): the "
        "act that bears the name ranks first (`title_match: exact title`), ahead "
        "of the acts that amend it. It does NOT read act bodies — for subject "
        "search use search_indexed. Every result carries `in_force` and the "
        "publisher's `status`; report both.",
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Polish title words, e.g. 'energetyczne'."},
                "publisher": {"type": "string", "enum": sorted(PUBLISHERS)},
                "year": {"type": "integer"},
                "act_type": {"type": "string", "description": "e.g. 'Ustawa', 'Rozporządzenie'."},
                "in_force_only": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 20},
                "offset": {"type": "integer", "default": 0},
            },
        },
        _t_search_title,
    ),
    Tool(
        "search_indexed",
        "Subject search over the local index: titles, act types, issuing bodies "
        "and the Sejm's own controlled keywords. Hybrid retrieval (BM25 + fuzzy, "
        "plus dense vectors when EMBEDDINGS_URL is configured). Use this for "
        "'which act governs X'. Check `coverage` — the index holds the year range "
        "that was crawled, not all of Polish law. in_force_only keeps acts the "
        "index records as in force and reports what it excluded.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Polish terms work best."},
                "mode": {
                    "type": "string",
                    "enum": ["hybrid", "lexical", "semantic", "fuzzy"],
                    "default": "hybrid",
                },
                "in_force_only": {"type": "boolean", "default": False},
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
        _t_search_indexed,
    ),
    Tool(
        "get_act",
        "Full metadata for one act: title, in force + status, entry into force, "
        "ELI, subject keywords, transposed EU directives, and the `references` "
        "amendment graph (which acts amended, repealed or implement it), each "
        "entry with its own Dz.U./M.P. citation. Long relations are cut to "
        "`references_limit` with totals in `references_total`; page one relation "
        "with `relation` + `references_offset`. Read the graph before treating "
        "the text as current law.",
        {
            "type": "object",
            "properties": {
                **_LOC,
                "references_limit": {"type": "integer", "default": REFERENCES_LIMIT,
                                     "description": "Entries per relation; 0 = totals only."},
                "relation": {"type": "string",
                             "description": "Return only this relation, e.g. 'Akty zmieniające'."},
                "references_offset": {"type": "integer", "default": 0,
                                      "description": "Start within `relation` (with relation only)."},
            },
            "required": ["publisher", "year", "pos"],
        },
        _t_get_act,
    ),
    Tool(
        "get_act_text",
        "Text of an act. fmt='html' returns the text; fmt='pdf' returns the PDF "
        "URL (binary is not inlined). Many Polish acts are PDF-only — this checks "
        "the publisher's textHTML flag first and tells you plainly rather than "
        "returning an empty body.",
        {
            "type": "object",
            "properties": {
                **_LOC,
                "fmt": {"type": "string", "enum": ["html", "pdf"], "default": "html"},
                "max_chars": {"type": "integer", "default": 60000},
            },
            "required": ["publisher", "year", "pos"],
        },
        _t_get_text,
    ),
    Tool(
        "list_year",
        "Everything published by Dz.U. or M.P. in a given year. Useful for "
        "regulatory monitoring and for finding an act when you know roughly when "
        "it appeared.",
        {
            "type": "object",
            "properties": {
                "publisher": {"type": "string", "enum": sorted(PUBLISHERS), "default": "DU"},
                "year": {"type": "integer"},
                "limit": {"type": "integer", "default": 50},
                "offset": {"type": "integer", "default": 0},
            },
            "required": ["year"],
        },
        _t_list_year,
    ),
    Tool(
        "server_status",
        "Index size and coverage, last crawl, and whether semantic search is on. "
        "Call this to tell an un-crawled index apart from a genuinely empty result.",
        {"type": "object", "properties": {}},
        _t_status,
    ),
]


if __name__ == "__main__":
    run(TOOLS, name="pl-sejm-mcp", version=__version__, instructions=INSTRUCTIONS)
