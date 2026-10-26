"""Mevzuat — kanun, KHK, CB kararnamesi, yönetmelik, tebliğ … (Bedesten / mevzuat.adalet.gov.tr).

Upstream: ``https://bedesten.adalet.gov.tr/mevzuat`` (same rate limit and same
envelope as the içtihat service; the bucket is shared).

    POST /searchDocuments        title / full-text search, 12 legislation types
    POST /getDocumentContent     {"documentType": "MEVZUAT", "id": mevzuatId}
    POST /mevzuatMaddeTree       article tree (içindekiler)
    POST /getDocumentContent     {"documentType": "MADDE", "id": maddeId}
    POST /getGerekceContent      {"gerekceId": gerekceId}

Citation contract: ``SINAİ MÜLKİYET KANUNU (Kanun No. 6769, RG 10.01.2017/29944) m. 120``.
``mevzuatAdi``, ``mevzuatNo``, ``resmiGazeteTarihi`` and ``resmiGazeteSayisi`` come
verbatim from the record; the article designation comes from the article header
found in the text ("MADDE 120-", "GEÇİCİ MADDE 1-", "Madde 169/a –"). Never from
memory, never from a neighbouring article, never inferred when a field is missing
(``citation_missing`` lists what the record lacks).

One-call article lookup
-----------------------
``article(number="6769", madde_no=["120", "geçici 1"])`` resolves the law, reads
the (cached) article tree and returns each article's text, heading and citation.
It exists because verifying one article used to take two calls and a 26-80 KB
tree, and the model skipped it and cited SMK m.120 from memory as the company's
pre-emption right (it is the EMPLOYEE's). What the tree looks like, verified live
2026-09-21 on SMK (104221), İş K. (103054), İİK (102993):

* Ek, geçici and mükerrer articles are NOT tree nodes. SMK geçici 1-6 live inside
  node 191; İş K. ek 1-3 and geçici 1-12 inside node 120. They are sliced out of
  the host node (or the full text) by their exact header.
* ``184 - Diğer Hükümler`` on node 166 is a range node: "MADDE 166 ila 184- (…
  ile ilgili olup yerine işlenmiştir.)". Articles 167-184 have no node.
* ``Madde No: 58`` is a placeholder title: the heading is missing upstream.
* Lettered articles (HMK 183/A) may be a second node with the same maddeNo, or
  sit inside the base article's node (İİK 169/a).
* Nodes after the depth-1 "Diğer Bilgiler" node are duplicates (HMK 304-306 ×5).
* The full text ends with "… İŞLENEMEYEN HÜKÜMLER" (other laws' "MADDE 1 –" and
  "Geçici Madde 1 –"), an amendment table, tariffs and footnotes. Article slicing
  stops before all of them.

Ported from saidsurucu/mevzuat-mcp (MIT) — endpoint discovery and envelope are
his; the transport is rewritten on the standard library and the per-type tool
sprawl is collapsed into one ``mevzuat_ara`` with a ``types`` filter.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from mcpcore import McpError
from net import Http, HttpError, decode
from textx import (count_hits, excerpt, fold_same_len, html_to_text, paginate, pdf_to_text,
                   tr_fold, tr_lower)
from sources import Source
from sources.bedesten_ictihat import BUCKET, HEADERS

try:                                    # Konu süzgeci isteğe bağlıdır.
    import triyaj as _triyaj
except Exception:                       # pragma: no cover - modül yoksa sessiz
    _triyaj = None

BASE = "https://bedesten.adalet.gov.tr/mevzuat"

# Bedesten'in gerçek üst sınırı. 20'den büyük bir pageSize HTTP 400 döndürür:
# "Kayıt sayısı 20'den fazla olamaz" (canlı, 2026-09-20). Şema 50 diyordu.
PAGE_MAX = 20

# Bedesten TEK TARAFLI bir RG tarih aralığını SESSİZCE yok sayar (canlı, 2026-09-20): yalnız
# resmiGazeteTarihiStart verilince 917 kanunun hepsi döner, ikisi birlikte verilince 6. Eksik ucu
# biz doldururuz; yoksa rg_date_from="2024-09-01" diyen çağıran 1926 tarihli kanunları da alır.
_RG_EN_ESKI = "1850-01-01T21:00:00.000Z"
_RG_EN_YENI = "2100-01-01T21:00:00.000Z"

# RG tarihi BOŞ olan kayıtlar tarih süzgecine hiç girmez (canlı, 2026-09-21): 7552 sayılı İklim
# Kanunu (RG 32951) 2025-06..12 listesinde yok; 4857, 2004, 213, 488 de RG tarihsiz. Bunu
# söylemeden verilen liste "bu dönemde başka kanun yok" diye okunur.
_RG_TARIHSIZ_UYARI = (
    "RG tarih süzgeci kullanıldı: Bedesten kaydında RG tarihi BOŞ olan mevzuat bu listeye HİÇ "
    "girmez (ör. 7552 sayılı İklim Kanunu, RG sayı 32951; 4857 İş Kanunu). Liste eksik olabilir. "
    "Dönem taraması için ayrıca rg_number ile ya da number/query ile arayın; boş sonuç 'o dönemde "
    "mevzuat yok' demek değildir.")

# Bir çağrıda metni açılacak torba kanun sayısı (her biri bir Bedesten isteği).
TORBA_AZAMI = 5
# "(9/6/1932 tarihli ve 2004 sayılı İcra ve İflas Kanunu ile ilgili olup, …)".
# Metinde "ile" ile "ilgili" arasına satır sonu girebiliyor; boşluklar \s+ ile geçilir.
_TORBA_RE = re.compile(r"\(([^()]{5,400}?)\s+ile\s+ilgili\s+olup", re.S)
_JENERIK = {"kanun", "kanun hukmunde kararname"}      # "2872 sayılı Kanun" gibi adsız atıflar

_KONU_NOT = ("Konu süzgeci bir ÖN ELEMEDİR ve Resmî Gazete fihristinde eğitildi. Mevzuat "
             "başlıklarında ayrıca ölçüldü: eğitimde görülmemiş 60 başlıkta vergi 4/4, icra 2/2, "
             "enerji 0/3 yakalandı (pozitif az; sayı okuyun). Enerji taramasında konu'ya "
             "güvenmeyin, query ile birlikte kullanın. Torba kanunlarda değiştirilen kanun "
             "adlarına bakılır; adları okunamayan torba kanun ELENMEZ, konu_kaynak='belirsiz' "
             "ile döner. esik=0 süzgeci kapatır.")
_http = Http(BASE, HEADERS, bucket=BUCKET)

TYPES = {
    "KANUN": "Kanun", "KHK": "Kanun Hükmünde Kararname", "TUZUK": "Tüzük",
    "CB_KARARNAME": "Cumhurbaşkanlığı Kararnamesi", "CB_KARAR": "Cumhurbaşkanı Kararı",
    "CB_YONETMELIK": "Cumhurbaşkanlığı / Bakanlar Kurulu Yönetmeliği",
    "CB_GENELGE": "Cumhurbaşkanlığı Genelgesi", "YONETMELIK": "Bakanlar Kurulu Yönetmeliği",
    "KKY": "Kurum ve Kuruluş Yönetmeliği", "UY": "Üniversite Yönetmeliği",
    "TEBLIGLER": "Tebliğ", "MULGA": "Mülga mevzuat",
}

# number ile aramada sıralama: yürürlükteki kanun önce, mülga kayıt en sonda (TR-BED-02:
# "213" ilk sırada bir köy tüzel kişiliği kararı, "2004" ilk sırada İİK'nın MÜLGA kaydı geliyordu).
_TUR_ONCELIK = {"KANUN": 0, "KHK": 1, "CB_KARARNAME": 2, "TUZUK": 3, "CB_YONETMELIK": 4,
                "YONETMELIK": 5, "CB_KARAR": 6, "CB_GENELGE": 7, "KKY": 8, "UY": 9,
                "TEBLIGLER": 10, "MULGA": 99}

# --------------------------------------------------------------------------- #
# Madde getirme ayarları                                                       #
# --------------------------------------------------------------------------- #
MADDE_AZAMI = 10                 # tek çağrıda en çok madde
TAM_METIN_ESIGI = 4              # bu kadar farklı soğuk düğüm gerekiyorsa tam metin TEK istekte çekilir
MADDE_KARAKTER = 6000            # madde başına varsayılan metin sınırı
MADDE_KARAKTER_AZAMI = 20000
SURE_S = float(os.environ.get("MEVZUAT_MADDE_DEADLINE_S", "75"))   # istemci ~100 sn'de keser
_ASGARI_KALAN_S = 8.0            # bundan az süre kaldıysa yeni istek açılmaz
TTL_METIN = float(os.environ.get("MEVZUAT_TEXT_TTL_S", str(6 * 3600)))
TTL_KUNYE = float(os.environ.get("MEVZUAT_META_TTL_S", str(24 * 3600)))
DOGRULANMADI = ("Madde doğrulanamadı — metni görmeden atıf yapmayın; atıf gerekiyorsa yanına açıkça "
                "\"UYARI: veri çekilemedi, teyidiniz gerekli: https://www.mevzuat.gov.tr/\" yazın.")
KOMSU_UYARI = "EN YAKIN MADDEYİ ATIF OLARAK KULLANMAYIN."


# --------------------------------------------------------------------------- #
# Önbellek — süreli, bayt sınırlı, anahtar başına tek uçuş                      #
# --------------------------------------------------------------------------- #
class _Onbellek:
    """Thread-safe TTL cache, byte-bounded LRU, per-key single flight.

    Replaces a clear-all dict that never expired (an amended law's text could be
    served stale for days on a machine that never stops) and cached empty texts
    as valid ones. Errors are never cached: the loader raises and nothing is
    stored. Empty values are never cached: ``size_of`` returns 0 for them.
    """

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._d: "OrderedDict[Any, Tuple[float, float, int, Any]]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self._inflight: Dict[Any, threading.Event] = {}
        self.clock: Callable[[], float] = time.monotonic      # testler saati değiştirebilir

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
            self._bytes = 0

    def _drop(self, key: Any) -> None:
        e = self._d.pop(key, None)
        if e:
            self._bytes -= e[2]

    def peek(self, key: Any) -> Optional[Tuple[Any, float]]:
        """(value, stored_at_wall) or None. Does not load."""
        with self._lock:
            e = self._d.get(key)
            if not e:
                return None
            if e[0] <= self.clock():
                self._drop(key)
                return None
            self._d.move_to_end(key)
            return e[3], e[1]

    def put(self, key: Any, value: Any, ttl: float, size: int) -> None:
        if size <= 0 or size > self.max_bytes:
            return
        with self._lock:
            self._drop(key)
            self._d[key] = (self.clock() + ttl, time.time(), size, value)
            self._bytes += size
            while self._bytes > self.max_bytes and self._d:
                old, _ = next(iter(self._d.items()))
                self._drop(old)

    def get_or_load(self, key: Any, ttl: float, loader: Callable[[], Any],
                    size_of: Callable[[Any], int], wait_s: float = 60.0) -> Tuple[Any, float, bool]:
        """(value, stored_at_wall, from_cache). Concurrent callers of one key share one load."""
        waited = False
        while True:
            with self._lock:
                e = self._d.get(key)
                if e and e[0] > self.clock():
                    self._d.move_to_end(key)
                    return e[3], e[1], True
                if e:
                    self._drop(key)
                ev = self._inflight.get(key)
                leader = ev is None and True or False
                if leader or waited:
                    if leader:
                        ev = threading.Event()
                        self._inflight[key] = ev
                    break
            ev.wait(wait_s)           # önde giden yükleyiciyi bekle, sonra yeniden bak
            waited = True
        try:
            value = loader()
            self.put(key, value, ttl, size_of(value))
            return value, time.time(), False
        finally:
            if leader:
                with self._lock:
                    self._inflight.pop(key, None)
                ev.set()


_CACHE = _Onbellek(int(float(os.environ.get("MEVZUAT_CACHE_MB", "48")) * 1024 * 1024))

# madde_id → mevzuat_id (ağaç indekslenince dolar; eski madde_id çağrısına künye bağlamı verir)
_MADDE_KANUN: Dict[str, str] = {}
_MADDE_KANUN_KILIT = threading.Lock()


def _simdi_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Çağrı başına süre bütçesi                                                    #
# --------------------------------------------------------------------------- #
class _SureDoldu(Exception):
    """Çağrının süre bütçesi bitti; kalan kalemler BUDGET_EXHAUSTED döner."""

    def __init__(self, retry_after_s: float = 20.0) -> None:
        super().__init__("çağrı süre bütçesi doldu")
        self.retry_after_s = retry_after_s


class _BosIcerik(Exception):
    """Bedesten SUCCESS dedi ama metin boş (ya da PDF metni çıkarılamadı). Boş hüküm DEĞİL."""


class _Butce:
    def __init__(self, saniye: float) -> None:
        self.bitis = time.monotonic() + saniye
        self.istek = 0

    def kalan(self) -> float:
        return self.bitis - time.monotonic()


_yerel = threading.local()


def _butce() -> Optional[_Butce]:
    return getattr(_yerel, "butce", None)


class _ButceBaglami:
    """``with _ButceBaglami(75):`` — bu iş parçacığındaki Bedesten istekleri bütçeye tabi."""

    def __init__(self, saniye: float) -> None:
        self.saniye = saniye

    def __enter__(self) -> _Butce:
        self.onceki = _butce()
        self.b = _Butce(self.saniye)
        _yerel.butce = self.b
        return self.b

    def __exit__(self, *exc: Any) -> None:
        _yerel.butce = self.onceki


def _kova_bekleme() -> float:
    """Ortak kovada bir sonraki jetona tahmini bekleme (salt okunur, bilinmiyorsa 0)."""
    b = BUCKET
    try:
        now = time.monotonic()
        nb = float(getattr(b, "_not_before", 0.0) or 0.0)
        if now < nb:
            return nb - now
        tokens = min(b.capacity, b._tokens + (now - b._last) * b.refill_per_s)
        return 0.0 if tokens >= 1.0 else (1.0 - tokens) / b.refill_per_s
    except Exception:  # noqa: BLE001 - kova iç yapısı değişirse tahmin yapılmaz
        return 0.0


def _wrap(data: Dict[str, Any], paging: bool = False) -> Dict[str, Any]:
    out = {"data": data, "applicationName": "UyapMevzuat"}
    if paging:
        out["paging"] = True
    return out


def _rg_iso(d: str, end: bool = False) -> str:
    """DD/MM/YYYY or YYYY-MM-DD → the UTC boundary the API expects (Turkey is UTC+3)."""
    d = d.strip()
    dt = datetime.strptime(d, "%Y-%m-%d") if "-" in d[:5] else datetime.strptime(d, "%d/%m/%Y")
    if end:
        return dt.strftime("%Y-%m-%dT21:00:00.000Z")
    return (dt - timedelta(days=1)).strftime("%Y-%m-%dT21:00:00.000Z")


def _call(path: str, data: Dict[str, Any], paging: bool = False) -> Any:
    """Tek Bedesten isteği. Bir süre bütçesi içindeyse zaman aşımı ve yeniden deneme ona uyar."""
    timeout: Optional[float] = None
    retries = 1
    b = _butce()
    if b is not None:
        kalan, bekle = b.kalan(), _kova_bekleme()
        timeout = min(30.0, kalan - bekle - 5.0)
        if timeout < 5.0:
            raise _SureDoldu(max(10.0, bekle + 5.0))
        retries = 1 if kalan - bekle >= 2 * timeout + 5.0 else 0
    hdrs = {"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"}
    payload = json.dumps(_wrap(data, paging), ensure_ascii=False).encode("utf-8")
    _, rh, raw, _ = _http.request("POST", path, data=payload, headers=hdrs, timeout=timeout,
                                  retries=retries)
    text = decode(raw, rh)
    try:
        body = json.loads(text)
    except ValueError:
        raise HttpError(502, BASE + path, ("JSON olmayan yanıt: " + text[:200]).encode()) from None
    if not isinstance(body, dict):
        raise HttpError(502, BASE + path, b"beklenmeyen yanit bicimi")
    meta = body.get("metadata") or {}
    if meta.get("FMTY") != "SUCCESS":
        raise HttpError(400, BASE + path, str(meta.get("FMTE") or meta).encode())
    return body.get("data")


def _fetch(path: str, data: Dict[str, Any], paging: bool = False) -> Any:
    """Bütçe denetimi + sayaç + ``_call`` (testler ``_call``'ı değiştirir; bütçe yine işler)."""
    b = _butce()
    if b is not None:
        if b.kalan() < _ASGARI_KALAN_S:
            raise _SureDoldu(max(10.0, _kova_bekleme() + 5.0))
        b.istek += 1
    return _call(path, data, paging) if paging else _call(path, data)


_RETRY_IN = re.compile(r"retry in (\d+(?:\.\d+)?)\s*s", re.I)


def _hata(exc: BaseException, ne: str = "kayıt") -> Dict[str, Any]:
    """İstisna → açık hata sözlüğü. Erişilemeyen kaynak boş sonuç gibi gösterilmez."""
    if isinstance(exc, _SureDoldu):
        return {"error": "Çağrı süre bütçesi doldu (istemci ~100 sn'de keser); %s alınamadı. "
                         "Birkaç saniye sonra yineleyin." % ne,
                "error_code": "BUDGET_EXHAUSTED", "retry": True,
                "retry_after_s": round(exc.retry_after_s, 1)}
    if isinstance(exc, _BosIcerik):
        return {"error": "Bedesten %s için BOŞ içerik döndürdü (%s). Bu bir erişim sorunudur; "
                         "hüküm boş demek değildir." % (ne, exc),
                "error_code": "EMPTY_CONTENT", "retry": True, "retry_after_s": 30}
    msg = str(exc)
    if isinstance(exc, HttpError):
        if exc.status == 429:
            m = _RETRY_IN.search(msg)
            ra = float(m.group(1)) if m else 30.0
            return {"error": "Bedesten hız sınırı (10 istek / 30 sn, sunucu genelinde ortak): %s" % msg,
                    "error_code": "RATE_LIMITED", "retry": True, "retry_after_s": max(3.0, ra)}
        if "runtime exception" in msg.lower() or "bulunamad" in tr_fold(msg):
            return {"error": "Bedesten bu kimlikle %s döndürmedi (%s). Kimlik doğru mu? "
                             "tr_mevzuat_ara ile bulun." % (ne, msg[:160]),
                    "error_code": "NOT_FOUND", "retry": False}
        if exc.status in (0, 502, 503, 504):
            return {"error": msg, "error_code": "UPSTREAM_ERROR", "retry": True, "retry_after_s": 10}
    return {"error": msg, "error_code": "UPSTREAM_ERROR", "retry": False}


# --------------------------------------------------------------------------- #
# Kayıt, tarih, atıf                                                           #
# --------------------------------------------------------------------------- #
# Harf?harf: kaynakta bozulmuş boşluk ('KALDIRILARAK?KÜTAHYA', TR-BED-15). Başka hiçbir '?'
# değişmez; yalnız iki harf arasında ve boşluksuz olanı.
_BOZUK_ARA = re.compile(r"(?<=[^\W\d_])[?\ufffd](?=[^\W\d_])")
_KONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffd]")


def _temiz_ad(s: Any) -> str:
    s = _BOZUK_ARA.sub(" ", str(s or ""))
    s = _KONTROL.sub(" ", s)
    return " ".join(s.split())


_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?"
                  r"\s*(Z|[+-]\d{2}:?\d{2})?$")


def _rg_tarih(v: Any) -> str:
    """Kayıttaki RG tarihi → 'YYYY-AA-GG' (Türkiye yerel günü); bilinmiyorsa ''.

    Canlı kayıt '2017-01-10T00:00:00' (saat dilimsiz, yerel gün) verir; eski test
    kaydı '2026-08-26T21:00:00.000Z' biçimindeydi ve [:10] onu BİR GÜN ÖNCESİ
    okuyordu. Saat dilimi taşıyan değer UTC'ye çevrilip +3 saat eklenir (UTC+2
    kış saatli eski tarihlerde gece yarısı 22:00Z'dir; +3 yine aynı güne düşer).
    """
    s = str(v or "").strip()
    if not s:
        return ""
    m = _ISO.match(s)
    if not m:
        m2 = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$", s)
        if m2:
            return "%04d-%02d-%02d" % (int(m2.group(3)), int(m2.group(2)), int(m2.group(1)))
        return ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    tz = m.group(7)
    if not tz or m.group(4) is None:
        return "%04d-%02d-%02d" % (y, mo, d)
    dt = datetime(y, mo, d, int(m.group(4)), int(m.group(5)), int(m.group(6) or 0))
    if tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        hh, mm = int(tz[1:3]), int(tz[-2:])
        dt -= sign * timedelta(hours=hh, minutes=mm)
    return (dt + timedelta(hours=3)).strftime("%Y-%m-%d")


def _tr_date(iso: str) -> str:
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return iso


def _tur(m: Dict[str, Any]) -> str:
    return str(((m.get("mevzuatTur") or {}).get("name")) or "")


def _atif_tabani(m: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Kayıttan künye: 'AD (Kanun No. N, RG gg.aa.yyyy/sayı)'. Eksik alan TAHMİN EDİLMEZ, listelenir.

    RG tarihi yoksa 'RG sayı N' yazılır (4857, 2004, 213 ve 488 canlıda böyle): sayı
    bilinirken atmak, modeli tarihi ezberden eklemeye itiyordu (TR-BED-07).
    """
    title = _temiz_ad(m.get("mevzuatAdi"))
    no = m.get("mevzuatNo")
    tur = _tur(m)
    eksik: List[str] = []
    if not title:
        eksik.append("title")
    s = title
    if no in (None, ""):
        eksik.append("number")
        return s, eksik
    if not tur:
        eksik.append("type")
    ic = ("%s No. %s" % (TYPES.get(tur, tur), no)).strip()
    rg_date = _rg_tarih(m.get("resmiGazeteTarihi"))
    rg_no = str(m.get("resmiGazeteSayisi") or "").strip()
    if rg_date and rg_no:
        ic += ", RG %s/%s" % (_tr_date(rg_date), rg_no)
    elif rg_no:
        ic += ", RG sayı %s" % rg_no
        eksik.append("rg_date")
    elif rg_date:
        ic += ", RG %s" % _tr_date(rg_date)
        eksik.append("rg_number")
    else:
        eksik += ["rg_date", "rg_number"]
    if str(m.get("mukerrer") or "").strip().upper() == "EVET" and (rg_date or rg_no):
        ic += " (mükerrer)"
    return s + " (" + ic + ")", eksik


def _citation(m: Dict[str, Any], tur: Optional[Dict[str, Any]] = None, rg_date: str = "") -> str:
    """Geriye dönük ad; künye yalnız kayıttan (bkz. ``_atif_tabani``)."""
    return _atif_tabani(m)[0]


def _kanun_blogu(m: Dict[str, Any]) -> Dict[str, Any]:
    base, eksik = _atif_tabani(m)
    out = {
        "mevzuat_id": str(m.get("mevzuatId") or ""),
        "number": m.get("mevzuatNo"),
        "title": _temiz_ad(m.get("mevzuatAdi")),
        "type": _tur(m) or None,
        "tertip": m.get("mevzuatTertip"),
        "rg_date": _rg_tarih(m.get("resmiGazeteTarihi")),
        "rg_number": m.get("resmiGazeteSayisi"),
        "mukerrer": m.get("mukerrer"),
        "citation_base": base,
        "citation_missing": eksik,
        "source_url": m.get("url") or "",
    }
    if m.get("_kunyesiz"):
        out["warning"] = ("Bu mevzuat_id için Bedesten aramasında künye kaydı bulunamadı; ad ve numara "
                          "madde ağacının kökünden alındı. Tür ve RG künyesi bilinmiyor — uydurmayın.")
    return out


def _kayit_hatirla(m: Dict[str, Any]) -> None:
    mid = str(m.get("mevzuatId") or "").strip()
    if mid:
        _CACHE.put(("KAYIT", mid), m, TTL_KUNYE, 2048)


def _kayit_onbellekte(mid: str) -> Optional[Dict[str, Any]]:
    hit = _CACHE.peek(("KAYIT", str(mid)))
    return hit[0] if hit else None


def _satir(m: Dict[str, Any]) -> Dict[str, Any]:
    tur = _tur(m)
    base, eksik = _atif_tabani(m)
    out = {
        "mevzuat_id": m.get("mevzuatId"),
        "number": m.get("mevzuatNo"),
        "title": _temiz_ad(m.get("mevzuatAdi")),
        "type": tur or None,
        "tertip": m.get("mevzuatTertip"),
        "rg_date": _rg_tarih(m.get("resmiGazeteTarihi")),
        "rg_number": m.get("resmiGazeteSayisi"),
        "mukerrer": m.get("mukerrer"),
        "gerekce_id": m.get("gerekceId"),
        "citation": base,
        "source_url": m.get("url") or "",
    }
    if eksik:
        out["citation_missing"] = eksik
    return out


def _degistirilen(mevzuat_id: str) -> List[str]:
    """Bir torba kanunun değiştirdiği kanun ADLARI (metnin ilk sayfasından)."""
    g = get({"mevzuat_id": mevzuat_id, "page": 1, "page_chars": 12000})
    adlar: List[str] = []
    for mt in _TORBA_RE.finditer(g.get("text") or ""):
        seg = " ".join(mt.group(1).split())
        ad = re.sub(r"^.*?say[ıi]l[ıi]\s+", "", seg)
        if len(ad) < 8 or " ".join(tr_fold(ad).split()) in _JENERIK:
            continue
        if ad not in adlar:
            adlar.append(ad)
    return adlar


def _konu_ele(items: List[Dict[str, Any]], konu: str, motor: Any, e: float):
    """Başlıkları konu olasılığına göre eler; ``(tutulan, belirsiz_sayısı)``.

    Torba kanun ("… Değişiklik Yapılmasına Dair Kanun") başlığı konuyu taşımaz:
    7531 sayılı Kanun İİK ve HMK'yı değiştirir, adı "Bazı Kanunlarda Değişiklik"tir.
    Başlık eşiği geçemezse değiştirilen kanun ADLARI skorlanır. Adlar okunamazsa
    (istek sınırı, hata, metinde ad yok) kalem ELENMEZ: bilinmeyeni atmak, bir
    hukukçuya "bu dönemde İİK değişmedi" demektir.
    """
    tutulan: List[Dict[str, Any]] = []
    belirsiz = acilan = 0
    for it in items:
        baslik = " ".join((it.get("title") or "").split())
        metin = (_triyaj.TUR_BOLUM.get(it.get("type") or "", "") + " " + baslik).strip()
        p = motor.p(konu, metin)
        kaynak, degisen = "baslik", None
        torba = (it.get("type") == "KANUN"
                 and "degisiklik yapilmasina dair kanun" in " ".join(tr_fold(baslik).split()))
        if torba and p < e:
            adlar: List[str] = []
            if acilan < TORBA_AZAMI:
                acilan += 1
                try:
                    adlar = _degistirilen(str(it.get("mevzuat_id") or ""))
                except Exception:  # noqa: BLE001 - okunamayan torba kanun belirsizdir, hata değil
                    adlar = []
            if not adlar:
                kaynak = "belirsiz"
                belirsiz += 1
            else:
                en_p, en_ad = max(((motor.p(konu, "KANUNLAR " + ad), ad) for ad in adlar),
                                  key=lambda t: t[0])
                if en_p > p:
                    p, kaynak, degisen = en_p, "degistirilen_kanunlar", en_ad
        if kaynak == "belirsiz" or p >= e:
            ek: Dict[str, Any] = {"konu_skoru": round(p, 4), "konu_kaynak": kaynak}
            if degisen:
                ek["konu_degistirilen"] = degisen
            tutulan.append(dict(it, **ek))
    return tutulan, belirsiz


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    q = (args.get("query") or "").strip()
    number = str(args.get("number") or "").strip()
    types = args.get("types") or []
    if isinstance(types, str):
        types = [t.strip() for t in types.split(",") if t.strip()]
    bad = [t for t in types if t not in TYPES]
    if bad:
        return {"error": "Bilinmeyen mevzuat türü %s. Geçerli: %s" % (bad, list(TYPES))}
    # Bedesten sorgu kelimesi olmadan da listeler: tür ve/veya RG tarih aralığı yeter
    # (canlı doğrulandı 2026-09-20). Konu taramasını mümkün kılan şey budur.
    listeleme = bool(types or args.get("rg_date_from") or args.get("rg_date_to") or args.get("rg_number"))
    if not q and not number and not listeleme:
        return {"error": "query, number ya da bir liste süzgeci (types / rg_date_from / "
                         "rg_date_to / rg_number) gerekli."}
    konu = (args.get("konu") or "").strip()
    hazir: Any = None
    if konu:                       # doğrulama AĞA ÇIKMADAN
        if _triyaj is None:
            return {"error": "Konu süzgeci bu kurulumda yok (triyaj modülü yüklü değil)."}
        hazir = _triyaj.hazirla(konu, args.get("esik"))
        if isinstance(hazir, dict):
            return hazir
    where = args.get("search_in") or "title"
    page_size = max(1, min(int(args.get("page_size") or 10), PAGE_MAX))
    page = max(1, int(args.get("page") or 1))
    data: Dict[str, Any] = {
        "pageSize": page_size,
        "pageNumber": page,
        "sortFields": ["RESMI_GAZETE_TARIHI"],
        "sortDirection": "desc",
    }
    if q:
        if where == "fulltext":
            data["phrase"] = q
            data["basliktaAra"] = False
        else:
            data["mevzuatAdi"] = q
    if number:
        data["mevzuatNo"] = number
    if types:
        data["mevzuatTurList"] = types
    if args.get("exact_phrase"):
        data["tamCumle"] = True
    try:
        bas = _rg_iso(args["rg_date_from"]) if args.get("rg_date_from") else ""
        son = _rg_iso(args["rg_date_to"], end=True) if args.get("rg_date_to") else ""
    except ValueError:
        return {"error": "Tarih biçimi YYYY-MM-DD veya DD/MM/YYYY olmalı."}
    if bas or son:                 # tek taraflı aralık upstream'de sessizce yok sayılır
        data["resmiGazeteTarihiStart"] = bas or _RG_EN_ESKI
        data["resmiGazeteTarihiEnd"] = son or _RG_EN_YENI
    if args.get("rg_number"):
        data["resmiGazeteSayisi"] = str(args["rg_number"])

    # konu verilince art arda birkaç sayfa taranabilir; aksi hâlde tek sayfa (eski davranış).
    max_pages = max(1, min(int(args.get("max_pages") or 1), 5)) if konu else 1
    items: List[Dict[str, Any]] = []
    total = 0
    taranan = 0
    sayfa_hatasi = ""
    for n in range(max_pages):
        data["pageNumber"] = page + n
        try:
            d = _call("/searchDocuments", data, paging=True) or {}
        except HttpError as exc:
            if n == 0:
                return _hata(exc, "arama sonucu")
            sayfa_hatasi = "sayfa %d alınamadı: %s" % (page + n, exc)   # eksik ≠ boş
            break
        lst = d.get("mevzuatList") or []
        total = d.get("total", total)
        for m in lst:
            _kayit_hatirla(m)
        items.extend(_satir(m) for m in lst)
        taranan += 1
        if len(lst) < page_size:
            break
    out: Dict[str, Any] = {
        "query": q or number or "(liste)", "total": total, "page": page, "results": items,
        "note": "Madde metni + başlık + hazır atıf TEK çağrıda: tr_mevzuat_madde_getir(number=…, "
                "madde_no=[…]). Tam metin: tr_mevzuat_getir(mevzuat_id). Madde ağacı: "
                "tr_mevzuat_icindekiler. Belirli hükmü aramak için tr_mevzuat_icinde_ara."}
    if number and items:
        # Yürürlükteki kanun önce, MÜLGA kayıt en sonda; aynı türde Bedesten sırası korunur.
        items.sort(key=lambda r: _TUR_ONCELIK.get(r.get("type") or "", 50))
        ayri = {str(r.get("mevzuat_id")) for r in items}
        if len(ayri) > 1:
            out["ambiguous_number"] = True
            out["number_note"] = (
                "%s numarasını %d ayrı kayıt taşıyor (türler: %s). İlk sonucu körü körüne almayın: "
                "kanun için types=['KANUN'] verin; MULGA kaydı YÜRÜRLÜKTEN KALDIRILMIŞ hükümlerdir."
                % (number, len(ayri), ", ".join(str(r.get("type")) for r in items)))
    if bas or son:
        out["possibly_incomplete"] = True
        out["rg_date_warning"] = _RG_TARIHSIZ_UYARI
    if konu:
        motor, e = hazir
        tutulan, belirsiz = _konu_ele(items, konu, motor, e)
        out.update(results=tutulan, konu=konu, esik=e, konu_taranan=len(items),
                   konu_elenen=len(items) - len(tutulan), konu_belirsiz=belirsiz,
                   pages_scanned=taranan, konu_notu=_KONU_NOT)
        if sayfa_hatasi:
            out["pages_error"] = sayfa_hatasi
    return out


# --------------------------------------------------------------------------- #
# İçerik (tam metin / madde düğümü / gerekçe)                                  #
# --------------------------------------------------------------------------- #
def _icerik_yukle(doc_type: str, doc_id: str) -> Dict[str, Any]:
    d = _fetch("/getDocumentContent", {"documentType": doc_type, "id": doc_id}) or {}
    raw = base64.b64decode(d.get("content") or b"")
    mime = d.get("mimeType") or "text/html"
    if "pdf" in mime:
        text, _ = pdf_to_text(raw)
        if not text.strip():
            raise _BosIcerik("PDF metni çıkarılamadı: pypdf yok ya da PDF taranmış")
    else:
        text = html_to_text(raw.decode("utf-8", "replace"))
    if not text.strip():
        raise _BosIcerik("içerik alanı boş")
    return {"text": text, "mime": mime, "fetched_at": _simdi_iso()}


def _icerik(doc_type: str, doc_id: str) -> Tuple[Dict[str, Any], float]:
    """(içerik, önbellek_yaşı_sn). Boş metin önbelleğe girmez; hata önbelleğe girmez."""
    v, stored, _ = _CACHE.get_or_load(
        ("ICERIK", doc_type, str(doc_id)), TTL_METIN, lambda: _icerik_yukle(doc_type, str(doc_id)),
        size_of=lambda c: 4 * len(c.get("text") or "") + 512 if (c.get("text") or "").strip() else 0)
    return v, round(max(0.0, time.time() - stored), 1)


def _content(doc_type: str, doc_id: str) -> Dict[str, Any]:
    return _icerik(doc_type, doc_id)[0]


def _sayfa_boyu(v: Any, varsayilan: int) -> int:
    try:
        n = int(v) if v not in (None, "", 0, "0") else varsayilan
    except (TypeError, ValueError):
        n = varsayilan
    return max(500, min(n, 20000))


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    mid = str(args.get("mevzuat_id") or "").strip()
    if not mid:
        return {"error": "mevzuat_id gerekli (tr_mevzuat_ara sonucundan)."}
    try:
        c, yas = _icerik("MEVZUAT", mid)
    except (HttpError, _SureDoldu, _BosIcerik) as exc:
        return _hata(exc, "mevzuat metni")
    boy = _sayfa_boyu(args.get("page_chars"), 8000)
    out = paginate(c["text"], args.get("page") or 1, boy)
    out.update({"mevzuat_id": mid, "mime_type": c["mime"], "fetched_at": c.get("fetched_at"),
                "cache_age_s": yas})
    rec = _kayit_onbellekte(mid)
    if rec:
        out["citation"] = _atif_tabani(rec)[0]
    return out


def gerekce(args: Dict[str, Any]) -> Dict[str, Any]:
    gid = str(args.get("gerekce_id") or "").strip()
    if not gid:
        return {"error": "gerekce_id gerekli (tr_mevzuat_ara sonucunda gerekce_id alanı; yoksa gerekçe yayımlanmamış)."}

    def yukle() -> Dict[str, Any]:
        d = _fetch("/getGerekceContent", {"gerekceId": gid}) or {}
        raw = base64.b64decode(d.get("content") or b"")
        mime = d.get("mimetype") or d.get("mimeType") or "text/html"
        text = pdf_to_text(raw)[0] if "pdf" in mime else html_to_text(raw.decode("utf-8", "replace"))
        if not text.strip():
            raise _BosIcerik("gerekçe metni boş" + (" (PDF metni çıkarılamadı)" if "pdf" in mime else ""))
        return {"text": text, "mime": mime, "mevzuat_id": d.get("mevzuatId"), "fetched_at": _simdi_iso()}

    try:
        c, stored, _ = _CACHE.get_or_load(("GEREKCE", gid), TTL_METIN, yukle,
                                          size_of=lambda v: 4 * len(v["text"]) + 512)
    except (HttpError, _SureDoldu, _BosIcerik) as exc:
        return _hata(exc, "gerekçe")
    out = paginate(c["text"], args.get("page") or 1, _sayfa_boyu(args.get("page_chars"), 8000))
    icerir = _gerekce_kapsami(c["text"])
    out.update({"gerekce_id": gid, "mevzuat_id": c.get("mevzuat_id"), "mime_type": c["mime"],
                "contains": icerir, "fetched_at": c.get("fetched_at"),
                "cache_age_s": round(max(0.0, time.time() - stored), 1)})
    if "madde" not in icerir:
        out["coverage_note"] = ("Bu gerekçe metninde madde gerekçeleri YOK (yalnız %s). 'Gerekçe X "
                                "maddesi hakkında bir şey söylemiyor' demeyin: madde gerekçesi "
                                "Bedesten'de yayımlanmamış olabilir; TBMM sıra sayısına bakın."
                                % (", ".join(icerir) or "başlıksız metin"))
    return out


def _gerekce_kapsami(text: str) -> List[str]:
    f = fold_same_len(text)
    out = []
    if re.search(r"genel\s+gerekce", f):
        out.append("genel")
    if re.search(r"madde\s+gerekce|^[ \t]*madde\s+\d+\s*[-–—]", f, re.M):
        out.append("madde")
    if re.search(r"komisyon\s+raporu|komisyonu\s+raporu", f):
        out.append("komisyon")
    return out


# --------------------------------------------------------------------------- #
# Madde metni çözümleme                                                        #
# --------------------------------------------------------------------------- #
# Hepsi fold_same_len(metin) üzerinde çalışır (tek karaktere tek karakter: konumlar özgün
# metinde de geçerli). Satır başında: [Ek|Geçici|Mükerrer] Madde N[/harf][ ila M] + tire.
# Tireden önce yumuşak tire (U+00AD) de olabilir: TBK m.464 Bedesten'de "MADDE 464\xad- …".
_TIRE = "\\-\u2013\u2014\u2212\u2010\u2011"
_BASLIK_SATIRI = re.compile(
    r"^[ \t]*(?:(ek|gecici|mukerrer)\s+)?madde\s+(\d{1,4})"
    r"(?:[ \t]*/[ \t]*([a-z])(?![a-z0-9]))?"
    r"(?:\s+ila\s+(?:madde\s+)?(\d{1,4}))?"
    r"[ \t\u00ad]*[" + _TIRE + "]", re.M)
# Yalnız katı desen hiçbir başlık bulamazsa: "Madde 1." / "Madde 1:" biçimli eski metinler.
_BASLIK_SATIRI_GEVSEK = re.compile(
    r"^[ \t]*(?:(ek|gecici|mukerrer)\s+)?madde\s+(\d{1,4})"
    r"(?:[ \t]*/[ \t]*([a-z])(?![a-z0-9]))?"
    r"(?:\s+ila\s+(?:madde\s+)?(\d{1,4}))?"
    r"[ \t\u00ad]*[" + _TIRE + ".:]", re.M)
# Tiresiz başlık: TBK m.428 Bedesten metninde "MADDE 428 İşyerinin…" diye, tiresiz gelir. Özgün
# (katlanmamış) metinde aranır: yalnız BÜYÜK harfli MADDE, ardından büyük harf ya da "(" gelirse.
# Kabul koşulları _tiresizleri_ekle'de (numara komşu başlıkların arasında olmalı).
_BASLIK_TIRESIZ = re.compile(r"^[ \t]*MADDE[ \t]+(\d{1,4})(?=[ \t]+[A-ZÇĞİÖŞÜÂÎÛ(])", re.M)
# Kanun gövdesinin bittiği yerler (ilki geçerli). İşlenemeyen hükümler bölümü BAŞKA kanunların
# "MADDE 1 –", "Geçici Madde 1 –" başlıklarını taşır; gövdeye katılırsa yanlış madde döner.
_ISLENEMEYEN = re.compile(r"^[^\n]{0,160}?islenem[ei]yen\s+(?:hukum|gecici|madde|ek\b)", re.M)
_DEGISIKLIK_TABLOSU = re.compile(
    r"^[^\n]{0,120}?(?:ek\s+ve\s+degisiklik\s+getiren\s+mevzuat|"
    r"yururluge\s+giris\s+tarihlerini\s+gosterir)", re.M)
_EK_CETVEL = re.compile(r"^[ \t]*\(\s*[0-9ivxlc]{1,4}\s*\)\s*sayili\s+(?:tarife|cetvel|liste)\b[^\n]{0,80}$",
                        re.M)
_DIPNOT_SATIRI = re.compile(r"^[ \t]*\[(\d{1,4})\][ \t]*(?=\S)", re.M)
_DIPNOT_REF_SON = re.compile(r"(?:\s*\[\d{1,4}\])+\s*$")
_KUCUK = re.compile(r"[a-zçğıöşüâîû]")
_YAPI = re.compile(r"^(?:[a-z]+\s+){1,3}(?:alt\s+)?(?:kitap|kisim|bolum|ayirim|bap|bab|fasil|kesim)"
                   r"(?:\s*[-–:.]\s*.{0,80})?$")
_YAPI_BUYUK = re.compile(r"^(?:[a-z]+\s+){0,4}(?:hukumler|hukumleri|maddeler|maddeleri)\s*:?$")
_LISTE_ISARETI = re.compile(r"^(?:[a-zçğıöşü]|\d{1,3})\s*[).\-–]\s")

_KIND_TR = {"madde": "", "ek": "ek ", "gecici": "geçici ", "mukerrer": "mükerrer "}


def _baslik_gibi(satir: str) -> bool:
    """Bir maddenin kenar başlığı olabilecek tek satır mı? (Sonraki maddeye taşınacak satır.)"""
    s = satir.strip()
    if not s or len(s) > 200 or " | " in s:
        return False
    govde = _DIPNOT_REF_SON.sub("", s).strip()
    if not govde or govde[0] in "([\"'“«‘":
        return False
    if _BASLIK_SATIRI.match(fold_same_len(govde)):
        return False
    if re.fullmatch(r"[\d\s.,/()\-–]+", govde):
        return False
    if _LISTE_ISARETI.match(govde) and not govde.endswith(":"):
        return False           # "a) Kıdem tazminatı." bir bent; "a) Borca itiraz:" eski usul başlık
    if govde[-1] in ".;,":
        return False
    return any(ch.isalpha() for ch in govde)


def _yapi_basligi(satir: str) -> bool:
    """'İKİNCİ KISIM', 'Birinci Bölüm', 'GEÇİCİ MADDELER' gibi yapı başlığı mı?"""
    s = " ".join(satir.split())
    if not s or len(s) > 100:
        return False
    f = fold_same_len(s)
    if _YAPI.match(f):
        return True
    return bool(_YAPI_BUYUK.match(f)) and not _KUCUK.search(s)


def _kunye_parcasi(satir: str) -> bool:
    """Gövde sonundaki büyük harfli künye parçası: '9/6/1932 TARİHLİ VE 2004 SAYILI …', '6098'."""
    s = _DIPNOT_REF_SON.sub("", satir).strip()
    if not s or _KUCUK.search(s):
        return False
    if re.fullmatch(r"\d{1,5}", s):
        return True
    f = fold_same_len(s)
    return bool(re.search(r"\b(sayili|tarihli|tarih)\b", f)) and len(s) <= 200


def _norm_baslik(s: Optional[str]) -> str:
    """Başlık karşılaştırması için: katla, bent işareti / dipnot / noktalama at."""
    s = _DIPNOT_REF_SON.sub("", s or "").strip()
    s = _LISTE_ISARETI.sub("", s)
    s = tr_fold(s)
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


def _baslik_temiz(s: Optional[str]) -> Tuple[Optional[str], List[str]]:
    """Metinden alınan başlık: sondaki [n] dipnot işaretleri ayrılır."""
    if not s:
        return None, []
    s = s.strip()
    m = _DIPNOT_REF_SON.search(s)
    refs = re.findall(r"\[(\d+)\]", m.group(0)) if m else []
    return (_DIPNOT_REF_SON.sub("", s).strip() or None), refs


def _satirlar(text: str, bas: int, son: int) -> Tuple[List[str], List[int]]:
    lines = text[bas:son].split("\n")
    offs, o = [], bas
    for ln in lines:
        offs.append(o)
        o += len(ln) + 1
    return lines, offs


def _kuyruk(text: str, seg_bas: int, govde_bas: int, seg_son: int,
            sonra_baslik_var: bool) -> Tuple[int, Optional[str]]:
    """Maddenin gerçek sonu ve (varsa) BİR SONRAKİ maddenin kenar başlığı.

    Metin '…119 gövdesi\\n\\nÇalışanın önalım hakkı\\n\\nMADDE 120- …' diye akar: başlık
    satırı bir önceki parçanın sonunda kalır. Eski bölücü onu 119'a veriyordu (SMK
    119/120 karışıklığının aynısı, TR-BED-01). Burada o satır bir sonraki maddeye
    taşınır; 'İKİNCİ KISIM / Diğer Hükümler' gibi yapı başlıkları iki maddeye de verilmez.
    """
    lines, offs = _satirlar(text, seg_bas, seg_son)

    def son_dolu(j: int) -> Optional[int]:
        k = j - 1
        while k >= 0 and not lines[k].strip():
            k -= 1
        if k < 0 or offs[k] <= govde_bas:        # başlık satırının kendisine dokunulmaz
            return None
        return k

    j = len(lines)
    sonraki: Optional[str] = None
    if sonra_baslik_var:
        k = son_dolu(j)
        if k is not None and _baslik_gibi(lines[k]) and not _yapi_basligi(lines[k]):
            p = son_dolu(k)
            if p is not None and _yapi_basligi(lines[p]):
                j = p                            # bölüm alt başlığı: iki satır da düşer
            else:
                sonraki = lines[k].strip()
                j = k
    while True:
        k = son_dolu(j)
        if k is None:
            break
        if _yapi_basligi(lines[k]) or _kunye_parcasi(lines[k]):
            j = k
            continue
        if _baslik_gibi(lines[k]):
            p = son_dolu(k)
            if p is not None and _yapi_basligi(lines[p]):
                j = p
                continue
        break
    k = j - 1
    while k >= 0 and not lines[k].strip():
        k -= 1
    son = offs[k] + len(lines[k]) if k >= 0 else seg_bas
    return max(son, min(seg_son, govde_bas)), sonraki


def _onsoz_basligi(text: str, bas: int) -> Optional[str]:
    """İlk başlıktan önceki son satır kenar başlık mı? (Düğüm metni başlıkla başlar.)"""
    lines = text[:bas].rstrip().split("\n")
    lines = [ln for ln in lines if ln.strip()]
    if not lines:
        return None
    son = lines[-1]
    if not _baslik_gibi(son) or _yapi_basligi(son):
        return None
    if len(lines) >= 2 and _yapi_basligi(lines[-2]):
        return None
    return son.strip()


def _govde_sonu(text: str, f: str) -> int:
    son = len(text)
    for rx in (_ISLENEMEYEN, _DEGISIKLIK_TABLOSU):
        m = rx.search(f)
        if m and m.start() < son:
            son = m.start()
    for m in _EK_CETVEL.finditer(f):
        if m.start() >= son:
            break
        if not _KUCUK.search(_DIPNOT_REF_SON.sub("", text[m.start():m.end()])):
            son = m.start()       # "(4) SAYILI TARİFE" — büyük harfli ek başlığı
            break
    return son


def _tiresizleri_ekle(text: str, basliklar: List[Dict[str, Any]], govde_son: int) -> List[Dict[str, Any]]:
    """Tiresiz "MADDE 428 İşyerinin…" başlıklarını, yalnız güvenli durumda, listeye ekler.

    Koşullar: satır başında BÜYÜK harfli MADDE; numaradan sonra büyük harf ya da "("; aynı numara
    zaten düz başlık değil; numara önceki ve sonraki madde başlığının numaraları ARASINDA. Komşu
    başlık hiç yoksa (tek maddelik düğüm metni) aday metnin ilk satırı olmalı. "Madde 12 uyarınca…"
    gibi satır başına düşmüş atıflar küçük harfli ya da sırasız olduğundan başlık sayılmaz.
    """
    duz = {b["no"] for b in basliklar if b["kind"] == "madde" and b["harf"] is None}
    ekler: List[Dict[str, Any]] = []
    for m in _BASLIK_TIRESIZ.finditer(text, 0, govde_son):
        no = int(m.group(1))
        if no in duz:
            continue
        once = [b for b in basliklar if b["kind"] == "madde" and b["bas"] < m.start()]
        sonra = [b for b in basliklar if b["kind"] == "madde" and b["bas"] > m.start()]
        if not once and not sonra:
            if text[:m.start()].strip():
                continue
        if once and (once[-1]["aralik_son"] or once[-1]["no"]) >= no:
            continue
        if sonra and sonra[0]["no"] <= no:
            continue
        duz.add(no)
        ekler.append({
            "bas": m.start(), "govde_bas": m.end(), "kind": "madde", "no": no,
            "harf": None, "harf_ham": None, "aralik_son": None,
            "etiket": " ".join(text[m.start():m.end()].split()),
            "on_baslik": None, "son": None})
    if not ekler:
        return basliklar
    return sorted(basliklar + ekler, key=lambda b: b["bas"])


def _kaynak_coz(text: str) -> Dict[str, Any]:
    """Metni madde başlıklarına böler; her başlığın sonu ve kenar başlığı hesaplanır."""
    f = fold_same_len(text)
    govde_son = _govde_sonu(text, f)
    heads = list(_BASLIK_SATIRI.finditer(f, 0, govde_son))
    gevsek = False
    if not heads:
        heads = list(_BASLIK_SATIRI_GEVSEK.finditer(f, 0, govde_son))
        gevsek = bool(heads)
    basliklar: List[Dict[str, Any]] = []
    for h in heads:
        kind = {"ek": "ek", "gecici": "gecici", "mukerrer": "mukerrer"}.get(h.group(1) or "", "madde")
        harf_ham = text[h.start(3)] if h.group(3) else None
        basliklar.append({
            "bas": h.start(), "govde_bas": h.end(), "kind": kind, "no": int(h.group(2)),
            "harf": tr_lower(harf_ham) if harf_ham else None, "harf_ham": harf_ham,
            "aralik_son": int(h.group(4)) if h.group(4) else None,
            "etiket": " ".join(text[h.start():h.end()].replace("­", "").split()),
            "on_baslik": None, "son": None})
    basliklar = _tiresizleri_ekle(text, basliklar, govde_son)
    son_bas = basliklar[-1]["govde_bas"] if basliklar else 0
    m = _DIPNOT_SATIRI.search(f, son_bas, govde_son)
    dipnot_bas = m.start() if m else govde_son
    govde_son = dipnot_bas
    # gövde sonundaki künye parçaları ("9/6/1932 TARİHLİ VE 2004 SAYILI … KANUNUNA")
    lines, offs = _satirlar(text, son_bas, govde_son)
    k = len(lines) - 1
    while k >= 0:
        if not lines[k].strip():
            k -= 1
            continue
        if offs[k] > son_bas and _kunye_parcasi(lines[k]):
            govde_son = offs[k]
            k -= 1
            continue
        break
    for i, b in enumerate(basliklar):
        seg_son = basliklar[i + 1]["bas"] if i + 1 < len(basliklar) else govde_son
        b["son"], sonraki = _kuyruk(text, b["bas"], b["govde_bas"], seg_son, i + 1 < len(basliklar))
        if i + 1 < len(basliklar):
            basliklar[i + 1]["on_baslik"] = sonraki
    if basliklar:
        basliklar[0]["on_baslik"] = _onsoz_basligi(text, basliklar[0]["bas"])
    dipnotlar: Dict[str, str] = {}
    ms = list(_DIPNOT_SATIRI.finditer(f, dipnot_bas))
    for i, dm in enumerate(ms):
        e = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        dipnotlar[dm.group(1)] = " ".join(text[dm.end():e].split())
    return {"text": text, "basliklar": basliklar, "govde_son": govde_son, "dipnot_bas": dipnot_bas,
            "dipnotlar": dipnotlar, "gevsek": gevsek}


def _cozumle(c: Dict[str, Any]) -> Dict[str, Any]:
    """Önbellekteki içerik sözlüğüne bir kez çözümleme ekler (belirleyici; yarış zararsız)."""
    k = c.get("_cozum")
    if k is None:
        k = _kaynak_coz(c["text"])
        c["_cozum"] = k
    return k


def _tasarim(kind: str, no: int, harf: Optional[str], aralik_son: Optional[int] = None) -> str:
    s = _KIND_TR[kind] + str(no) + ("/" + harf if harf else "")
    if aralik_son:
        s += " ila %d" % aralik_son
    return s


# --------------------------------------------------------------------------- #
# Madde ağacı ve indeks                                                        #
# --------------------------------------------------------------------------- #
_MADDE_NO_BASLIK = re.compile(r"^madde no:\s*(\d+)\s*(?:-\s*(.*))?$", re.I)
_ARALIK_BASLIK = re.compile(r"^(\d{1,4})\s*[-–]\s*(.+)$")


def _baslik_coz(no: int, title: Any, madde_baslik: Any) -> Tuple[Optional[str], bool, List[str], Optional[int]]:
    """Ağaç düğümü başlığı → (başlık, yer_tutucu_mu, dipnot_işaretleri, aralık_sonu)."""
    t = str(title or "").strip()
    if not t:
        m = _MADDE_NO_BASLIK.match(str(madde_baslik or "").strip())
        if m and (m.group(2) or "").strip():
            t = m.group(2).strip()
        else:
            return None, True, [], None               # 'Madde No: 58' — başlık yok
    if re.match(r"^madde no:\s*\d+$", t, re.I):
        return None, True, [], None
    t, refs = _baslik_temiz(t)
    son = None
    m = _ARALIK_BASLIK.match(t or "")
    if m:
        e = int(m.group(1))
        if no < e <= no + 300:                        # '184 - Diğer Hükümler' @166 → 166..184
            son, t = e, m.group(2).strip()
        elif e == no:                                 # '183 - Maddi hataların düzeltilmesi' @183
            t = m.group(2).strip()
    return (t or None), False, refs, son


def _indeks_kur(raw: Any, mid: str) -> Dict[str, Any]:
    kok = raw if isinstance(raw, dict) else {"children": raw if isinstance(raw, list) else []}
    maddeler: List[Dict[str, Any]] = []
    yapi: List[Dict[str, Any]] = []
    durum = {"ek_bolum": False, "sira": 0}

    def walk(n: Any, depth: int) -> None:
        if not isinstance(n, dict):
            return
        mno = n.get("maddeNo")
        title = str(n.get("title") or "").strip()
        if mno in (None, ""):
            if depth <= 1 and " ".join(tr_fold(title).split()) == "diger bilgiler":
                durum["ek_bolum"] = True
            if depth > 0 and not durum["ek_bolum"]:
                yapi.append({"madde_id": str(n.get("maddeId") or ""), "title": title,
                             "desc": str(n.get("description") or "").strip(), "depth": depth})
        else:
            try:
                no = int(str(mno).strip())
            except ValueError:
                no = None
            if no is not None:
                baslik, yer_tutucu, refs, son = _baslik_coz(no, n.get("title"), n.get("maddeBaslik"))
                maddeler.append({
                    "madde_id": str(n.get("maddeId") or n.get("id") or ""), "no": no,
                    "heading": baslik, "placeholder": yer_tutucu, "refs": refs,
                    "range": [no, son] if son else None, "sira": durum["sira"],
                    "ek_bolum": durum["ek_bolum"], "gerekce_id": n.get("gerekceId"), "depth": depth})
                durum["sira"] += 1
        for ch in n.get("children") or []:
            walk(ch, depth + 1)

    if isinstance(raw, list):
        for n in raw:
            walk(n, 0)
    else:
        walk(kok, 0)
    ana = [m for m in maddeler if not m["ek_bolum"]]
    no_map: Dict[int, List[Dict[str, Any]]] = {}
    for m in ana:
        no_map.setdefault(m["no"], []).append(m)
    aralik_map: Dict[int, List[Dict[str, Any]]] = {}
    for m in ana:
        if m["range"]:
            for k in range(m["range"][0] + 1, m["range"][1] + 1):
                aralik_map.setdefault(k, []).append(m)
    ev = None                          # gömülü ek/geçici maddelerin ev sahibi tahmini
    for i, m in enumerate(ana):
        if m["heading"] and _norm_baslik(m["heading"]) == "yururluk":
            ev = ana[i - 1] if i > 0 else None
            break
    if ev is None and ana:
        ev = ana[-3] if len(ana) >= 3 else ana[-1]
    idx = {
        "mevzuat_id": mid, "maddeler": maddeler, "ana": ana, "no_map": no_map,
        "aralik_map": aralik_map, "ev_sahibi": ev,
        "by_id": {m["madde_id"]: m for m in maddeler},
        "max_no": max((m["range"][1] if m["range"] else m["no"] for m in ana), default=None),
        "kok_no": str(kok.get("title") or "").strip(), "kok_ad": _temiz_ad(kok.get("description")),
        "yapi": yapi, "ek_bolum_sayisi": len(maddeler) - len(ana),
    }
    with _MADDE_KANUN_KILIT:
        if len(_MADDE_KANUN) > 200000:
            _MADDE_KANUN.clear()
        for m in maddeler:
            if m["madde_id"]:
                _MADDE_KANUN[m["madde_id"]] = mid
    return idx


def _agac(mid: str) -> Dict[str, Any]:
    def yukle() -> Dict[str, Any]:
        raw = _fetch("/mevzuatMaddeTree", {"mevzuatId": mid})
        if not raw:
            raise _BosIcerik("madde ağacı boş")
        boy = len(json.dumps(raw, ensure_ascii=False))
        return {"raw": raw, "idx": _indeks_kur(raw, mid), "fetched_at": _simdi_iso(), "_boy": boy}

    v, _, _ = _CACHE.get_or_load(("AGAC", str(mid)), TTL_KUNYE, yukle,
                                 size_of=lambda a: 3 * a["_boy"] + 4096)
    return v


def _agac_onbellekte(mid: str) -> Optional[Dict[str, Any]]:
    hit = _CACHE.peek(("AGAC", str(mid)))
    return hit[0] if hit else None


def _eski_dugumler(raw: Any) -> List[Dict[str, Any]]:
    """Eski tr_mevzuat_icindekiler satırları (varsayılan çıktı değişmesin diye aynen)."""
    nodes: List[Dict[str, Any]] = []

    def walk(n: Dict[str, Any], depth: int) -> None:
        if not isinstance(n, dict):
            return
        title = (n.get("title") or n.get("maddeBaslik") or n.get("description") or "").strip()
        mno = n.get("maddeNo")
        if title or mno:
            nodes.append({"madde_id": n.get("maddeId") or n.get("id"), "madde_no": mno,
                          "title": title[:200], "depth": depth, "gerekce_id": n.get("gerekceId")})
        for ch in n.get("children") or []:
            walk(ch, depth + 1)

    if isinstance(raw, list):
        for n in raw:
            walk(n, 0)
    else:
        walk(raw, 0)
    return nodes


# --------------------------------------------------------------------------- #
# Kanun çözme                                                                  #
# --------------------------------------------------------------------------- #
class _KanunHatasi(Exception):
    def __init__(self, kod: str, mesaj: str, adaylar: Optional[List[Dict[str, Any]]] = None) -> None:
        super().__init__(mesaj)
        self.kod, self.mesaj, self.adaylar = kod, mesaj, adaylar or []

    def metin(self) -> str:
        s = "%s: %s" % (self.kod, self.mesaj)
        if self.adaylar:
            s += " Adaylar: " + json.dumps(self.adaylar, ensure_ascii=False)
        return s + " " + DOGRULANMADI

    def sozluk(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"error": self.metin(), "error_code": self.kod, "retry": False}
        if self.adaylar:
            out["candidates"] = self.adaylar
        return out


def _aday(m: Dict[str, Any]) -> Dict[str, Any]:
    return {"mevzuat_id": str(m.get("mevzuatId") or ""), "title": _temiz_ad(m.get("mevzuatAdi")),
            "type": _tur(m) or None, "tertip": m.get("mevzuatTertip"),
            "rg_date": _rg_tarih(m.get("resmiGazeteTarihi")), "rg_number": m.get("resmiGazeteSayisi")}


def _numara(v: Any) -> str:
    s = str(v if v is not None else "").strip()
    if not s:
        return ""
    if not re.fullmatch(r"\d{1,6}", s):
        raise _KanunHatasi("BAD_REQUEST", "number yalnız rakam olmalı (ör. \"6769\"); gelen: %r." % s)
    return s


def _turler(v: Any, number: str) -> Optional[List[str]]:
    if v in (None, "", []):
        return ["KANUN"] if number else None
    if isinstance(v, str):
        v = [t.strip() for t in v.split(",") if t.strip()]
    bad = [t for t in v if t not in TYPES]
    if bad:
        raise _KanunHatasi("BAD_REQUEST", "Bilinmeyen mevzuat türü %s. Geçerli: %s" % (bad, list(TYPES)))
    return list(v)


def _numara_ara(number: str, types: Optional[List[str]], tertip: Any) -> List[Dict[str, Any]]:
    key = ("NUMARA", number, tuple(types or ()), str(tertip if tertip not in (None, "") else ""))

    def yukle() -> List[Dict[str, Any]]:
        data: Dict[str, Any] = {"pageSize": PAGE_MAX, "pageNumber": 1, "mevzuatNo": number,
                                "sortFields": ["RESMI_GAZETE_TARIHI"], "sortDirection": "desc"}
        if types:
            data["mevzuatTurList"] = list(types)
        d = _fetch("/searchDocuments", data, paging=True) or {}
        rows = []
        for r in d.get("mevzuatList") or []:
            if str(r.get("mevzuatNo")).strip() != number:
                continue          # Bedesten numarayı başka alanlarda da eşleyebilir
            if types and _tur(r) not in types:
                continue
            if tertip not in (None, "") and str(r.get("mevzuatTertip")) != str(tertip):
                continue
            _kayit_hatirla(r)
            rows.append(r)
        return rows

    v, _, _ = _CACHE.get_or_load(key, TTL_KUNYE, yukle, size_of=lambda rows: 2048 * len(rows))
    return v


def _kanun_bul(number: str, mid: str, types: Optional[List[str]], tertip: Any) -> Dict[str, Any]:
    """Kanun kaydı. Birden çok kayıt → AMBIGUOUS_LAW (asla kendiliğinden seçilmez)."""
    if mid:
        rec = _kayit_onbellekte(mid)
        if rec is None:
            idx = _agac(mid)["idx"]
            aranan = number or (idx["kok_no"] if re.fullmatch(r"\d{1,6}", idx["kok_no"] or "") else "")
            for r in (_numara_ara(aranan, None, None) if aranan else []):
                if str(r.get("mevzuatId")) == str(mid):
                    rec = r
                    break
            if rec is None:
                rec = {"mevzuatId": mid, "mevzuatNo": idx["kok_no"] or None,
                       "mevzuatAdi": idx["kok_ad"] or "", "_kunyesiz": True}
        if number and str(rec.get("mevzuatNo") or "") not in ("", number):
            raise _KanunHatasi("BAD_REQUEST", "number=%s ile mevzuat_id=%s farklı kayıtları gösteriyor "
                                              "(kaydın numarası %s)." % (number, mid, rec.get("mevzuatNo")))
        return rec
    rows = _numara_ara(number, types, tertip)
    if not rows:
        adaylar: List[Dict[str, Any]] = []
        if types == ["KANUN"]:
            try:
                adaylar = [_aday(r) for r in _numara_ara(number, None, tertip)]
            except (HttpError, _SureDoldu):
                adaylar = []
        raise _KanunHatasi(
            "LAW_NOT_FOUND",
            "%s numaralı %s bulunamadı.%s" % (
                number, "/".join(types or ["mevzuat"]),
                " Aynı numarayı taşıyan başka türde kayıtlar var; doğru türü types ile verin."
                if adaylar else " Numara doğru mu? tr_mevzuat_ara(query=…) ile adından arayın."),
            adaylar)
    if len(rows) > 1:
        raise _KanunHatasi(
            "AMBIGUOUS_LAW",
            "%s numarasını %d kayıt taşıyor; kendiliğinden seçilmez. mevzuat_id, types veya tertip "
            "ile birini seçin (MULGA = yürürlükten kaldırılmış hükümler)." % (number, len(rows)),
            [_aday(r) for r in rows])
    return rows[0]


# --------------------------------------------------------------------------- #
# madde_no sözdizimi                                                           #
# --------------------------------------------------------------------------- #
_MADDE_SOZ = re.compile(
    r"^(?:(ek|gecici|gec\.?|mukerrer|muk\.?)\s*)?"
    r"(?:madde\s*|md\.?\s*|m\.?\s*)?"
    r"(\d{1,4})"
    r"(?:\s*/\s*([a-z])(?![a-z])|\s*/\s*(\d{1,2}))?\s*\.?$")
_SOZ_ORNEK = '"120", "m. 120", "170/a", "geçici 1", "ek 3", "mükerrer 257", "12/3" (m.12 fıkra 3)'


def _madde_coz(ham: Any) -> Dict[str, Any]:
    s = " ".join(str(ham if ham is not None else "").split())
    f = fold_same_len(s)
    m = _MADDE_SOZ.match(f)
    if not m:
        return {"requested": s, "hata": "BAD_SYNTAX"}
    kind = {"ek": "ek", "gecici": "gecici", "gec": "gecici", "gec.": "gecici", "mukerrer": "mukerrer",
            "muk": "mukerrer", "muk.": "mukerrer"}.get(m.group(1) or "", "madde")
    harf = tr_lower(s[m.start(3)]) if m.group(3) else None
    fikra = int(m.group(4)) if m.group(4) else None
    no = int(m.group(2))
    return {"requested": s, "kind": kind, "no": no, "harf": harf, "fikra": fikra,
            "tasarim": _tasarim(kind, no, harf)}


def _madde_listesi(v: Any) -> List[Dict[str, Any]]:
    if isinstance(v, (list, tuple)):
        ham = list(v)
    else:
        ham = [v]
    ham = [x for x in ham if str(x if x is not None else "").strip()]
    if not ham:
        raise _KanunHatasi("BAD_REQUEST", "madde_no boş. Biçim: %s." % _SOZ_ORNEK)
    kalemler, gorulen = [], set()
    for x in ham:
        k = _madde_coz(x)
        anahtar = k.get("tasarim", k["requested"]) + "|%s" % k.get("fikra")
        if anahtar in gorulen:
            continue
        gorulen.add(anahtar)
        kalemler.append(k)
    if len(kalemler) > MADDE_AZAMI:
        raise _KanunHatasi("BAD_REQUEST", "Tek çağrıda en çok %d madde (tekrarlar ayıklandıktan sonra %d "
                                          "kaldı). İkiye bölün." % (MADDE_AZAMI, len(kalemler)))
    return kalemler


def _azami_karakter(v: Any) -> int:
    try:
        n = int(v) if v not in (None, "", 0) else MADDE_KARAKTER
    except (TypeError, ValueError):
        n = MADDE_KARAKTER
    return max(500, min(n, MADDE_KARAKTER_AZAMI))


# --------------------------------------------------------------------------- #
# Madde bulma, dilimleme, kalem                                               #
# --------------------------------------------------------------------------- #
def _eslesir(b: Dict[str, Any], k: Dict[str, Any], katli: bool) -> bool:
    if b["kind"] != k["kind"]:
        return False
    if k["harf"] is None:
        if b["harf"] is not None:
            return False                   # 'MADDE 169' istenirken '169/a' ASLA dönmez
        if b["no"] == k["no"]:
            return True
        return b["aralik_son"] is not None and b["no"] <= k["no"] <= b["aralik_son"]
    if b["harf"] is None or b["no"] != k["no"]:
        return False
    if katli:
        return fold_same_len(b["harf"]) == fold_same_len(k["harf"])
    return b["harf"] == k["harf"]


def _bul(kaynak: Dict[str, Any], k: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    bs = kaynak["basliklar"]
    tam = [i for i, b in enumerate(bs) if _eslesir(b, k, False)]
    if tam:
        return tam[0], ("Metinde bu başlık %d kez geçiyor; ilki alındı." % len(tam) if len(tam) > 1 else None)
    if k["harf"]:
        yakin = [i for i, b in enumerate(bs) if _eslesir(b, k, True)]
        if len(yakin) == 1:
            return yakin[0], "Harf yalnız ı/i, ç/c gibi katlanınca eşleşti: '%s'." % bs[yakin[0]]["etiket"]
    return None, None


_ANNOT = re.compile(r"\(\s*(?:ek|degisik|mulga|iptal|yeniden\s+duzenleme)[^():]{0,60}:[^()]{0,300}\)")
_ISLENMIS = re.compile(r"ile\s+ilgili\s+olup,?\s+(?:yerine\s+)?islenmistir|"
                       r"ilgili\s+kanun(?:lar)?a\s+islenmistir")


def _durum(govde: str) -> Tuple[str, Optional[str]]:
    """Başlık satırından sonraki gövdeye göre durum: text / repealed / annulled / incorporated_elsewhere."""
    f = fold_same_len(govde)
    temiz = re.sub(r"\[\d{1,4}\]", " ", f)
    temiz = re.sub(r"\(\s*(?:ek|degisik)[^():]{0,60}:[^()]{0,300}\)", " ", temiz)
    temiz = " ".join(temiz.split()).strip(" .;")
    if re.fullmatch(r"\(\s*mulga\b[^()]*\)", temiz) or temiz in ("mulga", "(mulga)"):
        return "repealed", None
    if re.fullmatch(r"\(\s*iptal\b[^()]*\)", temiz):
        return "annulled", None
    if len(temiz) < 700 and _ISLENMIS.search(temiz):
        return "incorporated_elsewhere", " ".join(govde.split())
    return "text", None


def _serhler(metin: str) -> List[str]:
    f = fold_same_len(metin)
    return [" ".join(metin[m.start():m.end()].split()) for m in _ANNOT.finditer(f)]


def _sahip(idx: Optional[Dict[str, Any]], b: Dict[str, Any], on_baslik: Optional[str]) -> Optional[Dict[str, Any]]:
    """Tam metinden dilimlenen maddenin ağaçtaki kendi düğümü (varsa)."""
    if not idx or b["kind"] != "madde":
        return None
    ayni = idx["no_map"].get(b["no"], [])
    if b["harf"] is None:
        if ayni:
            return ayni[0]
        return None
    if len(ayni) > 1 and on_baslik:
        n = _norm_baslik(on_baslik)
        es = [m for m in ayni[1:] if m["heading"] and _norm_baslik(m["heading"]) == n]
        if len(es) == 1:
            return es[0]
    return None


def _ev_sahibi_tam_metin(kaynak: Dict[str, Any], i: int, idx: Optional[Dict[str, Any]]) -> Optional[str]:
    """Gömülü maddenin ev sahibi: kendinden önceki son düz 'MADDE n' başlığının düğümü."""
    if not idx:
        return None
    for b in reversed(kaynak["basliklar"][:i]):
        if b["kind"] == "madde" and b["harf"] is None:
            ayni = idx["no_map"].get(b["no"]) or idx["aralik_map"].get(b["no"]) or []
            return ayni[0]["madde_id"] if ayni else None
    return None


_FIKRA_PARANTEZ = r"^[ \t]*\(\s*%d\s*\)"
_FIKRA_NOKTA = r"^[ \t]*%d\.(?=[ \t])"


def _noktali_birinci(metin: str) -> Optional[int]:
    """Fıkralar "1." / "2." diye mi numaralı (İYUK m.45, VUK m.112 gibi)? Öyleyse "1."in konumu.

    Yalnız metinde "(1)" biçimi hiç yoksa ve gövde, başlık tiresinden sonra (şerh ve dipnot
    işaretleri atlanarak) doğrudan "1." ile başlıyorsa. "Madde 13 – Mücbir sebepler:\\n1. …" gibi
    gövdesi başka sözle başlayan maddedeki "1." bir bent/sıra numarasıdır, fıkra sayılmaz.
    """
    if re.search(r"^[ \t]*\(\s*\d{1,3}\s*\)", metin, re.M):
        return None
    ilk = metin.split("\n", 1)[0]
    t = re.search("[" + _TIRE + ".:]", fold_same_len(ilk))
    if not t:
        return None
    p = t.end()
    while True:
        m = re.match(r"[ \t]*(?:\[\d{1,4}\]|\([^()\n]*\))", ilk[p:])
        if not m:
            break
        p += m.end()
    m = re.match(r"\s*(1\.)(?=[ \t])", metin[p:])
    return p + m.start(1) if m else None


def _fikra(metin: str, n: int) -> Optional[str]:
    bir = _noktali_birinci(metin)
    desen = _FIKRA_PARANTEZ if bir is None else _FIKRA_NOKTA
    if bir is not None and n == 1:
        bas, ara = bir, bir + 2
    else:
        m = re.compile(desen % n, re.M).search(metin, bir or 0)
        if not m:
            return None
        bas, ara = m.start(), m.end()
    e = re.compile(desen % (n + 1), re.M).search(metin, ara)
    return metin[bas: e.start() if e else len(metin)].strip()


def _kalem(k: Dict[str, Any], kaynak: Dict[str, Any], i: int, kaynak_turu: str, dugum_id: Optional[str],
           idx: Optional[Dict[str, Any]], law: Optional[Dict[str, Any]], azami: int,
           c: Dict[str, Any], yas: float, uyari: Optional[str]) -> Dict[str, Any]:
    b = kaynak["basliklar"][i]
    text = kaynak["text"]
    metin = text[b["bas"]:b["son"]].strip()
    govde = text[b["govde_bas"]:b["son"]]
    on_baslik, on_refs = _baslik_temiz(b["on_baslik"])

    sahip = None
    if kaynak_turu != "full_text" and dugum_id:
        node = idx["by_id"].get(dugum_id) if idx else None
        birinci = i == 0
        if node and birinci and (node["no"] == b["no"] or (node["range"] and node["range"][0] <= b["no"] <= node["range"][1])):
            ilk_dugum = (idx["no_map"].get(node["no"]) or [node])[0]
            if b["kind"] == "madde" and (b["harf"] is None or node is not ilk_dugum):
                sahip = node
            elif b["kind"] == "mukerrer" and node is not ilk_dugum:
                sahip = node
        elif node is None and birinci and b["kind"] == "madde" and not idx:
            sahip = {"madde_id": dugum_id, "heading": None, "placeholder": True, "refs": [], "range": None}
    elif kaynak_turu == "full_text":
        sahip = _sahip(idx, b, on_baslik)

    if sahip and sahip.get("heading") and not sahip.get("placeholder"):
        heading, h_kaynak, refs = sahip["heading"], "tree", list(sahip.get("refs") or [])
    elif on_baslik:
        heading, h_kaynak, refs = on_baslik, "text", on_refs
    else:
        heading, h_kaynak, refs = None, None, []
    madde_id = sahip.get("madde_id") if sahip else None
    if kaynak_turu == "full_text":
        host = madde_id or _ev_sahibi_tam_metin(kaynak, i, idx)
        source = "full_text"
    else:
        host = dugum_id
        source = "madde_node" if sahip else "host_slice"

    durum, hedef = _durum(govde)
    kesik = len(metin) > azami
    item: Dict[str, Any] = {
        "requested": k["requested"], "ok": True, "kind": k["kind"],
        "madde_no": _tasarim(b["kind"], k["no"], b["harf"]), "madde_no_int": k["no"],
        "suffix": b["harf"], "paragraph_hint": k["fikra"],
        "heading": heading, "heading_source": h_kaynak,
        "text": metin[:azami], "truncated": kesik, "chars": len(metin),
        "status": durum, "annotations": _serhler(metin),
        "footnotes": {}, "range": [b["no"], b["aralik_son"]] if b["aralik_son"] else None,
        "madde_id": madde_id, "host_madde_id": host, "source": source,
        "header": b["etiket"], "fetched_at": c.get("fetched_at"), "cache_age_s": yas,
    }
    if sahip and sahip.get("placeholder") and kaynak_turu != "full_text" and idx:
        item["heading_note"] = "Bedesten ağacında bu maddenin başlığı yok ('Madde No: %d')." % sahip["no"]
    if sahip and sahip.get("heading") and on_baslik and _norm_baslik(sahip["heading"]) != _norm_baslik(on_baslik):
        item["heading_text"] = on_baslik
        item["heading_mismatch"] = True
    if refs:
        item["heading_footnote_refs"] = refs
    if hedef:
        item["target_text"] = hedef
        item["status_note"] = ("Bu hüküm BAŞKA bir kanuna işlenmiştir; metni burada yoktur. İşlendiği "
                               "kanunun ilgili maddesini ayrıca getirin; bu maddeyi o içerikle alıntılamayın.")
    if durum == "repealed":
        item["status_note"] = "Madde MÜLGA (yürürlükten kaldırılmış). Yürürlükteki hüküm gibi atıf yapmayın."
    elif durum == "annulled":
        item["status_note"] = "Madde AYM kararıyla İPTAL edilmiş. Yürürlükteki hüküm gibi atıf yapmayın."
    if kesik:
        item["truncated_note"] = ("Metin %d karakterde kesildi (toplam %d). Tamamı için "
                                  "max_chars_per_article artırın (en çok %d)." % (azami, len(metin), MADDE_KARAKTER_AZAMI))
    if item["range"]:
        item["range_note"] = ("'%s' başlığı %d-%d maddelerini TEK düğümde toplar; madde %d'in kendi "
                              "metni yoktur." % (b["etiket"], b["no"], b["aralik_son"], k["no"]))
    for n in sorted(set(re.findall(r"\[(\d{1,4})\]", (b["on_baslik"] or "") + metin)), key=int):
        if n in kaynak["dipnotlar"]:
            item["footnotes"][n] = kaynak["dipnotlar"][n]
    ek = " " + ("m." if b["kind"] == "madde" else _KIND_TR[b["kind"]].strip() + " m.") + " " + str(k["no"])
    if b["harf"]:
        ek += "/" + (b["harf_ham"] or b["harf"])
    if k["fikra"] is not None:
        par = _fikra(metin, k["fikra"])
        if par:
            ek += "/%d" % k["fikra"]
            item["paragraph_text"] = par[:azami]
        else:
            item["paragraph_not_found"] = True
            item["paragraph_note"] = ("Metinde '(%d)' ya da '%d.' numaralı fıkra yok; fıkraları sayarak "
                                      "atıf yapmayın, metinden doğrulayın." % (k["fikra"], k["fikra"]))
    if law:
        item["citation"] = law["citation_base"] + ek
        if law.get("citation_missing"):
            item["citation_missing"] = list(law["citation_missing"])
    else:
        item["citation"] = None
        item["citation_missing"] = ["law"]
    if uyari:
        item["warning"] = uyari
    return item


def _hata_kalemi(k: Dict[str, Any], kod: str, mesaj: str, retry: bool = False,
                 retry_after_s: Optional[float] = None, **ek: Any) -> Dict[str, Any]:
    out = {"requested": k.get("requested"), "ok": False, "error_code": kod,
           "message": mesaj + ("" if kod in ("BAD_SYNTAX",) else " " + DOGRULANMADI),
           "retry": retry}
    if retry_after_s is not None:
        out["retry_after_s"] = round(retry_after_s, 1)
    out.update(ek)
    return out


def _istisna_kalemi(k: Dict[str, Any], exc: BaseException) -> Dict[str, Any]:
    h = _hata(exc, "madde metni")
    kod = h.get("error_code") or "UPSTREAM_ERROR"
    if kod in ("NOT_FOUND", "EMPTY_CONTENT"):
        kod = "UPSTREAM_ERROR"
    return _hata_kalemi(k, kod, h["error"], bool(h.get("retry")), h.get("retry_after_s"))


def _adaylar(idx: Dict[str, Any], k: Dict[str, Any]) -> List[str]:
    """Denenecek madde düğümleri, sırayla (sonra tam metin)."""
    ayni = idx["no_map"].get(k["no"], [])
    if k["kind"] == "madde" and k["harf"] is None:
        c = [m["madde_id"] for m in ayni] + [m["madde_id"] for m in idx["aralik_map"].get(k["no"], [])]
    elif k["kind"] == "madde":
        sira = "abcçdefgğhıijklmnoöprsştuüvyz".find(k["harf"])
        oncelik = ayni[sira + 1] if 0 <= sira and sira + 1 < len(ayni) else None
        c = ([oncelik["madde_id"]] if oncelik else []) + [m["madde_id"] for m in ayni if m is not oncelik]
    elif k["kind"] == "mukerrer":
        c = [m["madde_id"] for m in ayni[1:]] + [m["madde_id"] for m in ayni[:1]]
    else:
        c = [idx["ev_sahibi"]["madde_id"]] if idx.get("ev_sahibi") else []
    out: List[str] = []
    for x in c:
        if x and x not in out:
            out.append(x)
    return out


def _bulunamadi(k: Dict[str, Any], idx: Dict[str, Any], tam_metin_bakildi: bool) -> Dict[str, Any]:
    en_buyuk = idx.get("max_no")
    ne = _tasarim(k["kind"], k["no"], k["harf"])
    mesaj = ("'%s' bu mevzuatta bulunamadı (madde ağacındaki en büyük madde numarası: %s%s). %s"
             % (ne, en_buyuk if en_buyuk is not None else "?",
                "; tam metin de tarandı" if tam_metin_bakildi else "", KOMSU_UYARI))
    return _hata_kalemi(k, "NOT_FOUND", mesaj, False, max_madde_no=en_buyuk)


def _kalemleri_coz(law_rec: Dict[str, Any], agac: Dict[str, Any], kalemler: List[Dict[str, Any]],
                   azami: int) -> List[Dict[str, Any]]:
    idx = agac["idx"]
    mid = str(law_rec.get("mevzuatId") or idx["mevzuat_id"])
    law = _kanun_blogu(law_rec)
    sonuc: List[Optional[Dict[str, Any]]] = [None] * len(kalemler)
    plan: List[Tuple[int, Dict[str, Any], List[str]]] = []
    for n, k in enumerate(kalemler):
        if k.get("hata"):
            sonuc[n] = _hata_kalemi(k, "BAD_SYNTAX", "Anlaşılamayan madde_no %r. Biçim: %s."
                                    % (k["requested"], _SOZ_ORNEK))
        else:
            plan.append((n, k, _adaylar(idx, k)))

    def onbellekte(doc_type: str, i: str) -> bool:
        return _CACHE.peek(("ICERIK", doc_type, str(i))) is not None

    soguk = {a[0] for _, _, a in plan if a and not onbellekte("MADDE", a[0])}
    tam_mod = onbellekte("MEVZUAT", mid) or len(soguk) >= TAM_METIN_ESIGI
    durum: Dict[str, Any] = {"tam_hata": None, "sure_bitti": None}

    def tam_metin() -> Tuple[Dict[str, Any], float]:
        if durum["tam_hata"] is not None:
            raise durum["tam_hata"]
        try:
            return _icerik("MEVZUAT", mid)
        except (HttpError, _SureDoldu, _BosIcerik) as exc:
            durum["tam_hata"] = exc              # tam metin bir çağrıda bir kez denenir
            raise

    for n, k, adaylar in plan:
        kaynaklar: List[Tuple[str, Optional[str]]] = []
        if tam_mod:
            # Tam metinde başlık bulunamazsa (bozuk/tiresiz başlık) maddenin kendi düğümlerine de
            # bakılır; tam metin hiç gelemediyse düğümlere tek tek gidilmez (aşağıda break).
            kaynaklar = [("full_text", None)] + [("node", a) for a in adaylar]
        else:
            kaynaklar = [("node", a) for a in adaylar] + [("full_text", None)]
        bulundu = None
        son_hata: Optional[BaseException] = None
        dugum_var_baslik_yok: Optional[str] = None
        tam_bakildi = False
        for tur, ident in kaynaklar:
            if tam_mod and tur == "node" and durum["tam_hata"] is not None:
                break
            if durum["sure_bitti"] is not None:
                cached = onbellekte("MADDE", ident) if tur == "node" else onbellekte("MEVZUAT", mid)
                if not cached:
                    son_hata = durum["sure_bitti"]
                    continue
            try:
                if tur == "node":
                    c, yas = _icerik("MADDE", ident)
                else:
                    tam_bakildi = True
                    c, yas = tam_metin()
            except _SureDoldu as exc:
                durum["sure_bitti"] = exc
                son_hata = exc
                continue
            except (HttpError, _BosIcerik) as exc:
                son_hata = exc
                continue
            kaynak = _cozumle(c)
            i, uyari = _bul(kaynak, k)
            if i is None:
                if tur == "node" and ident == (adaylar[0] if adaylar else None):
                    dugum_var_baslik_yok = ident
                continue
            bulundu = _kalem(k, kaynak, i, "full_text" if tur == "full_text" else "node", ident, idx,
                             law, azami, c, yas, uyari)
            break
        if bulundu is not None:
            sonuc[n] = bulundu
        elif son_hata is not None and (not tam_bakildi or durum["tam_hata"] is not None or
                                       isinstance(son_hata, _SureDoldu)):
            if isinstance(son_hata, _SureDoldu):
                sonuc[n] = _hata_kalemi(k, "BUDGET_EXHAUSTED",
                                        "Çağrı süre bütçesi doldu (istemci ~100 sn'de keser); bu madde "
                                        "getirilemedi. Yalnız bu maddeyle yineleyin.", True,
                                        son_hata.retry_after_s)
            else:
                sonuc[n] = _istisna_kalemi(k, son_hata)
        elif dugum_var_baslik_yok and k["kind"] == "madde" and k["harf"] is None and adaylar and \
                idx["by_id"].get(dugum_var_baslik_yok, {}).get("no") == k["no"]:
            sonuc[n] = _hata_kalemi(
                k, "SLICE_FAILED",
                "Ağaçta bu maddenin düğümü var (madde_id=%s) ama ne düğüm metninde ne tam metinde '%s' "
                "başlığı bulunabildi; metin verilmedi. Düğümü ham okumak için tr_mevzuat_madde_getir"
                "(madde_id=\"%s\"). %s" % (dugum_var_baslik_yok, _tasarim(k["kind"], k["no"], k["harf"]),
                                           dugum_var_baslik_yok, KOMSU_UYARI),
                False, host_madde_id=dugum_var_baslik_yok)
        else:
            sonuc[n] = _bulunamadi(k, idx, tam_bakildi)
    return [s for s in sonuc if s is not None]


def _dugum_icinde(aid: str, kalemler: List[Dict[str, Any]], azami: int,
                  law_rec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """madde_id + madde_no: yalnız o düğümün metninde, tam başlık eşleşmesiyle."""
    law = _kanun_blogu(law_rec) if law_rec else None
    idx = None
    mid = str(law_rec.get("mevzuatId")) if law_rec else _MADDE_KANUN.get(aid)
    if mid:
        a = _agac_onbellekte(mid)
        idx = a["idx"] if a else None
        if law is None:
            rec = _kayit_onbellekte(mid)
            law = _kanun_blogu(rec) if rec else None
    c, yas = _icerik("MADDE", aid)
    kaynak = _cozumle(c)
    items = []
    for k in kalemler:
        if k.get("hata"):
            items.append(_hata_kalemi(k, "BAD_SYNTAX", "Anlaşılamayan madde_no %r. Biçim: %s."
                                      % (k["requested"], _SOZ_ORNEK)))
            continue
        i, uyari = _bul(kaynak, k)
        if i is None:
            items.append(_hata_kalemi(
                k, "NOT_FOUND", "madde_id=%s düğümünde '%s' başlığı yok (bu düğümdeki maddeler: %s). Tüm "
                "kanunda aramak için number veya mevzuat_id + madde_no verin. %s"
                % (aid, _tasarim(k["kind"], k["no"], k["harf"]),
                   ", ".join(_tasarim(b["kind"], b["no"], b["harf"], b["aralik_son"])
                             for b in kaynak["basliklar"]) or "yok", KOMSU_UYARI), False))
            continue
        items.append(_kalem(k, kaynak, i, "node", aid, idx, law, azami, c, yas, uyari))
    out: Dict[str, Any] = {"law": law, "madde_id": aid, "complete": all(x.get("ok") for x in items),
                           "fetched_at": _simdi_iso(), "items": items}
    if law is None:
        out["citation_note"] = ("Bu çağrı kanun künyesini bilmiyor; citation boş. Atıf için "
                                "tr_mevzuat_madde_getir(number=…, madde_no=…) kullanın.")
    return out


def _eski_madde(aid: str) -> Dict[str, Any]:
    """Eski {madde_id} çağrısı: aynı anahtarlar + ek alanlar."""
    try:
        c, yas = _icerik("MADDE", aid)
    except (HttpError, _SureDoldu, _BosIcerik) as exc:
        return _hata(exc, "madde metni")
    out: Dict[str, Any] = {"madde_id": aid, "text": c["text"], "mime_type": c["mime"],
                           "fetched_at": c.get("fetched_at"), "cache_age_s": yas}
    kaynak = _cozumle(c)
    tasarimlar = [_tasarim(b["kind"], b["no"], b["harf"], b["aralik_son"]) for b in kaynak["basliklar"]]
    out["articles_in_node"] = tasarimlar
    if len(tasarimlar) > 1:
        out["note"] = ("Bu düğüm %d madde içeriyor (%s). Düğüm metnini tek madde gibi alıntılamayın; "
                       "tek madde için tr_mevzuat_madde_getir(mevzuat_id=…, madde_no=\"…\")."
                       % (len(tasarimlar), ", ".join(tasarimlar)))
    mid = _MADDE_KANUN.get(aid)
    a = _agac_onbellekte(mid) if mid else None
    rec = _kayit_onbellekte(mid) if mid else None
    node = a["idx"]["by_id"].get(aid) if a else None
    if node and kaynak["basliklar"]:
        b = kaynak["basliklar"][0]
        out["mevzuat_id"] = mid
        out["madde_no"] = _tasarim(b["kind"], b["no"], b["harf"], b["aralik_son"])
        out["heading"] = node["heading"]
        if rec and b["kind"] == "madde" and b["no"] == node["no"]:
            ek = " m. %d" % b["no"] + ("/" + (b["harf_ham"] or b["harf"]) if b["harf"] else "")
            out["citation"] = _atif_tabani(rec)[0] + ek
    if "citation" not in out:
        out["citation_note"] = ("Bu çağrı kanun künyesini bilmez: atıf için tr_mevzuat_madde_getir"
                                "(number=…, madde_no=…) kullanın; kanun adını/RG'yi ezberden eklemeyin.")
    return out


def article(args: Dict[str, Any]) -> Dict[str, Any]:
    """tr_mevzuat_madde_getir: tek çağrıda madde metni + başlık + atıf (ya da eski madde_id)."""
    istenen = args.get("madde_no")
    if istenen in (None, "", []):
        istenen = args.get("madde")
    aid = str(args.get("madde_id") or "").strip()
    if aid and istenen in (None, "", []):
        return _eski_madde(aid)
    try:
        number = _numara(args.get("number"))
        mid = str(args.get("mevzuat_id") or "").strip()
        if istenen in (None, "", []):
            raise _KanunHatasi("BAD_REQUEST", "madde_no gerekli: ör. tr_mevzuat_madde_getir(number=\"6769\", "
                                              "madde_no=\"120\") ya da madde_no=[\"120\", \"geçici 1\"].")
        if not (aid or number or mid):
            raise _KanunHatasi("BAD_REQUEST", "number (kanun numarası, ör. \"6769\") ya da mevzuat_id gerekli.")
        types = _turler(args.get("types"), number)
        kalemler = _madde_listesi(istenen)
        if not any(not k.get("hata") for k in kalemler):
            raise _KanunHatasi("BAD_REQUEST", "madde_no anlaşılamadı: %s. Biçim: %s."
                               % (", ".join(repr(k["requested"]) for k in kalemler), _SOZ_ORNEK))
    except _KanunHatasi as e:
        raise McpError(e.metin()) from None
    azami = _azami_karakter(args.get("max_chars_per_article"))
    with _ButceBaglami(SURE_S) as butce:
        try:
            if aid:
                rec = _kanun_bul(number, mid, types, args.get("tertip")) if (number or mid) else None
                out = _dugum_icinde(aid, kalemler, azami, rec)
                out["requests"] = butce.istek
                return out
            rec = _kanun_bul(number, mid, types, args.get("tertip"))
            agac = _agac(str(rec.get("mevzuatId")))
        except _KanunHatasi as e:
            raise McpError(e.metin()) from None
        except (HttpError, _SureDoldu, _BosIcerik) as exc:
            h = _hata(exc, "kanun künyesi / madde ağacı")
            raise McpError("%s: %s (retry=%s%s). %s" % (
                h.get("error_code"), h["error"], h.get("retry"),
                ", retry_after_s=%s" % h["retry_after_s"] if h.get("retry_after_s") else "",
                DOGRULANMADI)) from None
        items = _kalemleri_coz(rec, agac, kalemler, azami)
        tamam = all(x.get("ok") for x in items)
        out = {"law": _kanun_blogu(rec), "complete": tamam, "fetched_at": _simdi_iso(),
               "requests": butce.istek, "items": items,
               "note": ("Her maddenin citation alanını BİREBİR kullanın ve maddeye yüklediğiniz içeriğin "
                        "(hakkın sahibi, şart, süre, sonuç) heading ve text ile örtüştüğünü kontrol edin. "
                        "ok=false olan madde DOĞRULANMADI: ezberden yazmayın; yanına açıkça "
                        "\"UYARI: veri çekilemedi, teyidiniz gerekli: https://www.mevzuat.gov.tr/\" yazın.")}
        if not tamam:
            out["incomplete_note"] = ("Bazı maddeler getirilemedi (items[].error_code). retry=true olanları "
                                      "birkaç saniye sonra yalnız onlarla yineleyin.")
        return out


# --------------------------------------------------------------------------- #
# İçindekiler                                                                  #
# --------------------------------------------------------------------------- #
_TOC_NOT = ("Madde metni + başlık + hazır atıf TEK çağrıda: tr_mevzuat_madde_getir(mevzuat_id=…, "
            "madde_no=[\"120\", \"geçici 1\"]). Ek/geçici/mükerrer maddeler bu ağaçta AYRI DÜĞÜM "
            "DEĞİLDİR (bir ev sahibi maddenin metnindedir; ör. SMK geçici 1-6 → 191): onları "
            "tr_mevzuat_madde_getir(madde_no=\"geçici 1\") ile alın. 'Madde No: N' başlığı Bedesten'de "
            "başlığın olmadığını gösterir; '184 - Diğer Hükümler' gibi bir başlık 166-184 aralığını "
            "tek düğümde toplar.")


def _int_or_none(v: Any) -> Optional[int]:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def toc(args: Dict[str, Any]) -> Dict[str, Any]:
    mid = str(args.get("mevzuat_id") or "").strip()
    try:
        number = _numara(args.get("number"))
        if not mid and not number:
            return {"error": "mevzuat_id ya da number gerekli (ör. number=\"6769\")."}
        types = _turler(args.get("types"), number)
    except _KanunHatasi as e:
        return e.sozluk()
    rec: Optional[Dict[str, Any]] = None
    with _ButceBaglami(SURE_S):
        try:
            if not mid:
                rec = _kanun_bul(number, "", types, args.get("tertip"))
                mid = str(rec.get("mevzuatId"))
            agac = _agac(mid)
        except _KanunHatasi as e:
            return e.sozluk()
        except (HttpError, _SureDoldu, _BosIcerik) as exc:
            return _hata(exc, "madde ağacı")
        law: Dict[str, Any]
        try:
            law = _kanun_blogu(rec or _kanun_bul("", mid, None, None))
        except (_KanunHatasi, HttpError, _SureDoldu, _BosIcerik) as exc:
            law = {"mevzuat_id": mid, "citation_base": None,
                   "error": "Künye alınamadı: %s" % (exc.mesaj if isinstance(exc, _KanunHatasi) else exc)}
    idx = agac["idx"]
    ortak: Dict[str, Any] = {
        "law": law, "max_madde_no": idx["max_no"],
        "ranges": [m["range"] for m in idx["ana"] if m["range"]],
        "placeholder_headings": [m["no"] for m in idx["ana"] if m["placeholder"]],
        "fetched_at": agac.get("fetched_at"), "note": _TOC_NOT}
    if idx["ek_bolum_sayisi"]:
        ortak["duplicate_nodes"] = idx["ek_bolum_sayisi"]
        ortak["duplicate_note"] = ("'Diğer Bilgiler' düğümünden sonra %d madde düğümü daha var (mükerrer "
                                   "kopyalar); kanunun maddeleri onlardan öncekilerdir." % idx["ek_bolum_sayisi"])
    compact = bool(args.get("compact"))
    madde_from, madde_to = _int_or_none(args.get("madde_from")), _int_or_none(args.get("madde_to"))
    hq = " ".join(tr_fold(str(args.get("heading_query") or "")).split())
    filtre = madde_from is not None or madde_to is not None or bool(hq)
    if not compact and not filtre and args.get("limit") in (None, "") and args.get("offset") in (None, ""):
        nodes = _eski_dugumler(agac["raw"])            # varsayılan çıktı: eskisiyle aynı satırlar
        out = {"mevzuat_id": mid, "total": len(nodes), "articles": nodes}
        out.update(ortak)
        return out
    # Seçmeli, küçük çıktı
    tekrar: Dict[int, int] = {}
    satirlar: List[Any] = []
    yapi_dahil = bool(args.get("include_structure"))
    yapi_by_sira: List[Tuple[int, Dict[str, Any]]] = []
    for m in idx["ana"]:
        tekrar[m["no"]] = tekrar.get(m["no"], 0) + 1
        lo, hi = (m["range"][0], m["range"][1]) if m["range"] else (m["no"], m["no"])
        if madde_from is not None and hi < madde_from:
            continue
        if madde_to is not None and lo > madde_to:
            continue
        if hq and hq not in " ".join(tr_fold(m["heading"] or "").split()):
            continue
        etiket = "%d-%d" % (lo, hi) if m["range"] else str(m["no"])
        if tekrar[m["no"]] > 1:
            etiket += " [%d]" % tekrar[m["no"]]
        if compact:
            satirlar.append("%s | %s | %s" % (etiket, m["heading"] or "(başlık yok)", m["madde_id"]))
        else:
            satirlar.append({"madde_id": m["madde_id"], "madde_no": m["no"], "label": etiket,
                             "heading": m["heading"], "range": m["range"],
                             "placeholder": m["placeholder"] or None})
    if yapi_dahil:
        yapi_by_sira = [(0, y) for y in idx["yapi"]]
    toplam = len(satirlar)
    offset = max(0, _int_or_none(args.get("offset")) or 0)
    limit = max(1, min(_int_or_none(args.get("limit")) or 400, 2000))
    secilen = satirlar[offset:offset + limit]
    out = {"mevzuat_id": mid, "total": toplam, "returned": len(secilen), "offset": offset,
           "has_more": offset + len(secilen) < toplam}
    out["rows" if compact else "articles"] = secilen
    if compact:
        out["row_format"] = "madde_no | başlık | madde_id  ('[2]' = aynı numaralı ikinci düğüm, ör. 183/A)"
    if yapi_dahil:
        out["structure"] = [("%s - %s" % (y["title"], y["desc"]) if y["desc"] else y["title"])
                            for _, y in yapi_by_sira]
    out.update(ortak)
    return out


# --------------------------------------------------------------------------- #
# Mevzuat içinde arama                                                         #
# --------------------------------------------------------------------------- #
_DURAK = {"ve", "veya", "ile", "icin", "bu", "bir", "da", "de", "ki", "olarak", "olan", "gibi",
          "her", "en", "az", "ya", "ise", "the"}
_KUYRUK_ISARETLERI = (
    (_ISLENEMEYEN, "İŞLENEMEYEN HÜKÜMLER (başka kanunların hükümleri)"),
    (_DEGISIKLIK_TABLOSU, "DEĞİŞİKLİK / YÜRÜRLÜK TABLOSU"),
)


def _kuyruk_parcalari(kaynak: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Gövdeden sonraki bölümler: işlenemeyen hükümler, tarife/cetveller, tablo, dipnotlar."""
    text = kaynak["text"]
    bas = kaynak["govde_son"]
    if bas >= len(text):
        return []
    f = fold_same_len(text)
    isaretler: List[Tuple[int, str]] = []
    for rx, ad in _KUYRUK_ISARETLERI:
        for m in rx.finditer(f, bas):
            isaretler.append((m.start(), ad))
            break
    for m in _EK_CETVEL.finditer(f, bas):
        satir = text[m.start():m.end()]
        if not _KUCUK.search(_DIPNOT_REF_SON.sub("", satir)):
            isaretler.append((m.start(), " ".join(_DIPNOT_REF_SON.sub("", satir).split())))
    dm = _DIPNOT_SATIRI.search(f, bas)
    if dm:
        isaretler.append((dm.start(), "DİPNOTLAR"))
    isaretler.sort()
    if not isaretler or isaretler[0][0] > bas:
        isaretler.insert(0, (bas, "KANUN SONU EKLERİ"))
    out = []
    for i, (p, ad) in enumerate(isaretler):
        e = isaretler[i + 1][0] if i + 1 < len(isaretler) else len(text)
        govde = text[p:e].strip()
        if govde:
            out.append({"etiket": ad, "tasarim": None, "kind": "ek_bolum", "title": None,
                        "govde": govde, "madde_id": None})
    return out


def search_within(args: Dict[str, Any]) -> Dict[str, Any]:
    """Tam metni madde başlıklarından böler, kelime isabetine göre sıralar.

    Kenar başlığı KENDİ maddesine bağlanır (eskiden bir önceki maddenin sonunda
    kalıyordu: 'önalım' araması 119'u 120'nin başlığıyla eşliyordu). Başlığı eşleşen
    madde önce gelir; sonra daha çok farklı terimi içeren; sonra isabet sayısı.
    """
    mid = str(args.get("mevzuat_id") or "").strip()
    q = (args.get("query") or "").strip()
    try:
        number = _numara(args.get("number"))
    except _KanunHatasi as e:
        return e.sozluk()
    if not (mid or number) or not q:
        return {"error": "mevzuat_id (ya da number) ve query gerekli."}
    with _ButceBaglami(SURE_S):
        try:
            if not mid:
                rec = _kanun_bul(number, "", _turler(args.get("types"), number), args.get("tertip"))
                mid = str(rec.get("mevzuatId"))
            c, yas = _icerik("MEVZUAT", mid)
        except _KanunHatasi as e:
            return e.sozluk()
        except (HttpError, _SureDoldu, _BosIcerik) as exc:
            return _hata(exc, "mevzuat metni")
    kaynak = _cozumle(c)
    text = kaynak["text"]
    agac = _agac_onbellekte(mid)
    idx = agac["idx"] if agac else None
    parcalar: List[Dict[str, Any]] = []
    for i, b in enumerate(kaynak["basliklar"]):
        on_baslik, _ = _baslik_temiz(b["on_baslik"])
        sahip = _sahip(idx, b, on_baslik)
        title = (sahip["heading"] if sahip and sahip.get("heading") else None) or on_baslik
        parcalar.append({"etiket": b["etiket"].rstrip(" -–—:.").strip(),
                         "tasarim": _tasarim(b["kind"], b["no"], b["harf"], b["aralik_son"]),
                         "kind": b["kind"], "title": title, "govde": text[b["bas"]:b["son"]],
                         "madde_id": sahip["madde_id"] if sahip else None})
    madde_sayisi = len(parcalar)
    if parcalar:
        parcalar += _kuyruk_parcalari(kaynak)
    else:
        step = 2500
        parcalar = [{"etiket": "bölüm %d" % (i // step + 1), "tasarim": None, "kind": "bolum",
                     "title": None, "govde": text[i:i + step], "madde_id": None}
                    for i in range(0, len(text), step)]
    ham_terimler = [t for t in re.split(r"\s+", q) if len(t) > 1]
    terimler = [t for t in ham_terimler if " ".join(tr_fold(t).split()) not in _DURAK] or ham_terimler
    kq = " ".join(tr_fold(q).split())
    puanli = []
    for sira, p in enumerate(parcalar):
        baslik_k = tr_fold(p["title"] or "")
        govde_k = tr_fold(p["govde"])
        tf = [tr_fold(t) for t in terimler]
        bas_terim = sum(1 for t in tf if t in baslik_k)
        eslesen = sum(1 for t in tf if t in baslik_k or t in govde_k)
        if not eslesen:
            continue
        isabet = sum(govde_k.count(t) + baslik_k.count(t) for t in tf)
        ifade = len(tf) > 1 and kq in " ".join((baslik_k + " " + govde_k).split())
        anahtar = (0 if bas_terim else 1, -eslesen, 0 if ifade else 1, -bas_terim,
                   1 if p["kind"] == "ek_bolum" else 0, -isabet, sira)
        puanli.append((anahtar, isabet, bas_terim, eslesen, p))
    puanli.sort(key=lambda x: x[0])
    limit = max(1, min(int(args.get("limit") or 8), 30))
    igne = next((t for t in terimler if count_hits(text, t)), terimler[0] if terimler else q)
    sonuc = []
    for _, isabet, bas_terim, eslesen, p in puanli[:limit]:
        etiket = p["etiket"] + (" - " + p["title"] if p["title"] else "")
        sonuc.append({"heading": etiket, "madde_no": p["tasarim"], "kind": p["kind"], "title": p["title"],
                      "madde_id": p["madde_id"], "hits": isabet, "heading_match": bool(bas_terim),
                      "terms_matched": eslesen, "excerpt": excerpt(p["govde"], igne, 300),
                      "chars": len(p["govde"])})
    out: Dict[str, Any] = {
        "mevzuat_id": mid, "query": q, "articles_scanned": madde_sayisi, "matches": len(puanli),
        "results": sonuc, "fetched_at": c.get("fetched_at"), "cache_age_s": yas,
        "note": ("Bu liste bir BULMA aracıdır, atıf kaynağı değildir: atıf yapacağınız maddeyi "
                 "tr_mevzuat_madde_getir(mevzuat_id=\"%s\", madde_no=[…]) ile getirip citation alanını "
                 "birebir kullanın. Kavramsal (anahtar kelime paylaşmayan) hüküm için semantik arama; "
                 "burada yalnız kelime eşleşmesi vardır. kind='ek_bolum' sonuçlar kanunun maddesi değildir "
                 "(işlenemeyen hükümler, tarife, tablo, dipnot)." % mid)}
    rec = _kayit_onbellekte(mid)
    if rec:
        out["citation_base"] = _atif_tabani(rec)[0]
    return out


# --------------------------------------------------------------------------- #
# Şemalar                                                                      #
# --------------------------------------------------------------------------- #
SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Mevzuat adı (varsayılan) veya tam metin ifadesi."},
        "number": {"type": "string", "description": "Mevzuat numarası, örn. 6446. Aynı numarayı birden çok "
                                                    "kayıt taşıyabilir (KANUN, MULGA, CB kararı): kanun için "
                                                    "types=['KANUN'] verin."},
        "types": {"type": "array", "items": {"type": "string", "enum": list(TYPES)},
                  "description": "Tür filtresi. Boş = tümü."},
        "search_in": {"type": "string", "enum": ["title", "fulltext"], "default": "title"},
        "exact_phrase": {"type": "boolean", "default": False},
        "rg_date_from": {"type": "string", "description": "Resmî Gazete tarihi ≥, YYYY-MM-DD. RG tarihi "
                                                          "boş kayıtlar tarih süzgecine girmez."},
        "rg_date_to": {"type": "string", "description": "Resmî Gazete tarihi ≤, YYYY-MM-DD"},
        "rg_number": {"type": "string", "description": "Resmî Gazete sayısı"},
        "page": {"type": "integer", "minimum": 1, "default": 1},
        "page_size": {"type": "integer", "minimum": 1, "maximum": PAGE_MAX, "default": 10,
                      "description": "Bedesten'in üst sınırı 20'dir; fazlası 400 döndürür."},
        "konu": {"type": "string", "enum": ["enerji", "rekabet", "vergi", "icra"],
                 "description": "Yerel, ağsız konu ÖN ELEMESİ: başlıkta konu adı geçmese de yakalar; torba "
                                "kanunlarda değiştirilen kanun adlarına da bakar. Sorgu kelimesi gerekmez: "
                                "types ve/veya rg_date_from/rg_date_to ile listeleyin. SINIR: eğitimde "
                                "görülmemiş 60 mevzuat başlığında vergi 4/4, icra 2/2, enerji 0/3 yakalandı — "
                                "enerji taramasında query ile birlikte kullanın. Elenen ve belirsiz sayısı "
                                "yanıtta yazar; esik=0 süzgeci kapatır."},
        "esik": {"type": "number", "description": "Konu eşiği (varsayılan 0.20, ölçülmüştür). 0 = eleme yok, "
                                                   "skorlar yine döner."},
        "max_pages": {"type": "integer", "minimum": 1, "maximum": 5, "default": 1,
                      "description": "Yalnız konu ile: art arda taranacak sayfa sayısı (sayfa başı en çok "
                                     "20 kayıt, istek başına ~3,5 sn)."},
    },
}

GET_SCHEMA = {
    "type": "object",
    "properties": {
        "mevzuat_id": {"type": "string"},
        "page": {"type": "integer", "minimum": 1, "default": 1},
        "page_chars": {"type": "integer", "minimum": 500, "maximum": 20000, "default": 8000},
    },
    "required": ["mevzuat_id"],
}

MADDE_SCHEMA = {
    "type": "object",
    "properties": {
        "number": {"type": "string", "description": "Kanun numarası, ör. \"6769\" (SMK). mevzuat_id yerine."},
        "mevzuat_id": {"type": "string", "description": "tr_mevzuat_ara sonucundaki mevzuat_id (number yerine)."},
        "madde_no": {"type": ["string", "array"], "items": {"type": "string"}, "maxItems": MADDE_AZAMI,
                     "description": "Tek madde ya da en çok %d maddelik liste: %s." % (MADDE_AZAMI, _SOZ_ORNEK)},
        "madde": {"type": ["string", "array"], "items": {"type": "string"},
                  "description": "madde_no ile aynı (eş ad)."},
        "types": {"type": "array", "items": {"type": "string", "enum": list(TYPES)},
                  "description": "number ile varsayılan [\"KANUN\"]; MULGA ancak listelenirse. KHK/CBK için "
                                 "[\"KHK\"], [\"CB_KARARNAME\"] …"},
        "tertip": {"type": "integer", "description": "Aynı numaralı kayıtları ayırmak için (ör. 5)."},
        "max_chars_per_article": {"type": "integer", "minimum": 500, "maximum": MADDE_KARAKTER_AZAMI,
                                  "default": MADDE_KARAKTER},
        "madde_id": {"type": "string", "description": "ESKİ kullanım: tr_mevzuat_icindekiler'deki düğüm; "
                                                      "tek başına düğümün ham metnini verir. madde_no ile "
                                                      "birlikte o düğümün içinde arar."},
    },
}

TOC_SCHEMA = {
    "type": "object",
    "properties": {
        "mevzuat_id": {"type": "string"},
        "number": {"type": "string", "description": "mevzuat_id yerine kanun numarası (ör. \"6769\")."},
        "types": {"type": "array", "items": {"type": "string", "enum": list(TYPES)}},
        "tertip": {"type": "integer"},
        "compact": {"type": "boolean", "default": False,
                    "description": "Satır başına 'madde_no | başlık | madde_id'; yapı düğümleri düşer. "
                                   "SMK ~26 KB'tan ~7 KB'a iner."},
        "madde_from": {"type": "integer"}, "madde_to": {"type": "integer"},
        "heading_query": {"type": "string", "description": "Başlıkta (Türkçe katlanmış) alt dizge, ör. 'önalım'."},
        "include_structure": {"type": "boolean", "default": False},
        "limit": {"type": "integer", "minimum": 1, "maximum": 2000}, "offset": {"type": "integer", "minimum": 0},
    },
}

WITHIN_SCHEMA = {
    "type": "object",
    "properties": {
        "mevzuat_id": {"type": "string"},
        "number": {"type": "string", "description": "mevzuat_id yerine kanun numarası (varsayılan tür KANUN)."},
        "query": {"type": "string"},
        "limit": {"type": "integer", "default": 8, "maximum": 30},
    },
    "required": ["query"],
}

GEREKCE_SCHEMA = {
    "type": "object",
    "properties": {"gerekce_id": {"type": "string"},
                   "page": {"type": "integer", "minimum": 1, "default": 1},
                   "page_chars": {"type": "integer", "minimum": 500, "maximum": 20000, "default": 8000}},
    "required": ["gerekce_id"],
}

SOURCE = Source(
    key="mevzuat",
    label="Mevzuat — kanun, KHK, CBK, yönetmelik, tebliğ (Bedesten)",
    kind="mevzuat",
    notes=(
        "12 mevzuat türü tek uçta. Bir maddeye atıf yapmadan önce tr_mevzuat_madde_getir(number=\"6769\", "
        "madde_no=\"120\") ile metni, başlığı ve hazır citation'ı TEK çağrıda alın (≤10 madde); madde "
        "numarası, başlığı ve RG künyesi kayıttan gelir, ezberden yazılmaz. Varsayılan arama BAŞLIKTA; "
        "hüküm metni için search_in='fulltext' veya tr_mevzuat_icinde_ara. Ek/geçici maddeler madde "
        "ağacında ayrı düğüm değildir. Sektörel ikincil düzenleme (EPDK/SPK/BDDK tebliğ ve "
        "yönetmelikleri) de buradadır: types=['KKY','TEBLIGLER'] ve query='Enerji Piyasası' gibi. Sorgu "
        "kelimesi olmadan da listeler (types ve/veya RG tarih aralığı; RG tarihi boş kayıtlar tarih "
        "süzgecine girmez); konu=… bu listeyi yerel olarak eler ve torba kanunlarda değiştirilen kanun "
        "adlarına bakar. Sayfa başı en çok 20 kayıt."
    ),
    search=search, get=get, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage="https://mevzuat.adalet.gov.tr",
)
