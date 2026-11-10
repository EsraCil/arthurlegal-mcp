"""Dependency-free client for Azerbaijan's official legislation API (e-qanun.az).

Wraps the public, unauthenticated REST backend at ``https://api.e-qanun.az`` plus
the Referer-guarded static full-text host at ``https://e-qanun.az``. Uses only the
Python standard library so it runs on a stock Python 3.9+ with no pip installs.

API surface was reconstructed by black-box observation of the official frontend
(see ``README.md``); no authentication is used and the client identifies itself by
name rather than impersonating a browser. All content is public legal acts
published by the Ministry of Justice.

Example
-------
    from eqanun import EqanunClient

    c = EqanunClient()
    hits = c.search("mülki məcəllə", scope="title", status="in_force", length=5)
    print(hits["total"], "results")
    act = c.get_act(hits["results"][0]["id"])
    text = c.get_act_fulltext(act["id"])
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Union

from ._gate import CACHE, GATE, ByteCache, Gate, GateRefused
from ._html import decode_html, html_to_text

API_BASE = "https://api.e-qanun.az"
SITE_BASE = "https://e-qanun.az"

__version__ = "0.1.0"

# The static full-text host runs an nginx hotlink guard that requires a same-site
# Referer: a bare GET returns 403, a GET with `Referer: https://e-qanun.az/`
# returns 200. A browser-looking User-Agent is *not* required (verified: an
# identifying UA + Referer -> 200; a full Chrome UA with no Referer -> 403), so
# this client identifies itself honestly instead of impersonating a browser.
# Override with EQANUN_USER_AGENT or the `user_agent=` argument.
_DEFAULT_UA = f"eqanun-api/{__version__} (+https://github.com/beerbottle90/arthurlegal-mcp)"

# Confirmed status filter values (map to the site's Qüvvədədir / Ləğv / Bütün).
STATUS = {"all": 0, "in_force": 1, "cancelled": 2}


class EqanunError(RuntimeError):
    """Raised for transport errors or non-2xx API responses."""


def _timed_out(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError) \
        or "timed out" in str(reason).lower()


def _retry_after(exc: urllib.error.HTTPError) -> Optional[float]:
    try:
        return float((exc.headers or {}).get("Retry-After") or "")
    except (TypeError, ValueError):
        return None


class EqanunClient:
    """Thin, polite client over the e-qanun.az public API."""

    def __init__(
        self,
        *,
        user_agent: Optional[str] = None,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        retry_backoff: float = 1.5,
        gate: Optional[Gate] = None,
        cache: Union[bool, ByteCache] = True,
        proxy: Optional[str] = None,
    ) -> None:
        # Every client shares one gate unless a test hands it its own: the budget
        # is per machine, not per client object.
        self._gate = gate or GATE
        self._cache = cache if isinstance(cache, ByteCache) else (CACHE if cache else None)
        # From Fly's Amsterdam region e-qanun.az does not answer; from Frankfurt it
        # does. The hosted endpoint therefore tunnels through az-relay
        # (EQANUN_PROXY=http://arthurlegal-az-relay.internal:8080). TLS stays end to
        # end; unset, requests go direct as before.
        self.proxy = (proxy if proxy is not None else os.environ.get("EQANUN_PROXY", "")).strip()
        self._opener = (urllib.request.build_opener(urllib.request.ProxyHandler(
            {"https": self.proxy, "http": self.proxy})) if self.proxy else None)
        # Precedence: explicit argument > EQANUN_USER_AGENT > library default.
        # Reading the env var here means the CLI and the MCP server inherit it
        # without either of them needing a flag of its own.
        self.user_agent = user_agent or os.environ.get("EQANUN_USER_AGENT") or _DEFAULT_UA
        # 30 s and two retries meant about 95 s before a dead upstream was reported:
        # longer than an MCP client waits, so the caller saw a bare "operation timed
        # out" and never the reason (hosted endpoint, 2026-10-09). Same precedence
        # as the User-Agent: argument > EQANUN_TIMEOUT / EQANUN_RETRIES > default.
        self.timeout = timeout if timeout is not None else float(os.environ.get("EQANUN_TIMEOUT", "12"))
        self.retries = retries if retries is not None else int(os.environ.get("EQANUN_RETRIES", "1"))
        self.retry_backoff = retry_backoff

    # ---------------------------------------------------------------- transport
    def _request(self, url: str, *, referer: Optional[str] = None) -> bytes:
        if self._cache is not None:
            cached = self._cache.get(url)
            if cached is not None:
                return cached     # costs e-qanun.az nothing
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
        }
        if referer:
            headers["Referer"] = referer

        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                send = self._opener.open if self._opener else urllib.request.urlopen
                with self._gate:
                    with send(req, timeout=self.timeout) as resp:
                        body = resp.read()
                self._gate.ok()
                if self._cache is not None:
                    self._cache.put(url, body)
                return body
            except GateRefused as exc:
                raise EqanunError(str(exc)) from exc
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503):
                    self._gate.throttled(_retry_after(exc))
                    raise EqanunError(f"e-qanun.az asked to slow down (HTTP {exc.code}); "
                                      f"requests are paused") from exc
                if exc.code in (401, 403):
                    self._gate.refused()
                    raise EqanunError(f"HTTP {exc.code} for {url}: access refused; requests "
                                      f"are paused for an hour") from exc
                # Other 4xx are deterministic; do not retry those.
                if 400 <= exc.code < 500:
                    raise EqanunError(f"HTTP {exc.code} for {url}") from exc
                self._gate.failed()
                last_exc = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                self._gate.failed()
                last_exc = exc
                if _timed_out(exc):
                    # A host that silently drops the connection does not answer a
                    # second ask either; waiting it out again only hides the reason.
                    raise EqanunError(
                        f"e-qanun.az did not answer within {self.timeout:g} s ({url})"
                    ) from exc
            if attempt < self.retries:
                time.sleep(self.retry_backoff * (attempt + 1))
        raise EqanunError(f"request failed for {url}: {last_exc}") from last_exc

    def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{API_BASE}{path}"
        if params:
            # doseq keeps repeated keys (e.g. categories=0&categories=1).
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        raw = self._request(url, referer=f"{SITE_BASE}/")
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise EqanunError(f"invalid JSON from {url}") from exc

    # ------------------------------------------------------------------- search
    def search(
        self,
        query: str,
        *,
        scope: str = "title",
        status: str = "in_force",
        exact: bool = False,
        types: Optional[Iterable[Union[int, str]]] = None,
        start: int = 0,
        length: int = 20,
        order_column: int = 8,
        order_direction: str = "desc",
    ) -> Dict[str, Any]:
        """Search legal acts by word.

        Parameters
        ----------
        query : the search term(s).
        scope : ``"title"`` searches act titles, ``"text"`` searches full text.
        status : one of ``"in_force"``, ``"cancelled"``, ``"all"``.
        exact : exact-match toggle.
        types : optional act-type ids from :meth:`list_types` — restricts the
            search to those types. ``None`` (default) means no type filter.
            Example: ``types=[73]`` returns only Constitutional Court decisions.
        start / length : pagination (offset, page size).

        Returns a dict ``{"total": int, "start": int, "length": int,
        "results": [act, ...]}`` where each act has id, title, citation,
        typeName, statusName, acceptDate, registerCode, ...

        Note: ``length``, ``order_column`` and the ``array`` parameter this
        builds from ``types`` are mandatory upstream — omitting any of the three
        makes ``/getDetailSearch`` return HTTP 500.
        """
        if scope not in ("title", "text"):
            raise ValueError("scope must be 'title' or 'text'")
        if status not in STATUS:
            raise ValueError(f"status must be one of {sorted(STATUS)}")

        if types is None:
            array = ""
        else:
            try:
                ids = [int(t) for t in types]
            except (TypeError, ValueError) as exc:
                raise ValueError("types must be an iterable of act-type ids (int)") from exc
            array = ",".join(str(i) for i in ids)

        params = {
            "start": start,
            "length": length,
            "orderColumn": order_column,
            "orderDirection": order_direction,
            "name": query,
            "title": "true" if scope == "title" else "false",
            "text": "true" if scope == "text" else "false",
            "exact": "true" if exact else "false",
            "statusId": STATUS[status],
            "codeType": 1,
            "dateType": 1,
            "secondType": 2,
            "specialDate": "false",
            "array": array,
        }
        data = self._get_json("/getDetailSearch", params)
        results = data.get("data") or []
        return {
            "total": data.get("totalCount"),
            "start": start,
            "length": length,
            "results": results,
        }

    def count(
        self,
        query: str,
        *,
        scope: str = "title",
        status: str = "in_force",
        exact: bool = False,
    ) -> int:
        """Return only the number of acts matching a query (cheap).

        There is no ``types`` filter here: ``/detailNameCount`` ignores the
        ``array`` parameter (verified — the count is identical with and without
        it). For a type-filtered count use ``search(..., types=[...], length=1)``
        and read ``["total"]``.
        """
        if scope not in ("title", "text"):
            raise ValueError("scope must be 'title' or 'text'")
        if status not in STATUS:
            raise ValueError(f"status must be one of {sorted(STATUS)}")

        params = {
            "name": query,
            "title": "true" if scope == "title" else "false",
            "text": "true" if scope == "text" else "false",
            "statusId": STATUS[status],
            "exact": "true" if exact else "false",
        }
        data = self._get_json("/detailNameCount", params)
        # This endpoint returns the count in the `id` field.
        n = data.get("id")
        if not isinstance(n, int):
            # Never return None silently — a missing count would otherwise be
            # printed as `null` and could be quoted as a corpus figure.
            raise EqanunError(f"unexpected /detailNameCount response: {data!r}")
        return n

    # -------------------------------------------------------------------- acts
    def get_act(self, act_id: int) -> Dict[str, Any]:
        """Return metadata for one act, flattened.

        Includes ``htmlUrl`` (canonical full-text location) and the requisite
        fields (title, acceptDate, effectDate, typeName, statusName, classCodes).
        """
        data = self._get_json(f"/framework/{int(act_id)}")
        payload = data.get("data") or {}
        req = payload.get("requisite") or {}
        return {
            "id": payload.get("id"),
            "htmlUrl": payload.get("htmlUrl"),
            "title": req.get("title"),
            "acceptDate": req.get("acceptDate"),
            "effectDate": req.get("effectDate"),
            "typeName": req.get("typeName"),
            "statusName": req.get("statusName"),
            "citation": req.get("citation"),
            "registerCode": req.get("registerCode"),
            "registerDate": req.get("registerDate"),
            "classCodes": req.get("classCodes"),
            "_raw": payload,
        }

    def get_act_fulltext(self, act_id: int, *, as_text: bool = True) -> str:
        """Fetch the full text of an act.

        Resolves the act's ``htmlUrl`` from metadata, then fetches that static
        HTML with the same-site ``Referer`` the host's hotlink guard requires.
        By default the HTML is converted to clean plain text; pass
        ``as_text=False`` for raw HTML.
        """
        meta = self.get_act(act_id)
        html_url = meta.get("htmlUrl")
        if not html_url:
            raise EqanunError(f"act {act_id} has no htmlUrl")
        raw = self._request(html_url, referer=f"{SITE_BASE}/")
        html = decode_html(raw)
        return html_to_text(html) if as_text else html

    # ------------------------------------------------------------------ lookups
    def list_types(self) -> List[Dict[str, Any]]:
        """Return the act-type taxonomy tree (id, name w/ counts, parentId)."""
        data = self._get_json("/all-type")
        return data.get("data") or []

    def list_sections(self) -> List[Dict[str, Any]]:
        """Return the four top-level sections (normative / non-normative / ...)."""
        data = self._get_json("/sections")
        return data.get("data") or []
