"""KVKK — Kişisel Verileri Koruma Kurulu karar özetleri (kvkk.gov.tr).

The reference implementation searches KVKK through the Brave web-search API,
i.e. with a paid key. This adapter does not: the Kurul Karar Özetleri listing
(``/Icerik/5406/kurul-karar-ozetleri?page=N``, 36 pages, ten per page) is
plain server-rendered HTML with links of the form ``/Icerik/<id>/<yyyy>-<no>``.
It is crawled into the local index (title + summary + full text) and searched
there, hybrid and — with an embeddings backend — semantically. ``search`` with
no local index falls back to scanning the listing pages live, which is slower
and keyword-only.

A decision's address needs its slug: ``/Icerik/8887/2026-1183``. The site sends
``/Icerik/8887/x`` to ``/error`` and ``/Icerik/8887`` to the home page, and until
2026-10-10 the full text was fetched from the former: all 291 indexed decisions
held the same 4,672 characters of site menu, and ``kurum_karari_getir`` returned
that menu. The text is now read from the article block only, and a page without
one is reported as an error instead of being stored as a decision.

Citation contract: ``KVK Kurulu, 27.02.2024 tarih ve 2024/347 sayılı karar özeti``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from net import Http, HttpError, TokenBucket
from textx import html_to_text, iso_tarih, paginate, strip_tags, count_hits, excerpt
from sources import Source

BASE = "https://www.kvkk.gov.tr"
LIST = "/Icerik/5406/kurul-karar-ozetleri"
# At most one request a second: a full crawl reads ~36 listing pages and ~360 decisions.
_http = Http(BASE, {"Accept": "text/html,*/*"}, bucket=TokenBucket(capacity=1, refill_s=1.0))
_LINK = re.compile(r'<a[^>]*href="(?:https://www\.kvkk\.gov\.tr)?(/Icerik/(\d+)/(\d{4}-\d+))"', re.I)
# The decision itself; what follows it is the sidebar and the footer.
_ARTICLE = re.compile(r'<div[^>]*class="news__detail-article(?:\s[^"]*)?"[^>]*>', re.I)
_AFTER_ARTICLE = re.compile(r'<(?:div|aside|footer)[^>]*class="[^"]*(?:sidebar-widget|footer)', re.I)
# The heading is a table; html_to_text joins its cells with " | ": "Karar No | | : | | 2026/1183".
_CELLS = r"[\s|]*:?[\s|]*"
_DATE = re.compile(r"Karar Tarihi" + _CELLS + r"(\d{2}[./]\d{2}[./]\d{4})")
_NO = re.compile(r"Karar No" + _CELLS + r"(\d{4}/\d+)")
_KONU = re.compile(r"Konu Özeti" + _CELLS + r"([^\n|][^\n]*)")
_SLUGGED = re.compile(r"(\d+)/(\d{4}-\d+)")


def _listing(page: int) -> List[Dict[str, Any]]:
    html = _http.get_text(LIST, params={"page": page})
    items: List[Dict[str, Any]] = []
    seen = set()
    # Each card: a block containing the decision title text and the "Devamını Gör" link.
    for block in re.split(r'(?=<div[^>]*class="[^"]*(?:card|item|karar|list)[^"]*")', html):
        m = _LINK.search(block)
        if not m:
            continue
        href, cid, no = m.groups()
        if cid in seen:
            continue
        seen.add(cid)
        txt = strip_tags(block)
        txt = re.sub(r"\bDevamını Gör\b", "", txt).strip()
        dm = re.search(r"(\d{2}[./]\d{2}[./]\d{4})", txt)
        items.append({"id": cid, "slug": no, "decision_no": no.replace("-", "/"), "date": dm.group(1) if dm else "",
                      "summary": txt[:700], "source_url": BASE + href})
    if not items:  # fallback: bare links
        for m in _LINK.finditer(html):
            href, cid, no = m.groups()
            if cid not in seen:
                seen.add(cid)
                items.append({"id": cid, "slug": no, "decision_no": no.replace("-", "/"), "date": "", "summary": "",
                              "source_url": BASE + href})
    for it in items:
        it["citation"] = "KVK Kurulu, %s tarih ve %s sayılı karar özeti" % (it["date"] or "?", it["decision_no"])
    return items


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    q = (args.get("query") or "").strip()
    max_pages = max(1, min(int(args.get("scan_pages") or 3), 10))
    hits: List[Dict[str, Any]] = []
    scanned = 0
    try:
        for p in range(1, max_pages + 1):
            for it in _listing(p):
                scanned += 1
                if not q or count_hits(it["summary"] + " " + it["decision_no"], q) or all(
                        count_hits(it["summary"], t) for t in q.split()):
                    # The id carries the slug, so kurum_karari_getir can open the page as given.
                    hits.append({**it, "id": "%s/%s" % (it["id"], it["slug"]),
                                 "excerpt": excerpt(it["summary"], q.split()[0] if q else "", 200)})
    except HttpError as exc:
        return {"error": str(exc)}
    return {"query": q, "scanned": scanned, "total": len(hits), "results": hits[: int(args.get("limit") or 20)],
            "note": "Canlı tarama son %d liste sayfası (≈%d özet) ile sınırlıdır; tüm arşiv için yerel "
                    "indeks (semantik_ara, kurum='kvkk'). Tam metin: kurum_karari_getir(kurum='kvkk', id=id)." % (max_pages, max_pages * 10)}


def _url(ident: str, scan_pages: int = 5) -> Optional[str]:
    """The decision page for an id: a URL, ``8887/2026-1183`` or ``8887``.

    A bare number has no slug, and the site cannot be asked without one, so it is
    looked up on the newest listing pages (the server first tries its own index).
    """
    if ident.startswith("http"):
        return ident
    m = _SLUGGED.fullmatch(ident)
    if m:
        return "%s/Icerik/%s/%s" % (BASE, m.group(1), m.group(2))
    if ident.isdigit():
        for p in range(1, scan_pages + 1):
            for it in _listing(p):
                if it["id"] == ident:
                    return it["source_url"]
    return None


def _parse(html: str) -> Optional[Dict[str, str]]:
    """The decision's text and heading fields, or None for a page without a decision
    (the error page and the home page the site redirects wrong addresses to)."""
    m = _ARTICLE.search(html)
    if not m:
        return None
    end = _AFTER_ARTICLE.search(html, m.end())
    text = html_to_text(html[m.end(): end.start() if end else len(html)]).strip()
    if not text:
        return None
    date, no, konu = _DATE.search(text), _NO.search(text), _KONU.search(text)
    return {"text": text, "date": date.group(1) if date else "", "no": no.group(1) if no else "",
            "konu": konu.group(1).strip() if konu else ""}


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    ident = str(args.get("id") or "").strip()
    if not ident:
        return {"error": "id gerekli."}
    try:
        url = _url(ident)
    except HttpError as exc:
        return {"error": str(exc)}
    if not url:
        return {"error": "KVKK kararının sayfası bulunamadı (%s). Sayfa adresi karar numarasını da taşır "
                         "(/Icerik/8887/2026-1183): kurum_karari_ara ya da semantik_ara sonucundaki id'yi "
                         "veya adresi verin." % ident}
    try:
        html = _http.get_text(url)
    except HttpError as exc:
        return {"error": str(exc), "source_url": url}
    doc = _parse(html)
    if doc is None:
        return {"error": "KVKK sayfası karar metni döndürmedi (yanlış adres ya da sayfa yapısı değişti).",
                "source_url": url}
    out = paginate(doc["text"], args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update({"id": ident, "source_url": url, "karar_tarihi": doc["date"], "karar_no": doc["no"],
                "konu": doc["konu"]})
    return out


def crawl(index, max_pages: int = 40, fetch_text: bool = True, log=print) -> Dict[str, Any]:
    n = metinsiz = 0
    for p in range(1, max_pages + 1):
        try:
            items = _listing(p)
        except HttpError as exc:
            log("kvkk: page %d failed: %s" % (p, exc))
            break
        if not items:
            break
        for it in items:
            if getattr(index, "exists", lambda r: False)("kvkk:%s" % it["id"]):
                continue
            body, title, date = it["summary"], "KVKK Kurul Kararı %s" % it["decision_no"], it["date"]
            if fetch_text:
                g = get({"id": it["source_url"], "page_chars": 200000})
                if g.get("text"):
                    body = g["text"]
                    if g.get("konu"):
                        # The subject line is what a reader searches for; the number alone is not.
                        title = "%s (KVKK Kurul Kararı %s)" % (g["konu"], it["decision_no"])
                    date = date or g.get("karar_tarihi") or ""
                else:
                    metinsiz += 1
                    log("kvkk: %s metin alınamadı, liste özeti kalır: %s" % (it["source_url"], g.get("error")))
            index.upsert({"ref": "kvkk:%s" % it["id"], "title": title,
                          "body": body, "url": it["source_url"], "lang": "tr", "date": _iso(date),
                          "status": "karar özeti", "court": "KVK Kurulu", "subject": "kişisel veriler",
                          "citation": "KVK Kurulu, %s tarih ve %s sayılı karar özeti" % (
                              date.replace("/", ".") or "?", it["decision_no"]),
                          "meta": {"kurum": "kvkk", "decision_no": it["decision_no"]}})
            n += 1
        log("kvkk: page %d, %d docs" % (p, n))
    return {"indexed": n, "metinsiz": metinsiz}


def _iso(d: str) -> str:
    return iso_tarih(d)


SEARCH_SCHEMA = {"type": "object", "properties": {
    "query": {"type": "string"}, "scan_pages": {"type": "integer", "default": 3, "maximum": 10},
    "limit": {"type": "integer", "default": 20}}}
GET_SCHEMA = {"type": "object", "properties": {"id": {"type": "string", "description": "İçerik id veya tam URL"},
                                               "page": {"type": "integer", "default": 1},
                                               "page_chars": {"type": "integer", "default": 8000}},
              "required": ["id"]}

SOURCE = Source(
    key="kvkk", label="KVKK — Kişisel Verileri Koruma Kurulu karar özetleri", kind="kurum",
    notes="Kurul karar özetleri (~360). Canlı arama son sayfaları tarar; arşiv için yerel indeks + semantik_ara.",
    search=search, get=get, crawl=crawl, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage=BASE + LIST,
)
