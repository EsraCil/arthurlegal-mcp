"""EPDK — Enerji Piyasası Düzenleme Kurulu kararları (epdk.gov.tr).

There is no decision API and the portal's own search is behind reCAPTCHA v3.
What exists — verified 2026-09 — is the *Kurul Kararları* tree per market:

    GET  /Detay/Icerik/3-0-39-3/son-kurul-kararlari/elektrik      (and dogalgaz, petrol, lpg, denetim)
         → accordion of categories, each ``<a data-id="N" onclick="ShowDetailList(this)">``
    POST /Detay/GetFastAccessList   {"fId": "N"}
         → {"Type": 1, "model": [{Title, Number, Date, RgDate, RgNumber, Url, IsLink,
                                   FastAccessDetail: [{ContentId, Title, …}]}]}
    GET  /Detay/DownloadDocument?id=<ContentId>                    → PDF / DOC

So a market page is a few dozen categories; each category is one POST. The
adapter walks the tree lazily, caches it in-process, and lets ``crawl`` write
every decision (subject line + optional document text) into the local index —
the only way to search EPDK decisions by subject across markets and years.

Citation contract: ``EPDK, 26.12.2024 tarihli ve 13134 sayılı Kurul Kararı (RG 29.12.2024/32767)``.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple

from net import Http, HttpError
from textx import OLE_MAGIC, doc_to_text, html_to_text, iso_tarih, paginate, pdf_to_text, strip_tags, count_hits
from sources import Source

BASE = "https://www.epdk.gov.tr"
_http = Http(BASE, {"Accept": "text/html,application/json,application/pdf,*/*"})

PAGES = {
    "elektrik": "/Detay/Icerik/3-0-39-3/son-kurul-kararlari/elektrik",
    "dogalgaz": "/Detay/Icerik/3-0-19-1007/son-kurul-kararlari/dogalgaz",
    "petrol": "/Detay/Icerik/3-0-100-1008/son-kurul-kararlari/petrol",
    "lpg": "/Detay/Icerik/3-0-101-1002/son-kurul-kararlari/lpg",
    "denetim": "/Detay/Icerik/3-0-136/denetim-kurul-kararlari",
}
_TTL = 6 * 3600
_tree: Dict[str, Any] = {}       # market -> (ts, [categories])
_lists: Dict[str, Any] = {}      # fId -> (ts, [decisions])


def _fresh(store: Dict[str, Any], key: str):
    hit = store.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    return None


def categories(market: str) -> List[Dict[str, str]]:
    """Category leaves of one market page, with their accordion path as context."""
    cached = _fresh(_tree, market)
    if cached is not None:
        return cached
    html = _http.get_text(PAGES[market], headers={"X-Requested-With": ""})
    out: List[Dict[str, str]] = []
    path: List[str] = []
    # Walk anchors in document order; parents are the collapse toggles without data-id.
    for m in re.finditer(r'<a\s+([^>]*)>(.*?)</a>', html, re.S):
        attrs, inner = m.groups()
        if "kesmebedel_" not in attrs:
            continue
        label = strip_tags(inner)
        if 'data-id="' in attrs and "ShowDetailList" in attrs:
            fid = re.search(r'data-id="(\d+)"', attrs).group(1)
            out.append({"id": fid, "label": label, "path": " › ".join(path[-2:]), "market": market})
        elif 'data-type="False"' in attrs:
            path = path[:1] + [label] if path else [label]
    _tree[market] = (time.time(), out)
    return out


def decisions(fid: str, market: str = "", category: str = "", path: str = "") -> List[Dict[str, Any]]:
    cached = _fresh(_lists, fid)
    if cached is not None:
        return cached
    data = _http.post_json("/Detay/GetFastAccessList", {"fId": fid},
                           headers={"X-Requested-With": "XMLHttpRequest", "Referer": BASE + PAGES.get(market, "/")})
    out: List[Dict[str, Any]] = []
    if isinstance(data, dict) and data.get("Type") == 1:
        for v in data.get("model") or []:
            docs = []
            for d in v.get("FastAccessDetail") or []:
                cid = d.get("ContentId")
                if cid:
                    docs.append({"title": (d.get("Title") or "").strip(),
                                 "url": "%s/Detay/DownloadDocument?id=%s" % (BASE, cid)})
            if v.get("Url"):
                docs.append({"title": "bağlantı", "url": v["Url"]})
            date, no = (v.get("Date") or "").strip(), (v.get("Number") or "").strip()
            rg_date, rg_no = (v.get("RgDate") or "").strip(), (v.get("RgNumber") or "").strip()
            title = (v.get("Title") or "").strip()
            ref = "%s:%s:%s" % (market, fid, (docs[0]["url"].split("?id=", 1)[-1] if docs else "%s-%s" % (date, no)))
            out.append({
                "id": ref, "market": market, "category": category, "path": path, "subject": title,
                "decision_no": no, "date": date, "rg_date": rg_date, "rg_no": rg_no,
                "mulga": (v.get("MulgaTitle") or "").strip(), "documents": docs,
                "citation": "EPDK, %s tarihli ve %s sayılı Kurul Kararı%s" % (
                    date or "?", no or "?", " (RG %s/%s)" % (rg_date, rg_no) if rg_date else ""),
                "source_url": docs[0]["url"] if docs else BASE + PAGES.get(market, ""),
            })
    _lists[fid] = (time.time(), out)
    return out


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    q = (args.get("query") or "").strip()
    market = (args.get("market") or "").strip()
    markets = [market] if market in PAGES else list(PAGES)
    cat_filter = (args.get("category") or "").strip()
    max_cats = max(1, min(int(args.get("max_categories") or 80), 200))
    hits: List[Dict[str, Any]] = []
    scanned = 0
    errors: Dict[str, str] = {}
    for m in markets:
        try:
            cats = categories(m)
        except HttpError as exc:
            errors[m] = str(exc)
            continue
        if cat_filter:
            cats = [c for c in cats if count_hits(c["label"] + " " + c["path"], cat_filter)]
        for c in cats[:max_cats]:
            try:
                rows = decisions(c["id"], m, c["label"], c["path"])
            except HttpError as exc:
                errors[c["id"]] = str(exc)
                continue
            scanned += 1
            for r in rows:
                hay = " ".join([r["subject"], r["category"], r["path"], r["decision_no"], r["date"], r["rg_no"]])
                if not q or all(count_hits(hay, t) for t in q.split()):
                    hits.append(r)
    if args.get("decision_no"):
        hits = [h for h in hits if str(args["decision_no"]) in h["decision_no"]]
    if args.get("year"):
        hits = [h for h in hits if h["date"].endswith(str(args["year"]))]
    hits.sort(key=lambda h: _iso(h["date"]), reverse=True)
    limit = max(1, min(int(args.get("limit") or 20), 200))
    out: Dict[str, Any] = {"query": q, "markets": markets, "categories_scanned": scanned, "total": len(hits),
                           "results": hits[:limit],
                           "note": "Konu + kategori satırında arama. Belge metni: kurum_karari_getir(kurum='epdk', id=<documents[0].url>). "
                                   "Yayım teyidi: resmi_gazete_fihrist(rg_date). İlk çağrı piyasa başına ~%d POST sürer, sonra önbellek." % 60}
    if errors:
        out["errors"] = errors
    return out


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    url = str(args.get("id") or "").strip()
    if url.startswith("/"):
        url = BASE + url
    if not url.startswith("http") and ":" in url:
        cid = url.rsplit(":", 1)[-1]
        url = "%s/Detay/DownloadDocument?id=%s" % (BASE, cid)
    if not url.startswith(BASE):
        return {"error": "id olarak EPDK belge URL'si (documents[0].url) verin."}
    try:
        body, hdrs, final = _http.get(url, timeout=120)
    except HttpError as exc:
        return {"error": str(exc)}
    ctype = (hdrs.get("Content-Type") or "").lower()
    text, pages, bicim = metin_cikar(body, ctype, ocr=bool(args.get("ocr")))
    out = paginate(text, args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update({"source_url": url, "pdf_pages": pages, "content_type": ctype, "metin_bicimi": bicim})
    if not text:
        out["error"] = "Belge metni çıkarılamadı (taranmış PDF ya da okunamayan dosya). URL'yi kaynak olarak verin."
    return out


_KASE = 200     # bundan kısa PDF metni belge değil, sayfadaki kaşe ya da başlıktır


def metin_cikar(body: bytes, ctype: str = "", ocr: bool = False) -> Tuple[str, int, str]:
    """Belge baytları → (metin, sayfa, biçim). Biçim: pdf, ocr, docx, doc, html ya da ''.

    EPDK aynı adresten PDF, DOCX ve eski Word (.doc) verir; 2016 öncesi kararların çoğu .doc'tur,
    bir kısmı da taranmış PDF. .doc için ``olefile`` gerekir (sunucuda yoksa o kararlar indeksten
    gelir); taranmış PDF ve Word'e resim olarak konmuş taranmış sayfa ancak ``ocr=True`` ve
    ``ocr.hazir()`` ise okunur (yalnız tarama makinesi).
    """
    if "pdf" in ctype or body.startswith(b"%PDF"):
        text, pages = pdf_to_text(body)
        # Taranmış belgede metin katmanı ya hiç yok ya yalnız kaşe ya da Resmî Gazete başlığı (6 kararda
        # 33-199 karakter); OCR istenince ikisi de okutulur.
        if len(text) < _KASE and ocr:
            import ocr as _ocr
            if _ocr.hazir():
                okunan, pages = _ocr.pdf_ocr(body)
                if len(okunan) > len(text):
                    return okunan, pages, "ocr"
        return text, pages, "pdf" if text else ""
    if body[:2] == b"PK":
        import io
        import zipfile
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                if "word/document.xml" in z.namelist():
                    xml = z.read("word/document.xml").decode("utf-8", "replace")
                    text = html_to_text(re.sub(r"</w:p>", "\n", xml))
                    if text or not ocr:
                        return text, 0, "docx" if text else ""
                    # Taranmış sayfa Word'e resim olarak konmuş (11 karar, 2016-2018).
                    resimler = [z.read(ad) for ad in sorted(z.namelist())
                                if ad.startswith("word/media/") and ad.lower().endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff"))]
                    import ocr as _ocr
                    if resimler and _ocr.hazir():
                        text = _ocr.goruntu_ocr(resimler)
                    return text, 0, "ocr" if text else ""
                # Karar ve ekleri tek arşivde (3 karar, 2003-2017): içindeki Word dosyaları sırayla.
                parcalar = [metin_cikar(z.read(ad))[0] for ad in sorted(z.namelist())
                            if ad.lower().endswith((".doc", ".docx"))]
        except Exception:  # noqa: BLE001
            return "", 0, ""
        text = "\n\n".join(p for p in parcalar if p)
        return text, 0, "zip" if text else ""
    if body.startswith(OLE_MAGIC):
        text = doc_to_text(body)
        return text, 0, "doc" if text else ""
    if b"<html" in body[:4000].lower():
        if b"<title>EPDK |" in body[:4000]:
            return "", 0, ""                  # portalın kendi sayfası (menü), karar belgesi değil
        text = html_to_text(body.decode("utf-8", "replace"))
        return text, 0, "html" if text else ""
    return "", 0, ""


def crawl(index, markets: Optional[List[str]] = None, fetch_text: bool = False, limit: int = 0,
          log=print) -> Dict[str, Any]:
    n = 0
    for m in markets or list(PAGES):
        try:
            cats = categories(m)
        except HttpError as exc:
            log("epdk: %s page failed: %s" % (m, exc))
            continue
        for c in cats:
            if limit and n >= limit:
                break
            try:
                rows = decisions(c["id"], m, c["label"], c["path"])
            except HttpError as exc:
                log("epdk: category %s failed: %s" % (c["id"], exc))
                continue
            for r in rows:
                if limit and n >= limit:
                    break
                if getattr(index, "exists", lambda ref: False)("epdk:%s" % r["id"]):
                    continue
                body = "%s\n%s › %s" % (r["subject"], r["path"], r["category"])
                if fetch_text and r["documents"]:
                    g = get({"id": r["documents"][0]["url"], "page_chars": 200000})
                    body = (g.get("text") or "") + "\n\n" + body
                index.upsert({"ref": "epdk:%s" % r["id"],
                              "title": "EPDK Kurul Kararı %s (%s) — %s" % (r["decision_no"], r["date"], r["subject"][:120]),
                              "body": body[:200000], "url": r["source_url"], "lang": "tr", "date": _iso(r["date"]),
                              "status": r["mulga"] or "yürürlükte", "court": "EPDK", "subject": "enerji",
                              "citation": r["citation"],
                              "meta": {"kurum": "epdk", "market": m, "category": c["label"], "path": c["path"],
                                       "decision_no": r["decision_no"], "rg": "%s/%s" % (r["rg_date"], r["rg_no"]) if r["rg_date"] else ""}})
                n += 1
        log("epdk: %s, %d categories, %d docs so far" % (m, len(cats), n))
    return {"indexed": n}


def _iso(d: str) -> str:
    return iso_tarih(d)


SEARCH_SCHEMA = {"type": "object", "properties": {
    "query": {"type": "string", "description": "Karar konusu / kategori satırında aranacak kelimeler (hepsi geçmeli)"},
    "market": {"type": "string", "enum": list(PAGES), "description": "Piyasa; boş = hepsi (ilk çağrı yavaş)"},
    "category": {"type": "string", "description": "Kategori adında filtre, örn. 'lisans', 'tarife', 'YEKDEM'"},
    "decision_no": {"type": "string"}, "year": {"type": "integer"},
    "max_categories": {"type": "integer", "default": 80},
    "limit": {"type": "integer", "default": 20}}}
GET_SCHEMA = {"type": "object", "properties": {"id": {"type": "string", "description": "Belge URL'si (documents[0].url)"},
                                               "page": {"type": "integer", "default": 1},
                                               "page_chars": {"type": "integer", "default": 8000}},
              "required": ["id"]}

SOURCE = Source(
    key="epdk", label="EPDK — Enerji Piyasası Düzenleme Kurulu kararları", kind="kurum",
    notes=("Beş piyasa sayfası (elektrik, dogalgaz, petrol, lpg, denetim) × onlarca kategori; kategori başına bir istek. "
           "market ve category ile daraltın. Yalnız 'yapısal' Kurul kararları listelenir (lisans tadilleri değil); "
           "bireysel lisans işlemleri için RG fihristi ve apigateway.epdk.gov.tr lisans sorguları ayrı kaynaktır. "
           "2016 öncesi kararların çoğu eski Word (.doc), bir kısmı taranmış PDF; metinleri yerel indekste "
           "(semantik_ara, kurum='epdk'). Canlı belgeden metin çıkmazsa kurum_karari_getir indeksteki metni "
           "verir; taranmış belgede OCR uyarısıyla (rakam ve özel isimde okuma hatası olabilir)."),
    search=search, get=get, crawl=crawl, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage=BASE + PAGES["elektrik"],
)
