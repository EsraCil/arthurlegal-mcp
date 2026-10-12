"""BDDK — Bankacılık Düzenleme ve Denetleme Kurulu kararları (bddk.org.tr).

Two server-rendered lists hold every published decision:

    /Mevzuat/Liste/55   Resmî Gazete'de yayımlanan Kurul kararları   (~1.000 links)
    /Mevzuat/Liste/56   Resmî Gazete'de yayımlanmayan Kurul kararları (~900 links)

Each entry is ``/Mevzuat/DokumanGetir/<id>`` with a title of the form
``(06.08.2026 - 11548) <konu>`` — date and decision number up front. The
reference implementation reaches these through the Tavily search API; this one
parses the two lists directly (no key) and can index them locally.

Citation contract: ``BDDK, 06.08.2026 tarih ve 11548 sayılı Kurul Kararı``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from net import Http, HttpError
from textx import html_to_text, iso_tarih, paginate, pdf_to_text, strip_tags, count_hits, excerpt
from sources import Source

BASE = "https://www.bddk.org.tr"
LISTS = {"rg": "/Mevzuat/Liste/55", "diger": "/Mevzuat/Liste/56"}
_http = Http(BASE, {"Accept": "text/html,application/pdf,*/*"})
_cache: Dict[str, List[Dict[str, Any]]] = {}
# Titles use a hyphen or an en dash between date and number: "(06.08.2026 - 11548) …" / "(23.05.2024 – 10914) …",
# some put a space before the closing parenthesis, and older ones use slashes: "(13.12.2018 - 8149 ) …",
# "(26/07/2007 - 2248) …" (17 decisions were left undated).
_HEAD = re.compile(r"^\((\d{1,2}[./]\d{1,2}[./]\d{4})\s*[-–—]\s*(\d+)\s*\)\s*(.*)$")


def _list(kind: str) -> List[Dict[str, Any]]:
    if kind in _cache:
        return _cache[kind]
    html = _http.get_text(LISTS[kind])
    items: List[Dict[str, Any]] = []
    seen = set()
    for m in re.finditer(r'<a[^>]*href="(/Mevzuat/DokumanGetir/(\d+))"[^>]*>(.*?)</a>', html, re.S):
        href, did, inner = m.groups()
        title = strip_tags(inner)
        if not title or did in seen:
            continue
        seen.add(did)
        hm = _HEAD.match(title)
        date, no, subject = (hm.group(1), hm.group(2), hm.group(3)) if hm else ("", "", title)
        items.append({"id": did, "date": date, "decision_no": no, "title": subject.strip(), "list": kind,
                      "citation": "BDDK, %s tarih ve %s sayılı Kurul Kararı" % (date or "?", no or "?"),
                      "source_url": BASE + href})
    _cache[kind] = items
    return items


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    q = (args.get("query") or "").strip()
    kinds = [args["list"]] if args.get("list") in LISTS else list(LISTS)
    hits: List[Dict[str, Any]] = []
    try:
        for k in kinds:
            for it in _list(k):
                hay = "%s %s %s" % (it["title"], it["decision_no"], it["date"])
                terms = q.split()
                if not q or all(count_hits(hay, t) for t in terms):
                    hits.append(it)
    except HttpError as exc:
        return {"error": str(exc)}
    if args.get("year"):
        hits = [h for h in hits if h["date"].endswith(str(args["year"]))]
    limit = max(1, min(int(args.get("limit") or 20), 100))
    return {"query": q, "total": len(hits), "results": hits[:limit],
            "note": "Başlık araması. Karar metni: kurum_karari_getir(kurum='bddk', id=id)."}


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    did = str(args.get("id") or "").strip()
    if not did.isdigit():
        return {"error": "id sayısal DokumanGetir kimliği olmalı."}
    url = "%s/Mevzuat/DokumanGetir/%s" % (BASE, did)
    try:
        body, hdrs, final = _http.get(url, timeout=120)
    except HttpError as exc:
        return {"error": str(exc)}
    ctype = (hdrs.get("Content-Type") or "").lower()
    if "pdf" in ctype or body.startswith(b"%PDF"):
        text, pages = pdf_to_text(body)
    else:
        text, pages = html_to_text(body.decode("utf-8", "replace")), 0
    out = paginate(text, args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update({"id": did, "source_url": url, "pdf_pages": pages})
    if not text:
        out["error"] = "Metin çıkarılamadı (taranmış PDF olabilir)."
    return out


def crawl(index, fetch_text: bool = False, limit: int = 0, log=print) -> Dict[str, Any]:
    n = 0
    for k in LISTS:
        for it in _list(k):
            if limit and n >= limit:
                break
            if getattr(index, "exists", lambda r: False)("bddk:%s" % it["id"]):
                continue
            body = it["title"]
            if fetch_text:
                g = get({"id": it["id"], "page_chars": 200000})
                body = g.get("text") or body
            index.upsert({"ref": "bddk:%s" % it["id"], "title": it["title"], "body": body, "url": it["source_url"],
                          "lang": "tr", "date": _iso(it["date"]), "status": "RG" if k == "rg" else "RG dışı",
                          "court": "BDDK", "subject": "bankacılık", "citation": it["citation"],
                          "meta": {"kurum": "bddk", "decision_no": it["decision_no"]}})
            n += 1
        log("bddk: list %s, %d docs" % (k, n))
    return {"indexed": n}


def _iso(d: str) -> str:
    return iso_tarih(d)


SEARCH_SCHEMA = {"type": "object", "properties": {
    "query": {"type": "string", "description": "Karar başlığında aranacak kelimeler (hepsi geçmeli)"},
    "list": {"type": "string", "enum": list(LISTS), "description": "rg = RG'de yayımlanan, diger = yayımlanmayan"},
    "year": {"type": "integer"}, "limit": {"type": "integer", "default": 20}}}
GET_SCHEMA = {"type": "object", "properties": {"id": {"type": "string"}, "page": {"type": "integer", "default": 1},
                                               "page_chars": {"type": "integer", "default": 8000}},
              "required": ["id"]}

SOURCE = Source(
    key="bddk", label="BDDK — Bankacılık Düzenleme ve Denetleme Kurulu kararları", kind="kurum",
    notes="~1.900 Kurul kararı iki listede. Canlı arama BAŞLIKTA; karar metnine dair soru için yerel indeks: "
          "semantik_ara(kurum='bddk') (metinler 2026-09-20'de eklendi).",
    search=search, get=get, crawl=crawl, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage=BASE + "/Mevzuat/Liste/55",
)
