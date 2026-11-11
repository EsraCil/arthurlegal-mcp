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

import os
import queue
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from net import Http, HttpError, TokenBucket
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


# --------------------------------------------------------------------------- #
# Arka planda ısıtma (F-067)
# --------------------------------------------------------------------------- #
# Soğuk önbellekte ilk arama beş piyasa sayfasını ve ~126 kategoriyi art arda çekiyordu: ~40 sn, istemcinin
# zaman aşımına yakın. Barındırılan sunucu açılıştan sonra tek bir arka plan işçisiyle listeyi BİR KEZ,
# iki istek arasında ISITMA_ARALIGI saniye bekleyerek çeker (makine başına ~131 istek, ~9 dk; iki makine
# toplamı dakikada en çok 30 istek, TKGM için seçilen sınırla aynı). Süresi
# (_TTL) geçen kayıt aramada bekletilmeden verilir ve aynı işçi onu aynı hızla tazeler: tazeleme yalnız
# EPDK arandığında olur, kendi kendine yinelenmez. Kayıt _BAYAT_SINIR'dan eskiyse ya da işçi yoksa (yerel
# kurulum, crawl.py, testler) eski davranış sürer: arama listeyi kendisi çeker. Üst üste _HATA_SINIRI
# hatada işçi sıradaki işleri bırakır; site yanıt vermezken istek yağdırmaz.
ISITMA_ARALIGI = 4.0
_BAYAT_SINIR = 24 * 3600
_HATA_SINIRI = 3


class _Tazeleyici:
    """Tek arka plan işçisi. Aynı işi iki kez sıraya almaz; istekler arasını kova açar."""

    def __init__(self, aralik: float, log) -> None:
        self.kova = TokenBucket(capacity=1, refill_s=max(float(aralik), 0.001), max_wait=3600)
        self.sira: "queue.Queue[Tuple[str, ...]]" = queue.Queue()
        self.bekleyen: set = set()
        self.kilit = threading.Lock()
        self.log = log
        self.ardisik_hata = 0
        self.thread: Optional[threading.Thread] = None

    def ekle(self, is_: Tuple[str, ...]) -> None:
        with self.kilit:
            if is_ in self.bekleyen:
                return
            self.bekleyen.add(is_)
        self.sira.put(is_)

    def calis(self) -> None:
        while True:
            self._yap(self.sira.get())

    def bosalt(self) -> None:
        """Sıradaki işleri bu iş parçacığında bitirir (testler)."""
        while True:
            try:
                is_ = self.sira.get_nowait()
            except queue.Empty:
                return
            self._yap(is_)

    def _yap(self, is_: Tuple[str, ...]) -> None:
        try:
            if is_[0] == "piyasa":
                market = is_[1]
                if _fresh(_tree, market) is None:
                    self.kova.acquire()
                    _kategorileri_cek(market)
                for c in _tree[market][1]:
                    if _fresh(_lists, c["id"]) is None:
                        self.ekle(("liste", c["id"], market, c["label"], c["path"]))
            elif _fresh(_lists, is_[1]) is None:
                self.kova.acquire()
                _listeyi_cek(*is_[1:])
            self.ardisik_hata = 0
        except Exception as exc:  # noqa: BLE001 - ısıtma bir iyileştirme; hata yalnız aramanın kendisinin çekmesi demek
            self.ardisik_hata += 1
            self.log("epdk ısıtma %s %s: %s" % (is_[0], is_[1], exc))
            if self.ardisik_hata >= _HATA_SINIRI:
                self._birak()
        finally:
            with self.kilit:
                self.bekleyen.discard(is_)

    def _birak(self) -> None:
        birakilan = 0
        while True:
            try:
                is_ = self.sira.get_nowait()
            except queue.Empty:
                break
            birakilan += 1
            with self.kilit:
                self.bekleyen.discard(is_)
        self.ardisik_hata = 0
        self.log("epdk ısıtma: üst üste %d hata, sıradaki %d iş bırakıldı" % (_HATA_SINIRI, birakilan))


_isci: Optional[_Tazeleyici] = None
_isci_kilit = threading.Lock()


def isit(aralik: float = ISITMA_ARALIGI, log=None) -> Optional[threading.Thread]:
    """İşçiyi başlatır (bir kez) ve beş piyasanın listesini ısıtma sırasına koyar."""
    global _isci
    with _isci_kilit:
        if _isci is None:
            _isci = _Tazeleyici(aralik, log or (lambda m: sys.stderr.write(m + "\n")))
            _isci.thread = threading.Thread(target=_isci.calis, name="epdk-isitma", daemon=True)
            _isci.thread.start()
        for m in PAGES:
            _isci.ekle(("piyasa", m))
        return _isci.thread


def acilista_isit(ortam: Optional[Dict[str, str]] = None) -> Optional[threading.Thread]:
    """Sunucu açılışında çağrılır. EPDK_ISITMA=0 kapatır, =1 her taşımada açar; ayar yoksa yalnız barındırılan
    HTTP sunucusunda (MCP_TRANSPORT=http) açıktır: yerel stdio kurulumu her açılışta EPDK'ya istek atmaz."""
    ortam = os.environ if ortam is None else ortam
    ayar = (ortam.get("EPDK_ISITMA") or "").strip()
    if ayar == "0" or (ayar != "1" and ortam.get("MCP_TRANSPORT") != "http"):
        return None
    return isit()


def _onbellekten(store: Dict[str, Any], key: str, is_: Tuple[str, ...]):
    """Taze kayıt; yoksa işçi varken _BAYAT_SINIR'dan genç bayat kayıt (işçi tazeler); yoksa None."""
    hit = store.get(key)
    if not hit:
        return None
    yas = time.time() - hit[0]
    if yas < _TTL:
        return hit[1]
    if _isci is not None and yas < _BAYAT_SINIR:
        _isci.ekle(is_)
        return hit[1]
    return None


def categories(market: str) -> List[Dict[str, str]]:
    """Category leaves of one market page, with their accordion path as context."""
    cached = _onbellekten(_tree, market, ("piyasa", market))
    if cached is not None:
        return cached
    return _kategorileri_cek(market)


def _kategorileri_cek(market: str) -> List[Dict[str, str]]:
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
    cached = _onbellekten(_lists, fid, ("liste", fid, market, category, path))
    if cached is not None:
        return cached
    return _listeyi_cek(fid, market, category, path)


def _listeyi_cek(fid: str, market: str = "", category: str = "", path: str = "") -> List[Dict[str, Any]]:
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
    if not url.startswith(BASE + "/"):
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


_ZIP_UYE_SINIRI = 64 * 1024 * 1024    # açılmış tek dosya; sıkıştırma bombası 1 GB'lık makineyi düşürmesin


def _zip_oku(z, ad: str) -> bytes:
    if z.getinfo(ad).file_size > _ZIP_UYE_SINIRI:
        raise ValueError("zip üyesi çok büyük: %s" % ad)
    return z.read(ad)


def metin_cikar(body: bytes, ctype: str = "", ocr: bool = False, _derinlik: int = 0) -> Tuple[str, int, str]:
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
                    xml = _zip_oku(z, "word/document.xml").decode("utf-8", "replace")
                    text = html_to_text(re.sub(r"</w:p>", "\n", xml))
                    if text or not ocr:
                        return text, 0, "docx" if text else ""
                    # Taranmış sayfa Word'e resim olarak konmuş (11 karar, 2016-2018).
                    resimler = [_zip_oku(z, ad) for ad in sorted(z.namelist())
                                if ad.startswith("word/media/") and ad.lower().endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff"))]
                    import ocr as _ocr
                    if resimler and _ocr.hazir():
                        text = _ocr.goruntu_ocr(resimler)
                    return text, 0, "ocr" if text else ""
                # Karar ve ekleri tek arşivde (3 karar, 2003-2017): içindeki Word dosyaları sırayla.
                # Yalnız bir kat: arşivin içindeki arşiv açılmaz.
                if _derinlik:
                    return "", 0, ""
                parcalar = [metin_cikar(_zip_oku(z, ad), _derinlik=1)[0] for ad in sorted(z.namelist())
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
