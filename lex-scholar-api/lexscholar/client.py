"""LexScholarClient — one search surface over nine sources.

Ties the three pieces together: the :mod:`router` decides *where* to go, the
adapters in :mod:`lexscholar.sources` know *how* to talk to each upstream, and
:mod:`lexscholar.record` makes everything come back the same shape.

Design commitments:

- **one endpoint, many sources** — ``source`` is a parameter, not a deployment.
- **never blind fan-out** — the router picks 2-3 sources unless told otherwise.
- **parallel** — selected sources are queried concurrently (measured: five
  upstreams in 0.96s wall clock), so federation costs latency once, not N times.
- **nothing fails loudly for the wrong reason** — one dead upstream degrades the
  result set and is reported in ``errors``; it never takes the query down.
- **no silent truncation** — ``sources_queried``, ``sources_skipped`` and
  ``errors`` always travel with the results.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any, Dict, List, Optional, Union

from . import router as _router
from ._http import LexScholarError, RateLimited
from ._text import markup_to_text
from .record import dedupe
from .sources import RESOLVERS, capabilities, get_source

# Sources are I/O-bound, so threads are the right primitive and keep us stdlib-only.
_MAX_WORKERS = 8


class LexScholarClient:
    """Federated client over the open-access legal scholarship sources."""

    def __init__(self, *, timeout: float = 45.0, max_sources: int = 3) -> None:
        self.timeout = timeout
        self.max_sources = max_sources

    # ------------------------------------------------------------------ search
    def search(
        self,
        query: str = "",
        *,
        source: str = "auto",
        jurisdiction: Optional[str] = None,
        language: Optional[str] = None,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
        peer_reviewed_only: bool = False,
        open_access_only: bool = False,
        full_text_only: bool = False,
        limit: int = 20,
        max_sources: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Search across the sources the router selects for this question."""
        plan = _router.route(
            query,
            source=source,
            jurisdiction=jurisdiction,
            language=language,
            peer_reviewed_only=peer_reviewed_only,
            full_text_only=full_text_only,
            max_sources=max_sources or self.max_sources,
        )

        kwargs: Dict[str, Any] = {
            "country": jurisdiction,
            "language": language,
            "year_from": year_from,
            "year_to": year_to,
            "peer_reviewed_only": peer_reviewed_only,
            "open_access_only": open_access_only,
            # Over-fetch a little per source so dedupe still leaves a full page.
            "limit": max(1, min(int(limit), 100)),
        }

        started = time.time()
        if plan["doi"]:
            outcomes = self._resolve_doi_sources(plan["doi"])
        else:
            outcomes = self._fan_out(plan["sources"], query, kwargs)

        records: List[Dict[str, Any]] = []
        per_source: Dict[str, Any] = {}
        errors: List[Dict[str, str]] = []
        for name, payload, err in outcomes:
            if err:
                errors.append({"source": name, "error": err})
                continue
            hits = payload.get("results") or []
            records.extend(hits)
            per_source[name] = {"returned": len(hits), "total": payload.get("total")}
            for extra in ("scanned", "sets", "collections", "window", "truncated_at", "budget"):
                if payload.get(extra) is not None:
                    per_source[name][extra] = payload[extra]

        merged = dedupe(records)
        if peer_reviewed_only:
            merged = [r for r in merged if r.get("peer_reviewed") is True]
        if open_access_only:
            merged = [r for r in merged if r.get("is_oa") is not False]
        merged = _rank(merged, query)[:limit]

        return {
            "query": query,
            "sources_queried": plan["sources"] if not plan["doi"] else RESOLVERS,
            "routing_reasons": plan["reasons"],
            "sources_skipped": plan["skipped"],
            "per_source": per_source,
            "errors": errors,
            "returned": len(merged),
            "elapsed_seconds": round(time.time() - started, 2),
            "results": merged,
        }

    # ----------------------------------------------------------------- records
    def compare_jurisdictions(
        self,
        query: str,
        jurisdictions: List[str],
        *,
        limit_per: int = 5,
        peer_reviewed_only: bool = False,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run one question across several jurisdictions and group the answers.

        This is the comparative-law case the federation exists for: the same
        issue — force majeure, stabilization clauses, investor-state arbitration
        — is litigated everywhere, and each jurisdiction's scholarship sits in a
        different index. Each jurisdiction is routed independently (so Brazil
        goes to SciELO, France to HAL, Indonesia to DOAJ) and the groups are
        returned side by side rather than merged into one ranked list, because
        the point is the contrast, not a single winner.
        """
        codes = [str(j).strip().upper()[:2] for j in jurisdictions if str(j).strip()]
        started = time.time()

        def one(code: str):
            try:
                res = self.search(
                    query,
                    jurisdiction=code,
                    limit=limit_per,
                    peer_reviewed_only=peer_reviewed_only,
                    year_from=year_from,
                    year_to=year_to,
                    max_sources=2,
                )
                return code, res, None
            except Exception as exc:
                return code, None, str(exc)[:200]

        groups: Dict[str, Any] = {}
        errors: List[Dict[str, str]] = []
        if codes:
            with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(codes))) as pool:
                for code, res, err in pool.map(one, codes):
                    if err or res is None:
                        errors.append({"jurisdiction": code, "error": err or "no result"})
                        continue
                    groups[code] = {
                        "sources_queried": res["sources_queried"],
                        "returned": res["returned"],
                        "results": res["results"],
                    }

        covered = [c for c in codes if groups.get(c, {}).get("returned")]
        return {
            "query": query,
            "jurisdictions_requested": codes,
            "jurisdictions_with_results": covered,
            "jurisdictions_empty": [c for c in codes if c not in covered],
            "errors": errors,
            "elapsed_seconds": round(time.time() - started, 2),
            "groups": groups,
        }

    def get_article(self, source: str, article_id: str, **kw: Any) -> Dict[str, Any]:
        """Fetch one article's full metadata from a named source."""
        mod = get_source(source)
        if not hasattr(mod, "get"):
            raise LexScholarError("source %r does not support get()" % source)
        # An id from another source (or a stale one) makes some adapters walk a
        # missing record; say what went wrong instead of surfacing the crash.
        try:
            record = mod.get(article_id, **kw)
        except (AttributeError, KeyError, TypeError, IndexError):
            record = None
        if not record:
            raise LexScholarError(
                "no record %r in source %r. Pass the source and id of the same search "
                "result (search_legal_scholarship returns both)." % (article_id, source))
        return record

    def get_fulltext(
        self,
        source: str,
        article_id: str,
        *,
        offset: int = 0,
        max_chars: int = 20000,
        **kw: Any,
    ) -> Dict[str, Any]:
        """Return readable full text, paginated by characters.

        Sources differ in how far they can go: SciELO serves real JATS bodies,
        others only expose a PDF link. When no text can be produced we say so
        and hand back the URLs instead of pretending.
        """
        mod = get_source(source)
        text: Optional[str] = None
        note: Optional[str] = None

        if hasattr(mod, "fulltext"):
            try:
                text = mod.fulltext(article_id, **kw)
            except Exception as exc:               # upstream guard / 403 / dead link
                note = "full text fetch failed: %s" % exc
        else:
            note = ("source %r has no full-text endpoint; use pdf_url/landing_url"
                    % source)

        record: Dict[str, Any] = {}
        try:
            record = mod.get(article_id, **kw) if hasattr(mod, "get") else {}
        except Exception:
            record = {}

        if not text:
            abstract = record.get("abstract")
            if abstract:
                note = (note or "") + " | returning abstract instead"
                text = abstract
        text = text or ""
        chunk = text[offset:offset + max_chars]
        nxt = offset + max_chars
        return {
            "source": source,
            "id": article_id,
            "title": record.get("title"),
            "note": note,
            "pdf_url": record.get("pdf_url"),
            "landing_url": record.get("landing_url"),
            "license": record.get("license"),
            "citation": record.get("citation"),
            "offset": offset,
            "returned_chars": len(chunk),
            "total_chars": len(text),
            "next_offset": nxt if nxt < len(text) else None,
            "text": chunk,
        }

    def resolve_doi(self, doi: str) -> Dict[str, Any]:
        """Resolve a DOI to authoritative metadata plus its best open copy."""
        outcomes = self._resolve_doi_sources(doi)
        merged = dedupe([r for _, payload, err in outcomes if not err
                         for r in (payload.get("results") or [])])
        errors = [{"source": n, "error": e} for n, _, e in outcomes if e]
        return {
            "doi": doi,
            "sources_queried": [n for n, _, e in outcomes if not e],
            "errors": errors,
            "result": merged[0] if merged else None,
        }

    def list_sources(self) -> List[Dict[str, Any]]:
        """Capability cards for every source, including live OpenAlex budget."""
        return capabilities()

    # ------------------------------------------------------------------ internals
    def _resolve_doi_sources(self, doi: str) -> List[Any]:
        def call(name: str):
            mod = get_source(name)
            try:
                return name, {"results": [mod.get(doi)], "total": 1}, None
            except Exception as exc:
                return name, {}, str(exc)[:200]

        with ThreadPoolExecutor(max_workers=len(RESOLVERS)) as pool:
            return list(pool.map(call, RESOLVERS))

    def _fan_out(self, names: List[str], query: str, kwargs: Dict[str, Any]) -> List[Any]:
        """Query the selected sources concurrently, under a hard deadline.

        The OAI-PMH sources (SciELO, Dialnet, Law Review Commons) have no
        server-side search, so they page and can run long. Waiting on the
        slowest one would make the whole endpoint feel broken — and a Copilot
        Studio connector would simply time out. Whatever has answered by the
        deadline is returned; the rest are reported as timed out, not hidden.
        """
        def call(name: str):
            mod = get_source(name)
            try:
                return name, mod.search(query, **kwargs), None
            except RateLimited:
                return name, {}, "rate limited (429) - source dropped for this query"
            except Exception as exc:
                return name, {}, str(exc)[:200]

        if not names:
            return []

        pool = ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(names)))
        try:
            futures = {pool.submit(call, name): name for name in names}
            outcomes: List[Any] = []
            done: set = set()
            try:
                for fut in as_completed(futures, timeout=self.timeout):
                    outcomes.append(fut.result())
                    done.add(futures[fut])
            except FuturesTimeout:
                pass
            for fut, name in futures.items():
                if name not in done:
                    fut.cancel()
                    outcomes.append(
                        (name, {}, "timed out after %.0fs - partial results returned"
                         % self.timeout)
                    )
            return outcomes
        finally:
            # Do not block on stragglers; they are already accounted for above.
            pool.shutdown(wait=False)


def _rank(records: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    """Order merged results: query-term overlap, then peer review, then recency.

    Each upstream ranks by its own opaque scheme, and those scores are not
    comparable, so the federated list is re-scored on fields we actually hold.
    """
    terms = [t for t in (query or "").lower().split() if len(t) > 2]

    def score(rec: Dict[str, Any]) -> Any:
        hay = " ".join(str(rec.get(f) or "") for f in ("title", "abstract", "journal")).lower()
        overlap = sum(1 for t in terms if t in hay)
        title_hit = sum(1 for t in terms if t in str(rec.get("title") or "").lower())
        return (
            -(overlap + title_hit),
            0 if rec.get("peer_reviewed") is True else 1,
            -(rec.get("year") or 0),
        )

    return sorted(records, key=score)


__all__ = ["LexScholarClient", "LexScholarError", "markup_to_text"]
