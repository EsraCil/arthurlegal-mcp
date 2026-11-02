"""Resmî Gazete — günlük fihrist ve belge metni (resmigazete.gov.tr).

    GET https://www.resmigazete.gov.tr/eskiler/<yyyy>/<mm>/<yyyymmdd>.htm     (windows-1254)

The page is the day's table of contents: section headings in capitals
(``YÜRÜTME VE İDARE BÖLÜMÜ``, ``YÖNETMELİKLER``, ``KURUL KARARI`` …) and items
prefixed ``––``, each linking to ``<yyyymmdd>-<n>.htm`` or ``.pdf``. Mükerrer
issues live at ``<yyyymmdd>M1.htm``.

This is the authoritative place to see that a regulator's Kurul kararı was
actually published (EPDK, BDDK, SPK tebliğleri, Rekabet Kurumu tebliğleri),
and what its RG date/number is.

Citation contract: ``RG 05.09.2026, S. 33361``.
"""

from __future__ import annotations

import re
from datetime import date as _date, datetime, timedelta
from typing import Any, Dict, List
from urllib.parse import urljoin

from net import Http, HttpError
from textx import html_to_text, paginate, pdf_to_text, strip_tags, count_hits
from sources import Source

try:                                    # Konu süzgeci isteğe bağlıdır.
    import triyaj as _triyaj
except Exception:                       # pragma: no cover - modül yoksa sessiz
    _triyaj = None

BASE = "https://www.resmigazete.gov.tr"
_http = Http(BASE, {"Accept": "text/html,application/pdf,*/*"})


def _konu_suz(items: List[Dict[str, Any]], konu: str, esik: Any
              ) -> Dict[str, Any]:
    """Fihrist kalemlerini konu olasılığına göre eler. Bkz. ``triyaj`` modülü.

    Neden ayrı bir süzgeç: ``query`` harfi harfine eşleşme arar ve Resmî Gazete
    başlıkları konu adını çoğu zaman taşımaz. Etiketli gövdede ölçüldü — konu
    adı, icra kalemlerinin yalnız %10'unda, rekabet kalemlerinin %14'ünde
    geçiyor. 'Konkordato Gider Avansı Tarifesi' icradır ama 'icra' yazmaz;
    'Şarj Hizmeti Yönetmeliği' enerjidir ama 'enerji' yazmaz. Konu süzgeci bu
    kalemleri bulur, metinsel arama bulamaz.

    Elenen kalemler SAYILIR ve çağırana bildirilir. Bir hukukçuya "bulunan
    budur" demek, arkada kaç kalemin sessizce atıldığını söylemeden dürüst
    değildir.
    """
    if _triyaj is None:
        return {"error": "Konu süzgeci bu kurulumda yok (triyaj modülü yüklü değil)."}
    hazir = _triyaj.hazirla(konu, esik)
    if isinstance(hazir, dict):
        return hazir
    motor, e = hazir

    tutulan = []
    for it in items:
        p = motor.p(konu, (it.get("section") or "") + " " + (it.get("title") or ""))
        if p >= e:
            tutulan.append(dict(it, konu_skoru=round(p, 4)))
    return {"items": tutulan, "elenen": len(items) - len(tutulan), "esik": e,
            "konu": konu}


# Süzgecin sınırı her yanıtta tekrarlanır; çağıran bunu görmeden karar vermesin.
_KONU_NOT = ("Konu süzgeci bir ÖN ELEMEDİR, kapsam garantisi değildir — ölçülen "
             "duyarlılık enerji %97, rekabet %100, vergi %93, icra %92. Yayım "
             "teyidi gibi eksiksizlik gerektiren işlerde konu kullanmayın; "
             "esik=0 ile süzgeci kapatabilirsiniz.")


def _day_url(d: _date, mukerrer: int = 0) -> str:
    return "%s/eskiler/%04d/%02d/%04d%02d%02d%s.htm" % (BASE, d.year, d.month, d.year, d.month, d.day,
                                                          "M%d" % mukerrer if mukerrer else "")


def fihrist(args: Dict[str, Any]) -> Dict[str, Any]:
    ds = str(args.get("date") or "").strip()
    try:
        d = datetime.strptime(ds, "%Y-%m-%d").date() if ds else _date.today()
    except ValueError:
        return {"error": "date YYYY-MM-DD olmalı."}
    url = _day_url(d, int(args.get("mukerrer") or 0))
    try:
        html = _http.get_text(url)
    except HttpError as exc:
        if exc.status == 404:
            return {"date": d.isoformat(), "error": "Bu tarihte Resmî Gazete yok (tatil?) veya henüz yayımlanmadı.", "url": url}
        return {"error": str(exc)}
    body = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    m = re.search(r"Tarihli ve (\d+) Sayılı", body)
    number = m.group(1) if m else ""
    section = ""
    items: List[Dict[str, Any]] = []
    for tag, attrs, inner in re.findall(r"<(p|div|h\d|b|strong)([^>]*)>(.*?)</\1>", body, re.S):
        txt = strip_tags(inner)
        if not txt:
            continue
        if txt.startswith(("––", "--", "–", "—")):
            title = txt.lstrip("–—- ").strip()
            link = re.search(r'href="([^"]+)"', inner)
            href = urljoin(url, link.group(1)) if link else ""
            items.append({"section": section, "title": title, "url": href})
        elif txt.isupper() and len(txt) < 80 and "GAZETE" not in txt:
            section = txt
    q = (args.get("query") or "").strip()
    if q:
        items = [i for i in items if all(count_hits(i["title"] + " " + i["section"], t) for t in q.split())]
    out = {"date": d.isoformat(), "number": number, "citation": "RG %s, S. %s" % (d.strftime("%d.%m.%Y"), number or "?"),
           "url": url, "total": len(items), "items": items,
           "note": "Belge metni: resmi_gazete_getir(url). Kurul kararları 'KURUL KARARI/KARARLARI' bölümündedir."}

    konu = (args.get("konu") or "").strip()
    if konu:
        suz = _konu_suz(items, konu, args.get("esik"))
        if suz.get("error"):
            return suz
        out.update(items=suz["items"], total=len(suz["items"]),
                   konu=konu, esik=suz["esik"], konu_elenen=suz["elenen"],
                   konu_notu=_KONU_NOT)
    return out


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    url = str(args.get("url") or "").strip()
    if not url.startswith(BASE + "/"):
        return {"error": "url resmigazete.gov.tr adresinde olmalı (fihrist sonucundaki url)."}
    try:
        body, hdrs, _ = _http.get(url, timeout=120)
    except HttpError as exc:
        return {"error": str(exc)}
    ctype = (hdrs.get("Content-Type") or "").lower()
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        text, _ = pdf_to_text(body)
    else:
        from net import decode
        text = html_to_text(decode(body, hdrs))
    out = paginate(text, args.get("page") or 1, int(args.get("page_chars") or 8000))
    out["source_url"] = url
    return out


def scan(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search fihrist titles across a date range (one request per day).

    İki süzgeç bağımsızdır ve birlikte verilirse VE ile birleşir:

    ``query``  harfi harfine — bilinen bir terimi ararken doğrudur.
    ``konu``   olasılıksal — bir alanı tararken doğrudur; başlıkta konu adı
               geçmese bile yakalar. Sınırı için ``triyaj`` modülüne bakın.

    En az biri gerekir: ikisi de boşken 60 günlük fihristi ham dökmek, çağırana
    yüzlerce ilgisiz kalem göndermekten başka bir şey yapmaz.
    """
    q = (args.get("query") or "").strip()
    konu = (args.get("konu") or "").strip()
    if not q and not konu:
        return {"error": "query veya konu gerekli (biri yeterli, ikisi VE ile birleşir)."}
    try:
        end = datetime.strptime(args["date_to"], "%Y-%m-%d").date() if args.get("date_to") else _date.today()
        start = datetime.strptime(args["date_from"], "%Y-%m-%d").date() if args.get("date_from") else end - timedelta(days=13)
    except ValueError:
        return {"error": "Tarihler YYYY-MM-DD olmalı."}
    if (end - start).days > 60:
        return {"error": "Aralık en fazla 60 gün olabilir (günde bir istek)."}

    # Konuyu DÖNGÜDEN ÖNCE doğrula. Aksi hâlde model eksikse fihrist her gün
    # hata döndürür, döngü o günü sessizce atlar ve kullanıcı "o aralıkta hiç
    # kalem yok" sanır. Bir araştırma aracında sessiz boş sonuç, gürültülü
    # hatadan çok daha pahalıdır.
    if konu:
        on = _konu_suz([], konu, args.get("esik"))
        if on.get("error"):
            return on

    hits, days, elenen = [], 0, 0
    d = start
    while d <= end:
        r = fihrist({"date": d.isoformat(), "query": q, "konu": konu,
                     "esik": args.get("esik")})
        if not r.get("error"):
            days += 1
            elenen += int(r.get("konu_elenen") or 0)
            for it in r["items"]:
                hits.append({**it, "date": r["date"], "rg_number": r["number"], "citation": r["citation"]})
        d += timedelta(days=1)
    out = {"query": q, "from": start.isoformat(), "to": end.isoformat(),
           "days_scanned": days, "total": len(hits), "results": hits}
    if konu:
        out.update(konu=konu, esik=on["esik"], konu_elenen=elenen,
                   konu_notu=_KONU_NOT)
    return out


SOURCE = Source(
    key="resmi_gazete", label="Resmî Gazete — günlük fihrist", kind="gazete",
    notes="Yayım teyidi ve RG künyesi için birincil kaynak. resmi_gazete_tara tarih aralığında başlık arar (gün başına bir istek).",
    search=fihrist, get=get, homepage=BASE,
    search_schema={"type": "object", "properties": {
        "date": {"type": "string", "description": "YYYY-MM-DD (varsayılan bugün)"},
        "mukerrer": {"type": "integer", "default": 0},
        "query": {"type": "string", "description": "Fihrist başlıklarında harfi harfine filtre"},
        "konu": {"type": "string", "enum": ["enerji", "rekabet", "vergi", "icra"],
                 "description": "Konu ön elemesi (yerel, ağsız). Başlıkta konu adı "
                                "geçmese de yakalar. ÖN ELEMEDİR: duyarlılık %92-100, "
                                "yayım teyidinde kullanmayın."},
        "esik": {"type": "number", "description": "Konu olasılık eşiği (varsayılan 0.20, "
                                                  "ölçülmüştür). 0 süzgeci kapatır ve tüm "
                                                  "kalemleri skorlarıyla döndürür."}}},
    get_schema={"type": "object", "properties": {"url": {"type": "string"}, "page": {"type": "integer", "default": 1},
                                                 "page_chars": {"type": "integer", "default": 8000}},
                "required": ["url"]},
)
