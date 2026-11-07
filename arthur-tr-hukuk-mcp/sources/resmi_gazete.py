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

import base64
import io
import json
import re
import struct
import threading
import time
import zlib
from collections import OrderedDict
from datetime import date as _date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from mcpcore import ContentResult
from net import Http, HttpError
from textx import (HAS_PYPDF, PdfReader, clean_ws, count_hits, html_to_text, paginate, pdf_to_text,
                   strip_tags, tr_fold)
from sources import Source

try:                                   # İsteğe bağlı: pypdf'in çözemediği taramaları (JBIG2,
    import pypdfium2 as _pdfium         # CCITT) sayfa görüntüsüne çevirir. Yalnız tam imajda var.
except Exception:                       # pragma: no cover - kod dağıtımında yok
    _pdfium = None

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
    ek: Dict[str, Any] = {}
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        reader, metinler = _pdf_ac(body)
        if reader is None:
            text, _ = pdf_to_text(body)
        else:
            # pdf_to_text ile aynı birleştirme; PDF bir kez çözülür, sayfa metinleri tarama için kalır.
            text = clean_ws("\n\n".join(p.strip() for p in metinler))
            ek = _pdf_ozeti(reader, metinler)
            _onbellege_koy(url, body)       # ek_sayfa aynı PDF'i yeniden indirmesin
    else:
        from net import decode
        html = decode(body, hdrs)
        text = html_to_text(html)
        ekler = _ekler(html, url)
        if ekler:
            ek = {"ekler": ekler, "ek_okuma": _EK_OKUMA}
    out = paginate(text, args.get("page") or 1, int(args.get("page_chars") or 8000))
    out["source_url"] = url
    out.update(ek)
    return out


# --------------------------------------------------------------------------- #
# Ekler                                                                        #
# --------------------------------------------------------------------------- #
# Tarifeler, cetveller ve formlar çoğu zaman ayrı bir PDF'tedir: sayfa sonunda "Ekleri için
# tıklayınız." bağlantısı (20261103-3.htm -> 20261103-3-1.pdf). Bu PDF'ler çoğunlukla taranmış
# görüntüdür: metin katmanı ya hiç yoktur ya da yalnız RG'nin üst bilgisi vardır ("Tebliğ (N)
# Avukatlık Ekleri Film 7", ~30 karakter). Tutarlar görüntüdedir; ek_sayfa sayfayı görüntü olarak
# verir, model kendisi okur.
RG_HOST = "www.resmigazete.gov.tr"

# Bu kadar (boşluksuz) karakterden az metni olan, çizim ya da görüntü taşıyan sayfa "taranmış"
# sayılır. Üst bilgi ~30 karakterdir; en kısa gerçek metin sayfası bunun çok üstündedir.
_METIN_ESIGI = 200
_AZAMI_BAYT = 1_500_000          # tek sayfa görüntüsü
_HEDEF_GENISLIK = 1600           # PDFium'la işlenen sayfanın genişliği (piksel)
_AZAMI_YUKSEKLIK = 4000
# Ham JPEG bu genişliğe kadar çözülmeden geçer: RG taramaları 1937 px genişliğindedir ve
# yeniden kodlamak hem kaliteyi düşürür hem PDFium ister. Bayt sınırı aynı kalır.
_GECIS_GENISLIGI = 2048

KURAL = ("Tutarları görüntüden okuyun, kaynağı RG tarih/sayı ile verin; okunamayan rakam için "
         "UYARI satırı.")
_EK_OKUMA = ("Ekler ayrı belgedir: tr_resmi_gazete_getir(url) ekin metnini ve taranmış sayfalarını "
             "verir; taranmış sayfayı tr_resmi_gazete_ek_sayfa(url, sayfa) görüntü olarak verir.")
_TARANMIS_OKUMA = "Taranmış sayfayı tr_resmi_gazete_ek_sayfa(url, sayfa) ile görüntü olarak okuyun."

# İndirilen ek PDF'leri kısa süre bellekte: 12 sayfalık bir eki sayfa sayfa okumak RG'den
# 12 kez aynı dosyayı istemesin.
_ONBELLEK: "OrderedDict[str, Tuple[float, bytes]]" = OrderedDict()
_ONBELLEK_ADET = 4
_ONBELLEK_AZAMI = 24 * 1024 * 1024
_ONBELLEK_SURE = 15 * 60
_ONBELLEK_KILIDI = threading.Lock()
# PDFium iş parçacığı güvenli değildir; HTTP sunucusu her bağlantıyı ayrı iş parçacığında karşılar.
_PDFIUM_KILIDI = threading.Lock()

_EK_ADI = re.compile(r"^(?P<kok>.+)-(?P<no>\d+)\.(?:pdf|html?)$", re.I)
_BAGLANTI = re.compile(r"""<a\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))[^>]*>(.*?)</a\s*>""",
                       re.I | re.S)
_RG_DOSYA = re.compile(r"^/eskiler/(\d{4})/(\d{2})/(\d{4})(\d{2})(\d{2})(?:M(\d+))?(?:[-.]|$)")


def _rg_adresi(url: str) -> bool:
    """Yalnız https://www.resmigazete.gov.tr/... (alt alan, kullanıcı adı, başka port yok)."""
    try:
        p = urlsplit(url)
        return (p.scheme == "https" and (p.hostname or "") == RG_HOST and p.port in (None, 443)
                and not p.username and not p.password)
    except ValueError:
        return False


def _ekler(html: str, url: str) -> List[Dict[str, Any]]:
    """Sayfadaki ek bağlantıları, sayfadaki sırayla: ``<sayfa>-<n>.pdf`` adlı dosyalar ve
    "… tıklayınız" yazılı PDF bağlantıları."""
    kok = urlsplit(url).path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    out: List[Dict[str, Any]] = []
    gorulen = set()
    for m in _BAGLANTI.finditer(html or ""):
        href = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        tam = urljoin(url, href)
        if tam in gorulen or not _rg_adresi(tam):
            continue
        ad = urlsplit(tam).path.rsplit("/", 1)[-1]
        e = _EK_ADI.match(ad)
        ek_adi = bool(e and e.group("kok").lower() == kok)
        tiklayiniz = ad.lower().endswith(".pdf") and "tiklayiniz" in tr_fold(strip_tags(m.group(4)))
        if ek_adi or tiklayiniz:
            gorulen.add(tam)
            out.append({"sira": len(out) + 1, "url": tam})
    return out


def _rg_kunyesi(url: str) -> Dict[str, Any]:
    """Bağlantının kendisinden RG tarihi (``/eskiler/2026/11/20261103-3-1.pdf``). Sayı bağlantıda
    yoktur; fihristten alınır."""
    m = _RG_DOSYA.match(urlsplit(url).path)
    if not m or m.group(1) != m.group(3) or m.group(2) != m.group(4):
        return {}
    y, a, g = m.group(3), m.group(4), m.group(5)
    try:
        _date(int(y), int(a), int(g))
    except ValueError:
        return {}
    cagri = 'tr_resmi_gazete_fihrist(date="%s-%s-%s"%s)' % (
        y, a, g, ", mukerrer=%s" % m.group(6) if m.group(6) else "")
    out: Dict[str, Any] = {"rg_tarihi": "%s.%s.%s" % (g, a, y)}
    if m.group(6):
        out["mukerrer"] = int(m.group(6))
    out["rg_sayisi_icin"] = cagri
    return out


def _pdf_ac(body: bytes):
    """(PdfReader, sayfa metinleri); pypdf yoksa ya da PDF bozuksa (None, [])."""
    if not HAS_PYPDF:
        return None, []
    try:
        reader = PdfReader(io.BytesIO(body))
        sayfalar = reader.pages
        metinler = []
        for p in sayfalar:
            try:
                metinler.append(p.extract_text() or "")
            except Exception:  # noqa: BLE001 - tek sayfanın hatası belgeyi düşürmez
                metinler.append("")
        return reader, metinler
    except Exception:  # noqa: BLE001 - bozuk PDF veri sorunudur
        return None, []


def _xobjeler(page) -> List[Any]:
    """Sayfanın doğrudan çizdiği XObject'ler (görüntü ya da form)."""
    try:
        res = page.get("/Resources")
        res = res.get_object() if res is not None else None
        xo = res.get("/XObject") if res is not None else None
        xo = xo.get_object() if xo is not None else None
        return [v.get_object() for v in xo.values()] if xo else []
    except Exception:  # noqa: BLE001
        return []


def _taranmis(page, metin: str) -> bool:
    """Metin katmanı yok: hiç metin yok ya da eşiğin altında metin + görüntü/çizim."""
    n = len(re.sub(r"\s+", "", metin or ""))
    if n >= _METIN_ESIGI:
        return False
    return n == 0 or bool(_xobjeler(page))


def _pdf_ozeti(reader, metinler: List[str]) -> Dict[str, Any]:
    taranmis = [i + 1 for i, (p, t) in enumerate(zip(reader.pages, metinler)) if _taranmis(p, t)]
    out: Dict[str, Any] = {"sayfa_sayisi": len(metinler), "taranmis_sayfalar": taranmis}
    if taranmis:
        out["taranmis_not"] = ("Bu sayfalarda metin katmanı yok (taranmış görüntü): tablo ve tutarlar "
                               "'text' alanında YOKTUR; orada görünen kısa satırlar sayfa üst bilgisidir. "
                               "Boş metin 'tutar yok' demek değildir.")
        out["ek_okuma"] = _TARANMIS_OKUMA
    return out


def _onbellege_koy(url: str, body: bytes) -> None:
    if len(body) > _ONBELLEK_AZAMI:
        return
    with _ONBELLEK_KILIDI:
        _ONBELLEK[url] = (time.monotonic(), body)
        _ONBELLEK.move_to_end(url)
        while len(_ONBELLEK) > _ONBELLEK_ADET:
            _ONBELLEK.popitem(last=False)


class _PdfDegil(Exception):
    pass


def _pdf_getir(url: str) -> bytes:
    with _ONBELLEK_KILIDI:
        kayit = _ONBELLEK.get(url)
        if kayit and time.monotonic() - kayit[0] < _ONBELLEK_SURE:
            _ONBELLEK.move_to_end(url)
            return kayit[1]
    body, _, _ = _http.get(url, timeout=120)
    if b"%PDF-" not in body[:1024]:
        raise _PdfDegil(url)
    _onbellege_koy(url, body)
    return body


def _jpeg_renk_uzayi(cs: Any) -> bool:
    """Tarayıcıda olduğu gibi görünen renk uzayları: gri, RGB, ya da 1/3 kanallı ICC."""
    try:
        cs = cs.get_object() if hasattr(cs, "get_object") else cs
        if str(cs) in ("/DeviceGray", "/DeviceRGB"):
            return True
        if isinstance(cs, list) and len(cs) == 2 and str(cs[0]) == "/ICCBased":
            return int(cs[1].get_object().get("/N", 0)) in (1, 3)
    except Exception:  # noqa: BLE001
        pass
    return False


def _ham_jpeg(page) -> Optional[Tuple[bytes, str, int, int, str]]:
    """Bağımlılıksız yol: sayfadaki TEK görüntü DCTDecode (JPEG) ise baytları çözmeden verir.

    pypdf DCTDecode'u çözmez; get_data() dosyadaki JPEG'i olduğu gibi döndürür. Maskeli,
    ters çevrilmiş (Decode), CMYK ya da döndürülmüş sayfada görüntü PDF'teki gibi görünmez:
    o sayfalar PDFium yoluna kalır.
    """
    xs = _xobjeler(page)
    if len(xs) != 1:
        return None
    o = xs[0]
    try:
        if o.get("/Subtype") != "/Image":
            return None
        f = o.get("/Filter")
        f = f.get_object() if hasattr(f, "get_object") else f
        filtreler = [str(x) for x in f] if isinstance(f, list) else [str(f)]
        if filtreler != ["/DCTDecode"]:
            return None
        if any(o.get(k) is not None for k in ("/SMask", "/Mask", "/Decode")):
            return None
        if int(page.rotation or 0) % 360:
            return None
        if not _jpeg_renk_uzayi(o.get("/ColorSpace")):
            return None
        w, h = int(o["/Width"]), int(o["/Height"])
        veri = o.get_data()
        if (not veri.startswith(b"\xff\xd8") or len(veri) > _AZAMI_BAYT
                or w > _GECIS_GENISLIGI or h > 2 * _AZAMI_YUKSEKLIK):
            return None
        return veri, "image/jpeg", w, h, "pdf-jpeg"
    except Exception:  # noqa: BLE001 - bu yol olmazsa sıradaki denenir
        return None


def _png(genislik: int, yukseklik: int, kanal: int, satirlar: bytes) -> bytes:
    """Standart kütüphaneyle PNG (8 bit gri ya da RGB, süzgeçsiz satırlar, zlib)."""
    def parca(tur: bytes, veri: bytes) -> bytes:
        return (struct.pack(">I", len(veri)) + tur + veri
                + struct.pack(">I", zlib.crc32(tur + veri) & 0xFFFFFFFF))
    satir = genislik * kanal
    ham = b"".join(b"\x00" + satirlar[y * satir:(y + 1) * satir] for y in range(yukseklik))
    ihdr = struct.pack(">IIBBBBB", genislik, yukseklik, 8, 0 if kanal == 1 else 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + parca(b"IHDR", ihdr) + parca(b"IDAT", zlib.compress(ham, 6))
            + parca(b"IEND", b""))


def _bitmap_png(bm) -> Tuple[bytes, int, int]:
    """PDFium bitmap'i (RGB/RGBx/gri) -> PNG; bütün pikselleri gri olan sayfa tek kanal yazılır."""
    w, h, n, adim = bm.width, bm.height, bm.n_channels, bm.stride
    tampon = bytes(bm.buffer)
    duz = tampon if adim == w * n else b"".join(tampon[y * adim:y * adim + w * n] for y in range(h))
    if n == 1:
        return _png(w, h, 1, duz), w, h
    r, g, b = duz[0::n], duz[1::n], duz[2::n]
    if r == g == b:
        return _png(w, h, 1, r), w, h
    rgb = bytearray(w * h * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = r, g, b
    return _png(w, h, 3, bytes(rgb)), w, h


def _pdfium_goruntu(veri: bytes, indeks: int) -> Optional[Tuple[bytes, str, int, int, str]]:
    """PDFium varsa sayfayı ~1600 px genişlikte işler; 1,5 MB'ı aşarsa küçülterek yineler."""
    if _pdfium is None:
        return None
    with _PDFIUM_KILIDI:
        pdf = _pdfium.PdfDocument(veri)
        try:
            sayfa = pdf[indeks]
            g_pt, y_pt = sayfa.get_size()
            if int(sayfa.get_rotation() or 0) % 180:
                g_pt, y_pt = y_pt, g_pt
            hedef = float(_HEDEF_GENISLIK)
            for _ in range(4):
                # PDFium boyu yukarı yuvarlar (595 pt x 1600/595 -> 1601 px): yarım piksel pay.
                olcek = min((hedef - 0.5) / max(g_pt, 1.0), (_AZAMI_YUKSEKLIK - 0.5) / max(y_pt, 1.0), 8.0)
                bm = sayfa.render(scale=olcek, rev_byteorder=True)
                try:
                    png, w, h = _bitmap_png(bm)
                finally:
                    bm.close()
                if len(png) <= _AZAMI_BAYT:
                    return png, "image/png", w, h, "pdfium"
                hedef *= 0.75
            return None
        finally:
            pdf.close()


def _uyari(url: str, neden: str, **ek: Any) -> Dict[str, Any]:
    """Görüntü uydurulmaz: veri yoksa yanıt bunu söyler ve UYARI satırını hazır verir."""
    return {"error": neden, "unavailable": True, "source_url": url,
            "uyari": "UYARI: veri çekilemedi, teyidiniz gerekli: %s" % url, **ek}


def ek_sayfa(args: Dict[str, Any]) -> Any:
    """Bir RG ek PDF'inin TEK sayfası: metin katmanı varsa metin, yoksa sayfa görüntüsü.

    Görüntü sırası: (1) sayfadaki tek görüntü JPEG ise PDF'teki baytlar (bağımlılıksız),
    (2) PDFium varsa sayfa işlenip PNG, (3) ikisi de olmazsa UYARI. Görüntü, modelin
    okuyacağı bir MCP görüntü bloğu olarak döner; yanında künye ve okuma kuralı.
    """
    url = str(args.get("url") or "").strip()
    if not _rg_adresi(url):
        return {"error": "url yalnız https://www.resmigazete.gov.tr/ adresinde bir PDF olabilir "
                         "(tr_resmi_gazete_getir sonucundaki ekler[].url)."}
    try:
        sayfa = int(args.get("sayfa") or 1)
    except (TypeError, ValueError):
        return {"error": "sayfa 1'den başlayan bir tam sayı olmalı."}
    if not HAS_PYPDF:
        return _uyari(url, "Bu kurulumda PDF okuyucu (pypdf) yok.")
    try:
        veri = _pdf_getir(url)
    except _PdfDegil:
        return {"error": "Bu adres PDF değil; HTML sayfası için tr_resmi_gazete_getir(url).",
                "source_url": url}
    except HttpError as exc:
        return _uyari(url, "Ek indirilemedi: %s" % exc)
    reader, metinler = _pdf_ac(veri)
    if reader is None:
        return _uyari(url, "PDF açılamadı (bozuk ya da şifreli).")
    toplam = len(metinler)
    if not 1 <= sayfa <= toplam:
        return {"error": "sayfa 1-%d arasında olmalı." % toplam, "toplam_sayfa": toplam, "source_url": url}
    page, metin = reader.pages[sayfa - 1], metinler[sayfa - 1]
    ozet: Dict[str, Any] = {"source_url": url, "sayfa": sayfa, "toplam_sayfa": toplam, **_rg_kunyesi(url)}
    if not _taranmis(page, metin):
        ozet.update(tur="metin", text=clean_ws(metin))
        return ozet

    goruntu = _ham_jpeg(page)
    if goruntu is None:
        try:
            goruntu = _pdfium_goruntu(veri, sayfa - 1)
        except Exception as exc:  # noqa: BLE001 - işlenemeyen sayfa UYARI olur, çöküş değil
            return _uyari(url, "Sayfa görüntüye çevrilemedi (%s)." % type(exc).__name__, **ozet)
    if goruntu is None:
        neden = ("Sayfa taranmış ve görüntüsü bu kurulumda çıkarılamıyor (JPEG değil; PDFium tam "
                 "imajda var)." if _pdfium is None else
                 "Sayfa görüntüsü %d baytı aşmadan üretilemedi." % _AZAMI_BAYT)
        return _uyari(url, neden, **ozet)
    bayt, mime, w, h, yol = goruntu
    ozet["tur"] = "goruntu"
    ozet["goruntu"] = {"mimeType": mime, "genislik": w, "yukseklik": h, "bayt": len(bayt), "yol": yol}
    kisa = clean_ws(metin)
    if kisa:
        ozet["sayfadaki_metin"] = kisa
    ozet["kural"] = "%s Biçim: UYARI: veri çekilemedi, teyidiniz gerekli: %s" % (KURAL, url)
    return ContentResult(
        [{"type": "text", "text": json.dumps(ozet, ensure_ascii=False, indent=2)},
         {"type": "image", "data": base64.b64encode(bayt).decode("ascii"), "mimeType": mime}],
        ozet)


EK_SAYFA_SCHEMA = {"type": "object", "properties": {
    "url": {"type": "string", "description": "Ek PDF'inin adresi (tr_resmi_gazete_getir sonucundaki "
                                             "ekler[].url); yalnız www.resmigazete.gov.tr"},
    "sayfa": {"type": "integer", "minimum": 1, "default": 1,
              "description": "1'den başlayan sayfa numarası; bir çağrıda tek sayfa"}},
    "required": ["url"]}


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
