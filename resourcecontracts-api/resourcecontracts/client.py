"""Dependency-free client for the ResourceContracts.org public API.

Wraps the public, unauthenticated REST backend at
``https://api.resourcecontracts.org`` — the open repository of petroleum and
mining contracts published by the Natural Resource Governance Institute (NRGI),
the Columbia Center on Sustainable Investment (CCSI) and partners. Content is
CC BY-SA 4.0. Signed oil, gas and mining contracts are a primary source for energy
and natural-resources legal work.

Uses only the Python standard library, so it runs on a stock Python 3.9+ with no
pip installs (mirrors the ``eqanun`` client). The API surface was confirmed by
black-box observation of the official API and its GitHub wiki (see ``API.md``);
these are GET endpoints that return only published contracts.

Example
-------
    from resourcecontracts import ResourceContractsClient

    c = ResourceContractsClient()
    hits = c.search(country="az", resource="Hydrocarbons", per_page=5)
    print(hits["total"], "results")
    meta = c.get_metadata(hits["results"][0]["id"])
    text = c.get_fulltext(meta["id"])
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from ._html import html_to_text

API_BASE = "https://api.resourcecontracts.org"
SITE_BASE = "https://www.resourcecontracts.org"

# Polite, identifying User-Agent. The API is open (no auth, no bot guard); we
# still identify the caller as good-citizen practice for a corporate deployment.
_DEFAULT_UA = "arthurlegal-resourcecontracts/0.1 (+legal-research; stdlib-urllib)"

# Sister database on the same platform (agriculture / land / forestry contracts).
# Same API shape; point the client at it by passing api_base=OPENLAND_API_BASE.
OPENLAND_API_BASE = "https://api.openlandcontracts.org"


class ResourceContractsError(RuntimeError):
    """Raised for transport errors or non-2xx / invalid API responses."""


def _facet_values(raw: Any) -> Any:
    """A facet as the list of its distinct values.

    The API deduplicates the values of the hits it returned and keeps their positions, so a facet with
    gaps arrives as an object -- {"0": "ST", "7": "CD"} -- that reads like counts. They are not counts.
    """
    if isinstance(raw, dict):
        return list(raw.values())
    return raw


class ResourceContractsClient:
    """Thin, polite client over the ResourceContracts.org public API."""

    def __init__(
        self,
        *,
        api_base: str = API_BASE,
        user_agent: str = _DEFAULT_UA,
        timeout: float = 30.0,
        retries: int = 2,
        retry_backoff: float = 1.5,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.retry_backoff = retry_backoff

    # ---------------------------------------------------------------- transport
    def _request(self, url: str) -> bytes:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, */*;q=0.8",
        }
        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                # 4xx are deterministic; do not retry those.
                if 400 <= exc.code < 500:
                    raise ResourceContractsError(f"HTTP {exc.code} for {url}") from exc
                last_exc = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_exc = exc
            if attempt < self.retries:
                time.sleep(self.retry_backoff * (attempt + 1))
        raise ResourceContractsError(f"request failed for {url}: {last_exc}") from last_exc

    def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.api_base}{path}"
        if params:
            # Drop None values so callers can pass optional filters uniformly.
            clean = {k: v for k, v in params.items() if v is not None and v != ""}
            if clean:
                url += "?" + urllib.parse.urlencode(clean, doseq=True)
        raw = self._request(url)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ResourceContractsError(f"invalid JSON from {url}") from exc

    # ------------------------------------------------------------------- search
    def search(
        self,
        query: str = "",
        *,
        country: Optional[str] = None,
        resource: Optional[str] = None,
        year: Optional[Any] = None,
        contract_type: Optional[str] = None,
        document_type: Optional[str] = None,
        language: Optional[str] = None,
        company_name: Optional[str] = None,
        corporate_group: Optional[str] = None,
        annotation_category: Optional[str] = None,
        annotated: Optional[bool] = None,
        recent: Optional[bool] = None,
        sortby: Optional[str] = None,
        order: str = "desc",
        from_: int = 0,
        per_page: int = 20,
        group: str = "metadata",
    ) -> Dict[str, Any]:
        """Search contracts via the grouped full-text endpoint ``/contracts/group``.

        Parameters
        ----------
        query : free-text query (``q``); empty string browses all contracts.
        country : ISO-3166 alpha-2 country code (case-insensitive), e.g. ``"az"``.
        resource : resource name exactly as the taxonomy lists it, e.g.
            ``"Hydrocarbons"``, ``"Gold"`` (see :meth:`list_resources`).
        year : contract signature year.
        contract_type : e.g. ``"Production or Profit Sharing Agreement"``.
        document_type : e.g. ``"Company-State Contract"``.
        language : contract language code, e.g. ``"en"``.
        company_name / corporate_group : company filters.
        annotation_category : filter to contracts annotated for a given category
            (see :meth:`list_annotation_categories`).
        annotated : ``True`` restricts to contracts with annotations.
        recent : ``True`` restricts to recently published contracts.
        sortby / order : best-effort server-side sort (e.g. ``sortby="year"``).
        from_ / per_page : pagination offset and page size.
        group : which sub-documents to embed per hit
            (``"metadata"``, ``"text"``, ``"annotations"`` or a ``|``-joined mix).

        Returns ``{"total", "from", "per_page", "facets": {country, year,
        resource}, "results": [...]}``. A facet is the list of distinct values among the hits of this
        page (no counts). Each hit carries id, name, year_signed,
        contract_type, document_type, resource, countries, language, ...
        """
        params = {
            "q": query,
            "country_code": country.lower() if isinstance(country, str) else country,
            "resource": resource,
            "year": year,
            "contract_type": contract_type,
            "document_type": document_type,
            "language": language,
            "company_name": company_name,
            "corporate_group": corporate_group,
            "annotation_category": annotation_category,
            "annotated": _bool01(annotated),
            "recent": _bool01(recent),
            "sortby": sortby,
            "order": order,
            "from": from_,
            "per_page": per_page,
            "group": group,
        }
        data = self._get_json("/contracts/group", params)
        return {
            "total": data.get("total"),
            "from": from_,
            "per_page": per_page,
            "facets": {
                "country": _facet_values(data.get("country")),
                "year": _facet_values(data.get("year")),
                "resource": _facet_values(data.get("resource")),
            },
            "results": data.get("results") or [],
        }

    def count(self, query: str = "", **filters: Any) -> int:
        """Number of contracts matching a query/filters (cheap: per_page=1).

        Accepts the same keyword filters as :meth:`search`.
        """
        filters.setdefault("per_page", 1)
        return self.search(query, **filters)["total"]

    def total_count(self) -> int:
        """Grand total of all published contracts (``/contracts/count``)."""
        data = self._get_json("/contracts/count")
        if isinstance(data, int):
            return data
        # Some deployments wrap it; be lenient.
        if isinstance(data, dict):
            return int(data.get("count") or data.get("total") or 0)
        return int(data)

    # ---------------------------------------------------------------- contracts
    def get_metadata(self, contract_id: Any) -> Dict[str, Any]:
        """Full structured metadata for one contract (``/contract/{id}/metadata``).

        Includes name, countries, resource, contract_type, document_type,
        year_signed, number_of_pages, government_entity, participation
        (companies + shares), concession, source_url, ...
        """
        return self._get_json(f"/contract/{_cid(contract_id)}/metadata")

    def get_text_page(self, contract_id: Any, *, page: int = 1) -> Dict[str, Any]:
        """One page of OCR/full text (``/contract/{id}/text?page=``).

        Returns the raw API shape ``{"total": <page_count>, "result": [{text,
        ...}]}``. Text may contain ``<br />`` and HTML entities; use
        :meth:`get_fulltext` for cleaned plain text.
        """
        return self._get_json(f"/contract/{_cid(contract_id)}/text", {"page": int(page)})

    def get_page_count(self, contract_id: Any) -> int:
        """Number of PDF pages for a contract (from metadata; 0 if unknown)."""
        try:
            return int(self.get_metadata(contract_id).get("number_of_pages") or 0)
        except (ResourceContractsError, ValueError, TypeError):
            return 0

    def get_fulltext(self, contract_id: Any, *, start_page: int = 1,
                     page_count: Optional[int] = None, as_text: bool = True,
                     max_pages: int = 500) -> str:
        """Contract text concatenated across PDF pages, cleaned to plain text.

        The API serves exactly one PDF page per ``/text?page=N`` request (its
        ``total`` field is a per-response record count, NOT the page count — the
        page count comes from metadata's ``number_of_pages``).

        - ``page_count=None`` (default): fetch from ``start_page`` to the end of
          the document (page count from metadata), capped at ``max_pages``
          requests. When the count is unknown, stop at the first empty page.
        - ``page_count=N``: fetch the fixed window ``[start_page, start_page+N)``.

        Blank pages (common in scans) contribute nothing. When ``as_text`` the
        HTML (``<br />``, entities) is stripped to readable plain text; some
        contracts are scanned OCR and may carry artefacts from the source.
        """
        if page_count is not None:
            last: Optional[int] = start_page + max(1, int(page_count)) - 1
        else:
            total = self.get_page_count(contract_id)
            last = total if total else None

        chunks: List[str] = []
        page = start_page
        fetched = 0
        while fetched < max_pages and (last is None or page <= last):
            piece = _join_text(self.get_text_page(contract_id, page=page))
            if piece:
                chunks.append(piece)
            elif last is None:
                # Unknown total: an empty page marks the end. (With a known
                # total, pages can be legitimately blank, so keep going.)
                break
            page += 1
            fetched += 1
        html = "\n".join(chunks)
        return html_to_text(html) if as_text else html

    def get_annotations(self, contract_id: Any, *, page: Optional[int] = None) -> Dict[str, Any]:
        """Human-curated key-clause annotations (``/contract/{id}/annotations``).

        Returns ``{"total", "result": [{category, category_key,
        article_reference, page_no, text, quote, cluster, ...}]}``. Annotations
        are the platform's expert extractions of key terms (arbitration,
        governing law, stabilization, environmental/local-content obligations,
        term, signature date, ...) — the highest-value signal for legal review.

        ``page`` filters to annotations located on a given **PDF page**; omit it
        (the default) to return **all** annotations for the contract.
        """
        params = {"page": int(page)} if page is not None else None
        return self._get_json(f"/contract/{_cid(contract_id)}/annotations", params)

    # ------------------------------------------------------------------ lookups
    def list_countries(self) -> List[Dict[str, Any]]:
        """Countries with contract counts (``/contract/countries``)."""
        return (self._get_json("/contract/countries") or {}).get("results") or []

    def list_resources(self) -> List[Dict[str, Any]]:
        """Resources (commodities) with contract counts (``/contract/resources``)."""
        return (self._get_json("/contract/resources") or {}).get("results") or []

    def list_years(self) -> List[Dict[str, Any]]:
        """Signature years with contract counts (``/contract/years``)."""
        return (self._get_json("/contract/years") or {}).get("results") or []

    def list_annotation_categories(self) -> List[str]:
        """Annotation category taxonomy (``/contracts/annotations/category``)."""
        return (self._get_json("/contracts/annotations/category") or {}).get("results") or []


# ------------------------------------------------------------------- helpers
def _bool01(value: Optional[bool]) -> Optional[int]:
    """Map an optional bool to the API's 1/None convention (omit when False/None)."""
    return 1 if value else None


def _cid(contract_id: Any) -> int:
    """Coerce a contract id to int, rejecting junk early with a clear error."""
    try:
        return int(contract_id)
    except (TypeError, ValueError) as exc:
        raise ResourceContractsError(f"invalid contract id: {contract_id!r}") from exc


def _join_text(text_page: Dict[str, Any]) -> str:
    """Concatenate the ``text`` fields of one ``/text`` page response."""
    return "\n".join(
        rec.get("text", "") for rec in (text_page.get("result") or []) if rec.get("text")
    )
