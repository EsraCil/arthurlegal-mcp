"""BTK — Bilgi Teknolojileri ve İletişim Kurumu Kurul kararları (btk.tr headless CMS).

    GET https://www.btk.tr/api/content/board-decisions?page=1&limit=20&locale=tr
        &search=<q>&filter[decision_no]=…&filter[decision_date]=YYYY-MM-DD
        &date_from=…&date_to=…&filter[relevant_unit]=…

Each item carries ``data.file_url.url`` (PDF on btk.gov.tr S3), ``decision_no``
(e.g. ``2026/İK-THD/186``), ``decision_date`` and ``relevant_unit``.

Citation contract: ``BTK, 04.08.2026 tarih ve 2026/İK-THD/186 sayılı Kurul Kararı``.

Endpoint from saidsurucu/yargi-mcp (MIT).
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List

from net import Http, HttpError
from textx import paginate, pdf_to_text
from sources import Source

BASE = "https://www.btk.tr"
_http = Http(BASE, {"Accept": "application/json"}, allowed_domains=["btk.gov.tr"])  # PDF'ler btk.gov.tr'de


def _item(it: Dict[str, Any]) -> Dict[str, Any]:
    d = it.get("data") or {}
    f = d.get("file_url") if isinstance(d.get("file_url"), dict) else {}
    date = _gun(d.get("decision_date"))
    no = d.get("decision_no") or ""
    return {
        "id": it.get("id"), "title": it.get("title"), "decision_no": no, "decision_date": date,
        "relevant_unit": d.get("relevant_unit"), "published_at": _gun(it.get("publishedAt")),
        "pdf_url": f.get("url") or f.get("storageUrl"),
        "citation": "BTK, %s tarih ve %s sayılı Kurul Kararı" % (_tr(date), no),
        "source_url": "%s/kurul-kararlari/%s" % (BASE, it.get("slug") or ""),
    }


def _gun(deger: Any) -> str:
    """API zaman damgasının Türkiye'deki günü (YYYY-MM-DD).

    btk.tr karar tarihini o günün Türkiye saatiyle gece yarısı olarak, UTC ile verir:
    15.09.2026 tarihli karar ``2026-09-14T21:00:00.000Z`` gelir. İlk on karakter bir gün
    erkeni gösterir; atıf (``BTK, 15.09.2026 tarih ve ... sayılı Kurul Kararı``) yanlış
    çıkar. Damgaya üç saat eklenir: yaz saati uygulanan yıllarda kış damgası 22:00Z'dir
    ve o da doğru güne düşer. Saat taşımayan değer olduğu gibi kalır.
    """
    s = str(deger or "").strip()
    if len(s) < 19 or s[10] != "T":
        return s[:10]
    try:
        t = datetime.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return s[:10]
    return (t + datetime.timedelta(hours=3)).strftime("%Y-%m-%d")


def _tr(iso: str) -> str:
    return "%s.%s.%s" % (iso[8:10], iso[5:7], iso[0:4]) if len(iso) >= 10 else iso


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    params = {"page": max(1, int(args.get("page") or 1)),
              "limit": max(1, min(int(args.get("page_size") or 20), 50)), "locale": "tr"}
    if args.get("query"):
        params["search"] = args["query"].strip()
    if args.get("decision_no"):
        params["filter[decision_no]"] = args["decision_no"].strip()
    if args.get("decision_date"):
        params["filter[decision_date]"] = args["decision_date"].strip()
    if args.get("date_from"):
        params["date_from"] = args["date_from"]
    if args.get("date_to"):
        params["date_to"] = args["date_to"]
    if args.get("unit"):
        params["filter[relevant_unit]"] = args["unit"]
    try:
        data = _http.get_json("/api/content/board-decisions", params=params)
    except HttpError as exc:
        return {"error": str(exc)}
    meta = data.get("meta") or {}
    return {"query": args.get("query") or "", "page": meta.get("page"), "total": meta.get("total"),
            "total_pages": meta.get("totalPages"),
            "results": [_item(i) for i in data.get("data") or []],
            "note": "Karar metni: kurum_karari_getir(kurum='btk', id=pdf_url)."}


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    url = str(args.get("id") or "").strip()
    if not url.startswith(("https://www.btk.gov.tr/", "https://www.btk.tr/", "https://btk.gov.tr/")):
        return {"error": "id olarak btk.gov.tr / btk.tr üzerindeki pdf_url verilmeli."}
    try:
        body, hdrs, _ = _http.get(url, timeout=120)
    except HttpError as exc:
        return {"error": str(exc)}
    text, pages = pdf_to_text(body)
    out = paginate(text, args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update({"source_url": url, "pdf_pages": pages})
    if not text:
        out["error"] = "PDF metni çıkarılamadı (taranmış belge olabilir)."
    return out


def crawl(index, max_pages: int = 10, fetch_text: bool = False, log=print) -> Dict[str, Any]:
    n = 0
    for page in range(1, max_pages + 1):
        res = search({"page": page, "page_size": 50})
        items = res.get("results") or []
        if not items:
            break
        for it in items:
            if getattr(index, "exists", lambda r: False)("btk:%s" % it["id"]):
                continue
            body = it["title"] or ""
            if fetch_text and it.get("pdf_url"):
                g = get({"id": it["pdf_url"], "page_chars": 200000})
                body = g.get("text") or body
            index.upsert({"ref": "btk:%s" % it["id"], "title": it["title"] or "", "body": body,
                          "url": it.get("pdf_url") or it["source_url"], "lang": "tr",
                          "date": it["decision_date"], "status": it.get("relevant_unit") or "",
                          "court": "BTK", "subject": "telekomünikasyon", "citation": it["citation"],
                          "meta": {"kurum": "btk", "decision_no": it["decision_no"]}})
            n += 1
        log("btk: page %d, %d docs" % (page, n))
        if page >= int(res.get("total_pages") or 1):
            break
    return {"indexed": n}


SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "decision_no": {"type": "string", "description": "Örn. 2026/İK-THD/186"},
        "decision_date": {"type": "string", "description": "YYYY-MM-DD"},
        "date_from": {"type": "string"}, "date_to": {"type": "string"},
        "unit": {"type": "string", "description": "İlgili birim adı"},
        "page": {"type": "integer", "default": 1}, "page_size": {"type": "integer", "default": 20},
    },
}
GET_SCHEMA = {"type": "object", "properties": {"id": {"type": "string", "description": "pdf_url"},
                                               "page": {"type": "integer", "default": 1},
                                               "page_chars": {"type": "integer", "default": 8000}},
              "required": ["id"]}

SOURCE = Source(
    key="btk", label="BTK — Bilgi Teknolojileri ve İletişim Kurumu Kurul kararları", kind="kurum",
    notes="Karar başlığı listede, gerekçe PDF'te; canlı arama BAŞLIKTA. Karar metnine dair soru için yerel indeks: "
          "semantik_ara(kurum='btk') (metinler 2026-09-20'de eklendi). id = pdf_url.",
    search=search, get=get, crawl=crawl, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage=BASE + "/kurul-kararlari",
)
