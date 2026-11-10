#!/usr/bin/env python3
"""fi-finlex-mcp — Finnish legislation over MCP. No auth, standard library only.

    python server.py                                 # stdio
    python server.py --transport http --port 8000    # http://127.0.0.1:8000/mcp

Search needs an index; browsing and direct fetches do not:

    python crawl.py --from 2020 --to 2026
"""

from __future__ import annotations

import json
import math
import os
import struct
from typing import Any, Dict, Optional

import retrieval
from finlex import ACT_TYPES, FinlexClient, FinlexError
from mcpcore import McpError, Tool, run
from retrieval import Index, embeddings_status

__version__ = "1.1.1"

_client = FinlexClient()
_index = Index()

# A result found by meaning alone counts as related only when its similarity to
# the query stands this many standard deviations above the query's similarity to
# the whole index. The top of a few hundred unrelated documents sits at ~2.3-3.2
# sd; related acts reached across languages sat at 3.45-4.3 sd (bge-m3, the
# 250-act index, 2026-10-09). On voyage-4-lite right answers fell below it, so
# results under the floor are marked, never removed; re-check it after changing
# model or corpus size.
SEMANTIC_MIN_Z = float(os.environ.get("FI_SEMANTIC_MIN_Z", "3.4"))

INSTRUCTIONS = """Finnish legislation from Finlex, the Ministry of Justice's
open-data service. Akoma Ntoso XML, no auth.

TWO CORPORA, NOT ONE.
- `statute-consolidated` — the amended text. This is "the law" for most questions.
- `statute` — the act AS ORIGINALLY PUBLISHED, amendments not applied.
Every response says which one it came from. Do not mix them.

TWO OFFICIAL LANGUAGES. Finnish (`fin@`) and Swedish (`swe@`) texts are equally
authoritative and both are indexed. A search may return either; the `lang` field
says which. That is correct behaviour, not a bug.

NEVER BUILD AN IDENTIFIER. A `lang_version` like `fin@20221099` carries a
version stamp. For the current consolidated text pass just `fin@` or `swe@`
(or nothing): the server asks Finlex for the latest version and reports the
stamp it got in `lang_version`. A specific stamp comes from `browse_year` or
`recent_changes`; an invented one is rejected, not guessed at.

SEARCH IS LOCAL. Finlex has no full-text search endpoint, so `search_acts` runs
against a local index. Read `index_coverage` before concluding an act does not
exist — the index holds only the years that were crawled.

MEANING-ONLY MATCHES. When no word of the query occurs in the index, results
carry `match: "semantic only"` and the response a `warning`: they are leads,
not answers, and the act asked about may be outside the index. Results no more
similar than unrelated text are marked `likely_unrelated` (`semantic_filter`).
`mode: "semantic"` asks for meaning matches on purpose and is not flagged."""


def _t_search(args: Dict[str, Any]) -> Any:
    query = (args.get("query") or "").strip()
    if not query:
        raise McpError("query is required")
    if _index.count() == 0:
        raise McpError(
            "The index is empty — run `python crawl.py --from 2020 --to 2026`. "
            "browse_year, recent_changes and get_act work without it."
        )
    filters: Dict[str, Any] = {}
    if args.get("language"):
        filters["lang"] = args["language"]
    if args.get("consolidated_only"):
        filters["status"] = "consolidated"
    for key in ("date_from", "date_to"):
        if args.get(key):
            filters[key] = args[key]
    out = _index.search(query, mode=args.get("mode", "hybrid"),
                        limit=int(args.get("limit", 20)), filters=filters)
    coverage = _index.get_state("coverage") or "unknown — call server_status"
    out["index_coverage"] = coverage
    out["language_note"] = ("Finnish and Swedish expressions are both indexed and "
                            "both authoritative; see each result's `lang`.")
    channels = out.get("retrieval", {}).get("channels_used", {})
    # Only when the keyword channels were asked and found nothing. In mode
    # "semantic" they are never asked, and every result is a meaning match by
    # request, not a sign that the query's words are missing from the index.
    if out["results"] and "lexical" in channels and not channels["lexical"] and not channels.get("fuzzy"):
        _semantic_only(query, out, coverage)
    return out


def _semantic_only(query: str, out: Dict[str, Any], coverage: str) -> None:
    """Flag a result set that no word of the query reached, and mark its noise.

    The semantic channel always returns its nearest neighbours, related or
    not. With no keyword or substring match behind them, each result is marked
    as a meaning-only match with its similarity, and results that stand no
    higher above the index than chance would put them are marked
    ``likely_unrelated``. They are not removed: the floor was set on bge-m3,
    and on voyage-4-lite acts that were the right answer sat below it
    (two of twelve questions in the 2026-10-09 known-item measurement).
    """
    scores = _similarity(query)
    for result in out["results"]:
        result["match"] = "semantic only"
    warning = (
        "No word of %r occurs in the index (coverage: %s, %d documents). These "
        "results match by meaning only and may be unrelated — the act you want may "
        "simply be outside the index. For a known act use get_act with its year "
        "and number; browse_year lists a year." % (query, coverage, _index.count()))
    if scores is None:
        out["warning"] = warning
        out["semantic_filter"] = {"applied": False,
                                  "note": "Similarity could not be computed; nothing was marked."}
        return
    sims, mean, sd = scores
    marked = 0
    for result in out["results"]:
        sim = sims.get(result["ref"])
        if sim is None:
            continue
        z = (sim - mean) / sd if sd else 0.0
        result["similarity"] = round(sim, 4)
        result["similarity_z"] = round(z, 2)
        if z < SEMANTIC_MIN_Z:
            result["likely_unrelated"] = True
            marked += 1
    if marked and marked == len(out["results"]):
        warning += (" None of them stands above the noise floor (similarity_z %.1f): treat "
                    "the act as probably outside the index." % SEMANTIC_MIN_Z)
    out["warning"] = warning
    out["semantic_filter"] = {
        "applied": True,
        "min_z": SEMANTIC_MIN_Z,
        "likely_unrelated": marked,
        "note": "similarity_z = standard deviations above the query's mean similarity "
                "to all %d indexed documents; results below min_z are marked "
                "likely_unrelated, not removed." % len(sims),
    }


def _similarity(query: str) -> Optional[tuple]:
    """``({ref: cosine}, mean, sd)`` of the query against every indexed vector.

    The same vectors the semantic channel ranked by; the index does not expose
    its scores, so they are recomputed here. None when the backend is down or
    nothing is vectorised for the configured model.
    """
    if not retrieval.embeddings_available():
        return None
    try:
        q = retrieval._embed([query], input_type="query")[0]
    except Exception:  # noqa: BLE001 - an unreachable backend is a reportable state
        return None
    norm = math.sqrt(sum(x * x for x in q)) or 1.0
    q = [x / norm for x in q]
    rows = _index.db.execute(
        "SELECT d.ref, v.dim, v.vec FROM vecs v JOIN docs d ON d.id = v.doc_id "
        "WHERE v.model = ?", (retrieval.embeddings_model(),)).fetchall()
    sims: Dict[str, float] = {}
    for ref, dim, blob in rows:
        if dim != len(q):
            continue
        vec = struct.unpack("<%df" % dim, blob)
        vnorm = math.sqrt(sum(x * x for x in vec)) or 1.0
        sims[ref] = sum(a * b for a, b in zip(q, vec)) / vnorm
    if len(sims) < 2:
        return None
    mean = sum(sims.values()) / len(sims)
    sd = math.sqrt(sum((s - mean) ** 2 for s in sims.values()) / len(sims))
    return sims, mean, sd


def _t_browse(args: Dict[str, Any]) -> Any:
    try:
        return _client.list_year(args.get("act_type", "statute-consolidated"),
                                 int(args["year"]), page=int(args.get("page", 1)))
    except (FinlexError, KeyError, ValueError) as exc:
        raise McpError(str(exc)) from exc


def _t_recent(args: Dict[str, Any]) -> Any:
    try:
        items = _client.recent(args.get("act_type", "statute-consolidated"))
    except FinlexError as exc:
        raise McpError(str(exc)) from exc
    return {
        "count": len(items),
        "note": "Finlex's change feed — the 5 most recently modified expressions, "
                "not the corpus. Each akn_uri already contains the {lang@version} "
                "segment get_act needs.",
        "items": items,
    }


def _t_get_act(args: Dict[str, Any]) -> Any:
    try:
        return _client.get_act(
            args.get("act_type", "statute-consolidated"), int(args["year"]),
            str(args["number"]), str(args.get("lang_version") or "fin@"),
            max_chars=int(args.get("max_chars", 60000)),
        )
    except (FinlexError, KeyError, ValueError) as exc:
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
        "server": "fi-finlex-mcp",
        "version": __version__,
        "source": "opendata.finlex.fi — Akoma Ntoso, public, no auth",
        "act_types": ACT_TYPES,
        "indexed_documents": _index.count(),
        "index_coverage": _index.get_state("coverage") or "not crawled",
        "last_crawl": _index.get_state("last_crawl") or "never — run crawl.py",
        "upstream_quirks": [
            "main.akn is a ZIP package containing main.xml, not raw XML — "
            "parsing it directly fails with a misleading 'not well-formed' error.",
            "/list is a 5-item change feed, not the corpus; enumerate with "
            "/act/{type}/{year}?page=N (5 per page, no page-size parameter).",
            "The root https://opendata.finlex.fi/ returns 403 while every "
            "/finlex/avoindata/v1/... path works.",
        ],
        **_index_updates(),
        **embeddings_status(),
    }


_TYPE = {"type": "string", "enum": sorted(ACT_TYPES), "default": "statute-consolidated"}

TOOLS = [
    Tool(
        "search_acts",
        "Search the full text of Finnish legislation in the local index. Hybrid "
        "retrieval (BM25 + fuzzy, plus dense vectors when EMBEDDINGS_URL is set). "
        "Finnish and Swedish texts are both indexed — filter with `language` if "
        "you need one. Check `index_coverage`: only crawled years are searchable.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Finnish or Swedish terms, e.g. 'sahkomarkkinat', 'kilpailulaki'."},
                "mode": {"type": "string", "enum": ["hybrid", "lexical", "semantic", "fuzzy"], "default": "hybrid"},
                "language": {"type": "string", "enum": ["fin", "swe"], "description": "Restrict to one official language."},
                "consolidated_only": {"type": "boolean", "default": False},
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
        _t_search,
    ),
    Tool(
        "browse_year",
        "List acts published in a year, five per page (Finlex's fixed page size). "
        "Each result carries the `expression_uri` whose {lang@version} segment "
        "get_act requires — this is the correct way to obtain one.",
        {
            "type": "object",
            "properties": {
                "year": {"type": "integer"},
                "act_type": dict(_TYPE),
                "page": {"type": "integer", "default": 1},
            },
            "required": ["year"],
        },
        _t_browse,
    ),
    Tool(
        "get_act",
        "Full text of one act. Returns the consolidated text by default — "
        "`statute` gives the original publication with amendments NOT applied. "
        "For the consolidated text, leave `lang_version` out or give 'fin@' / "
        "'swe@': Finlex resolves the current version and the response names it "
        "in `lang_version` (e.g. 412/1974 -> 'fin@20101051'). A full stamp from "
        "a listing pins that version; invented stamps are rejected.",
        {
            "type": "object",
            "properties": {
                "year": {"type": "integer"},
                "number": {"type": "string", "description": "Act number within the year, e.g. '469'."},
                "lang_version": {"type": "string", "default": "fin@",
                                 "description": "'fin@' / 'swe@' for the current version, or a stamp from a listing, e.g. 'fin@20221099'."},
                "act_type": dict(_TYPE),
                "max_chars": {"type": "integer", "default": 60000},
            },
            "required": ["year", "number"],
        },
        _t_get_act,
    ),
    Tool(
        "recent_changes",
        "Finlex's change feed — the five most recently modified acts, with ready "
        "akn_uris. Useful for regulatory monitoring and as a quick source of a "
        "valid {lang@version}.",
        {"type": "object", "properties": {"act_type": dict(_TYPE)}},
        _t_recent,
    ),
    Tool(
        "server_status",
        "Index size and coverage, last crawl, semantic-search state, and the "
        "upstream quirks this server works around.",
        {"type": "object", "properties": {}},
        _t_status,
    ),
]


if __name__ == "__main__":
    run(TOOLS, name="fi-finlex-mcp", version=__version__, instructions=INSTRUCTIONS)
