"""Uyuşmazlık Mahkemesi kararları — kararlar.uyusmazlik.gov.tr (ASP.NET WebForms).

The site is a classic postback form: GET the landing page for ``__VIEWSTATE`` &
friends, POST them back with ``txtSearch``; later pages are ``__EVENTTARGET=
GridView1`` / ``__EVENTARGUMENT=Page$N``. Results are PDF links.

TLS: verified like every other upstream. The chain once failed on a stock Python
trust store and verification was off here; it verifies since (checked on the
hosted machines 2026-11-03).

Citation contract: ``Uyuşmazlık Mahkemesi, E. 2023/12, K. 2023/45, 06.03.2023``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List
from urllib.parse import urljoin

from net import Http, HttpError
from textx import html_to_text, paginate, pdf_to_text, strip_tags
from sources import Source

BASE = "https://kararlar.uyusmazlik.gov.tr"
_http = Http(BASE, {"Accept": "text/html,*/*", "Origin": BASE, "Referer": BASE + "/"})
_HIDDEN = ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION", "__EVENTTARGET",
           "__EVENTARGUMENT", "__LASTFOCUS")


def _hidden(html: str) -> Dict[str, str]:
    out = {}
    for name in _HIDDEN:
        m = re.search(r'name="%s"[^>]*value="([^"]*)"' % re.escape(name), html)
        out[name] = m.group(1) if m else ""
    return out


def _parse(html: str) -> List[Dict[str, Any]]:
    m = re.search(r'<table[^>]*id="GridView1"[^>]*>(.*?)</table>', html, re.S)
    if not m:
        return []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S)
    out = []
    for row in rows[1:]:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 4:
            continue
        link = re.search(r"""href=["']([^"']+)["']""", cells[3])
        if not link or link.group(1).lower().startswith("javascript"):
            continue
        href = link.group(1)
        if "uploads" not in href.lower() and not href.lower().endswith(".pdf"):
            continue
        esas, karar, tarih = (strip_tags(c) for c in cells[:3])
        out.append({"esas_no": esas, "karar_no": karar, "karar_tarihi": tarih,
                    "document_url": urljoin(BASE + "/", href),
                    "citation": "Uyuşmazlık Mahkemesi, E. %s, K. %s, %s" % (esas, karar, tarih)})
    return out


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    q = (args.get("query") or "").strip()
    if not q:
        return {"error": "query gerekli."}
    page = max(1, int(args.get("page") or 1))
    try:
        landing = _http.get_text("/")
        form = _hidden(landing)
        scope = args.get("scope") or "All"
        form.update({"txtSearch": q, "rblSearchScope": scope, "btnSearch": "Ara"})
        html, _, _ = _http.post_form("/", form)
        if page > 1:
            f2 = _hidden(html)
            f2.update({"txtSearch": q, "rblSearchScope": scope,
                       "__EVENTTARGET": "GridView1", "__EVENTARGUMENT": "Page$%d" % page})
            html, _, _ = _http.post_form("/", f2)
    except HttpError as exc:
        return {"error": str(exc)}
    items = _parse(html)
    m = re.search(r"(\d+)\s*(?:adet\s*)?(?:kayıt|sonuç|karar)\b", html, re.I)
    pages = len(re.findall(r"Page\$\d+", html))
    return {"query": q, "page": page, "total": int(m.group(1)) if m else None,
            "more_pages": pages > 0, "results": items,
            "note": "Metin: uyusmazlik_getir(document_url). Sayfalama: page=2,3…"}


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    url = str(args.get("document_url") or "").strip()
    if not url.startswith(BASE):
        return {"error": "document_url kararlar.uyusmazlik.gov.tr adresinde olmalı."}
    try:
        body, hdrs, _ = _http.get(url)
    except HttpError as exc:
        return {"error": str(exc)}
    ctype = (hdrs.get("Content-Type") or "").lower()
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        text, _ = pdf_to_text(body)
    else:
        text = html_to_text(body.decode("utf-8", "replace"))
    out = paginate(text, args.get("page") or 1, 8000)
    out["source_url"] = url
    return out


SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "scope": {"type": "string", "enum": ["All", "EsasNo", "KararNo"], "default": "All",
                  "description": "All = metin içinde; EsasNo / KararNo = numara ile"},
        "page": {"type": "integer", "minimum": 1, "default": 1},
    },
    "required": ["query"],
}
GET_SCHEMA = {"type": "object", "properties": {"document_url": {"type": "string"},
                                               "page": {"type": "integer", "default": 1}},
              "required": ["document_url"]}

SOURCE = Source(
    key="uyusmazlik", label="Uyuşmazlık Mahkemesi", kind="ictihat",
    notes="Adli–idari yargı görev/hüküm uyuşmazlıkları. Sonuçlar PDF; metin için uyusmazlik_getir.",
    search=search, get=get, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage=BASE,
)
