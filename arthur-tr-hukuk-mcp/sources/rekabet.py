"""Rekabet Kurumu — Kurul kararları (rekabet.gov.tr).

Upstream is server-rendered HTML:

    GET /tr/Kararlar?PdfText=<q>&KararTuruID=<guid>&KararSayisi=&KararTarihi=&YayinlanmaTarihi=&page=N

Ten decisions per page inside ``div#kararList table.equalDivide`` (three rows
each). ``Toplam : N`` gives the count. ``/Karar?kararId=<guid>`` returns the
decision **PDF itself** (Content-Type application/pdf, several MB for long
reasoned decisions), which is why ``get`` pages the extracted text.

Citation contract: ``Rekabet Kurulu, 12.05.2022 tarih ve 22-21/345-150 sayılı karar``
— decision number and date verbatim from the listing.

Listing structure from saidsurucu/yargi-mcp (MIT).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urljoin, urlparse

from net import Http, HttpError
from textx import iso_tarih, paginate, pdf_to_text, strip_tags, HAS_PYPDF
from sources import Source

BASE = "https://www.rekabet.gov.tr"
_http = Http(BASE, {"Accept": "text/html,application/pdf,*/*"})

TYPES = {
    "birlesme_devralma": ("2fff0979-9f9d-42d7-8c2e-a30705889542", "Birleşme ve Devralma"),
    "rekabet_ihlali": ("720614bf-efd1-4dca-9785-b98eb65f2677", "Rekabet İhlali"),
    "muafiyet_menfi_tespit": ("95ccd210-5304-49c5-b9e0-8ee53c50d4e8", "Menfi Tespit ve Muafiyet"),
    "ozellestirme": ("e1f14505-842b-4af5-95d1-312d6de1a541", "Özelleştirme"),
    "diger": ("dda8feaf-c919-405c-9da1-823f22b45ad9", "Diğer"),
}

_pdf_cache: Dict[str, bytes] = {}


def _parse_list(html: str) -> Dict[str, Any]:
    total = None
    m = re.search(r"Toplam\s*:\s*(\d+)", html)
    if m:
        total = int(m.group(1))
    block = re.search(r'<div[^>]*id="kararList"[^>]*>(.*)', html, re.S)
    items: List[Dict[str, Any]] = []
    if block:
        for tbl in re.findall(r'<table[^>]*class="[^"]*equalDivide[^"]*"[^>]*>(.*?)</table>', block.group(1), re.S):
            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, re.S)
            if len(rows) < 3:
                continue
            r1 = re.findall(r"<td[^>]*>(.*?)</td>", rows[0], re.S)
            r2 = re.findall(r"<td[^>]*>(.*?)</td>", rows[1], re.S)
            r3 = rows[2]
            pub = strip_tags(r1[0]) if r1 else ""
            no = strip_tags(r1[1]) if len(r1) > 1 else ""
            dec_date = strip_tags(r2[0]) if r2 else ""
            dec_type = strip_tags(r2[1]) if len(r2) > 1 else ""
            link = re.search(r'<a[^>]*href="(/Karar\?kararId=[^"]+)"[^>]*>(.*?)</a>', r3, re.S)
            if not link:
                continue
            kid = parse_qs(urlparse(link.group(1)).query).get("kararId", [""])[0]
            title = strip_tags(link.group(2))
            items.append({
                "karar_id": kid, "title": title, "decision_number": no, "decision_date": dec_date,
                "publication_date": pub, "decision_type": dec_type,
                "citation": "Rekabet Kurulu, %s tarih ve %s sayılı karar" % (dec_date, no),
                "source_url": urljoin(BASE, link.group(1)),
            })
    return {"total": total, "results": items}


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    q = (args.get("query") or "").strip()
    kind = args.get("decision_type") or ""
    guid = TYPES[kind][0] if kind in TYPES else ""
    params = [("sayfaAdi", ""), ("YayinlanmaTarihi", args.get("publication_date") or ""),
              ("PdfText", q), ("KararTuruID", guid), ("KararSayisi", args.get("decision_number") or ""),
              ("KararTarihi", args.get("decision_date") or "")]
    page = max(1, int(args.get("page") or 1))
    if page > 1:
        params.append(("page", str(page)))
    try:
        html = _http.get_text("/tr/Kararlar", params=params)
    except HttpError as exc:
        return {"error": str(exc)}
    out = _parse_list(html)
    note = "Tam metin: kurum_karari_getir(kurum='rekabet', id=karar_id). PDF'ler uzun; sayfalanır."
    if q and " " in q.strip('"') and not (q.startswith('"') and q.endswith('"')):
        note += (" DİKKAT: tırnaksız çok kelimeli sorgu ifade araması değildir; Kurum kelimeleri ayrı ayrı eşler ve "
                 "'total' ifadeyi içeren karar sayısı değildir. İfade için sorguyu tırnak içinde yazın.")
    out.update({"query": q, "page": page, "page_size": 10, "note": note})
    return out


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    kid = str(args.get("id") or "").strip()
    if not kid:
        return {"error": "id (karar_id) gerekli."}
    url = "%s/Karar?kararId=%s" % (BASE, kid)
    try:
        data = _pdf_cache.get(kid)
        if data is None:
            body, hdrs, final = _http.get(url, timeout=120)
            ctype = (hdrs.get("Content-Type") or "").lower()
            if "pdf" not in ctype and not body.startswith(b"%PDF"):
                # Landing page instead of PDF: find the link.
                html = body.decode("utf-8", "replace")
                m = re.search(r'(?:href|src)="([^"]+\.pdf[^"]*)"', html, re.I)
                if not m:
                    return {"error": "Karar PDF'i bulunamadı", "source_url": url}
                body, _, _ = _http.get(urljoin(final, m.group(1)), timeout=120)
            data = body
            if len(_pdf_cache) > 8:
                _pdf_cache.clear()
            _pdf_cache[kid] = data
    except HttpError as exc:
        return {"error": str(exc), "source_url": url}
    text, pages = pdf_to_text(data)
    if not text:
        return {"source_url": url, "pdf_pages": pages, "text": "",
                "error": "PDF metni çıkarılamadı (taranmış belge veya pypdf yok: %s)." % HAS_PYPDF}
    out = paginate(text, args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update({"karar_id": kid, "pdf_pages": pages, "source_url": url})
    return out


def crawl(index, max_pages: int = 20, query: str = "", decision_type: str = "",
          fetch_text: bool = False, log=print) -> Dict[str, Any]:
    """Index the listing (title + metadata). Full PDF text only with ``fetch_text``."""
    n = 0
    for page in range(1, max_pages + 1):
        res = search({"query": query, "page": page, "decision_type": decision_type})
        items = res.get("results") or []
        if not items:
            break
        for it in items:
            if getattr(index, "exists", lambda r: False)("rekabet:%s" % it["karar_id"]):
                continue
            body = it["title"]
            if fetch_text:
                g = get({"id": it["karar_id"], "page_chars": 200000})
                body = (g.get("text") or it["title"])[:200000]
            tarih, gorunen, kaynak = karar_tarihi(it["decision_date"], it["decision_number"],
                                                  body if fetch_text else "")
            meta = {"decision_number": it["decision_number"], "kurum": "rekabet"}
            citation = it["citation"]
            if kaynak == "karar metni" and tarih != iso_tarih(it["decision_date"]):
                citation = "Rekabet Kurulu, %s tarih ve %s sayılı karar" % (gorunen, it["decision_number"])
                meta["liste_tarihi"] = it["decision_date"]
            index.upsert({
                "ref": "rekabet:%s" % it["karar_id"], "title": it["title"], "body": body,
                "url": it["source_url"], "lang": "tr", "date": tarih,
                "status": it["decision_type"], "court": "Rekabet Kurulu",
                "subject": "rekabet", "citation": citation, "meta": meta,
            })
            n += 1
        log("rekabet: page %d, %d docs" % (page, n))
    return {"indexed": n}


_KARAR_TARIHI = re.compile(r"Karar\s+Tarihi\s*:?\s*(\d{1,2}\s*[./]\s*\d{1,2}\s*[./]\s*\d{4})", re.I)


def karar_tarihi(liste_tarihi: str, karar_sayisi: str, metin: str = "") -> Tuple[str, str, str]:
    """Kararın tarihi: (ISO, atıfta yazılacak biçim, kaynak: 'liste' ya da 'karar metni').

    Liste sayfasındaki tarih elle girilmiş. 11.10.2026'da 10.445 kararın 41'inde kararın
    kendi "Karar Tarihi" satırından farklıydı (biri 17.8.2029; kararın kendisi 17.08.2023).
    Karar sayısının ilk iki hanesi yıldır (23-39/726-249 → 2023). Karar metnindeki tarih
    bu yılla uyuşuyorsa o esas alınır, uyuşmuyorsa yılla uyuşan liste tarihi (metinde de
    yazım hatası olabiliyor); ikisi de uyuşmuyorsa metindeki.
    """
    liste = iso_tarih(liste_tarihi)
    m = _KARAR_TARIHI.search((metin or "")[:5000])
    govde = iso_tarih(m.group(1)) if m else ""
    yil = re.match(r"\s*(\d{2})-", karar_sayisi or "")

    def uyar(t: str) -> bool:
        return bool(t and yil and t[2:4] == yil.group(1))

    if govde and (uyar(govde) or not yil or not uyar(liste)):
        return govde, re.sub(r"\s+", "", m.group(1)), "karar metni"
    return liste, (liste_tarihi or "").strip(), "liste"


SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": ("Karar PDF metninde aranacak ifade. Çok kelimeli ifadeyi "
                                                    "tırnak içinde yazın (\"karşı oy gerekçesi\"); tırnaksız sorgu "
                                                    "kelimeleri ayrı ayrı eşler.")},
        "decision_type": {"type": "string", "enum": list(TYPES), "description": "Karar türü filtresi"},
        "decision_number": {"type": "string", "description": "Örn. 22-21/345-150"},
        "decision_date": {"type": "string", "description": "DD.MM.YYYY"},
        "publication_date": {"type": "string", "description": "DD.MM.YYYY"},
        "page": {"type": "integer", "minimum": 1, "default": 1},
    },
}
GET_SCHEMA = {"type": "object", "properties": {"id": {"type": "string", "description": "karar_id (GUID)"},
                                               "page": {"type": "integer", "default": 1},
                                               "page_chars": {"type": "integer", "default": 8000}},
              "required": ["id"]}

SOURCE = Source(
    key="rekabet", label="Rekabet Kurumu — Kurul kararları", kind="kurum",
    notes=("Liste sayfası 10 karar/sayfa; PdfText araması karar metninde yapılır. Kurum araması kelime "
           "bazlıdır: tırnaksız çok kelimeli sorgu ifade aramaz (29.09.2026 ölçümü: 'karşı oy' 2.737, "
           "'farklı gerekçe' 5.222 karar; tırnaklı \"karşı oy gerekçesi\" 61). İfade için tırnak kullanın; "
           "'total' bir ifadeyi içeren karar sayısı değildir, istatistik için kullanmayın. Karar PDF'leri "
           "büyük olabilir (birleşme kararları 100+ sayfa). Kılavuz ve tebliğler için mevzuat_ara. "
           "Yerel indeks (semantik_ara, kurum='rekabet') kararların TAM METNİNİ de taşır (30.09.2026: 10.445 kararın "
           "10.433'ü; metni çıkmayan 12 karar yalnız başlıkla): kelime ve tırnaklı ifade gerekçede de aranır, sonuç "
           "ilgiye göre sıralanır; anlam kanalı başlığa ve metnin ilk 8.000 karakterine bakar. İndeksteki tarih "
           "ve atıf kararın kendi 'Karar Tarihi' satırıyla denetlidir (liste sayfası 41 kararda farklı tarih "
           "yazıyor; canlı aramadaki atıf liste tarihini taşır). İndeks son dağıtımdaki arşivdir; en yeni "
           "kararlar için bu canlı arama."),
    search=search, get=get, crawl=crawl, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage=BASE + "/tr/Kararlar",
)
