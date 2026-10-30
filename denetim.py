"""Denetim logu: modelin "Madde kontrolü: … okundu" beyanıyla karşılaştırılacak bağımsız kayıt.

Yalnız ayrı test uygulamasında, ``AUDIT_LOG=1`` ile açılır (server.py). ``AUDIT_LOG`` 1 değilken
bu modül yüklenmez bile: araçlar sarılmaz, oturum kimliği verilmez, bildirim kancası kurulmaz;
yanıtlar ve başlıklar bugünküyle bayt bayt aynıdır. Canlı uygulamada (``canli_uygulamada()``)
``AUDIT_LOG=1`` yok sayılır. Kayıt açıkken ``/directory/mcp`` sunulmaz: oradaki araçlar
sarılmaz, çağrıları kayda girmezdi.

Neden var
---------
Hukuk asistanının sistem talimatı modele maddeyi çekmesini (``tr_mevzuat_madde_getir``,
``tr_mevzuat_getir``) ve ardından "Madde kontrolü: … okundu" yazmasını söyler. O satır modelin
kendisi hakkındaki tanıklığıdır. Bu log aynı soruyu sunucu tarafında cevaplar: hangi madde
gerçekten çekildi, hangi çağrı hata verdi, hangisi iptal edildi. Claude.ai bir araç çağrısını
100 sn'de keser ve sonucu modele hiç göstermez; öyle bir çağrı burada
``sure_siniri_asildi: true`` ve (istemci bildirirse) ayrı bir ``iptal`` kaydıyla görünür.

Biçim ve akış
-------------
Her kayıt tek satırlık bir JSON nesnesidir (JSON Lines, ``ensure_ascii=False``) ve HER ZAMAN
standart hataya (stderr) tek bir ``write`` çağrısıyla yazılıp flush edilir. fly logs stdout ile
stderr'i birlikte toplar; stdio taşımasında stdout JSON-RPC kanalıdır ve öyle temiz kalır.
stderr'de sunucunun başka satırları da vardır: kayıt satırı ``{"olay":`` ile başlar, tüketici bu
öneke göre süzer.

İstemcinin denetlediği metinler (``session_id``, metin olan ``request_id``, ``reason``) kayda en
çok ``ISTEMCI_AZAMI`` (200) karakter olarak yazılır: sınırsız bir başlık log şişirmesin.

``olay: "cagri"`` — her araç çağrısı, handler bittikten SONRA (istisna fırlatsa da):

* ``ts`` çağrının BAŞLADIĞI an (UTC, milisaniye, ``Z``); bitiş = ts + ``duration_ms``.
  ``session_id`` / ``request_id``: ``mcpcore.istek_baglami()``.
* ``params``: argümanların olduğu gibi derin kopyası; maskeleme yok (aşağıda "Kararlar ve
  kısıtlar"). Argümanlar handler'dan ÖNCE kopyalanır.
* ``resolved``: ``tr_mevzuat_madde_getir`` için ``items[]``'tan
  ``{"kanun", "madde", "ok", "mulga", "durum"}``. ``kanun`` sonucun ``law.number`` alanı
  (Bedesten onu int verir; burada metne çevrilir), yoksa ``args.number``. ``madde`` kalemin
  ``madde_no``'su (ör. "120", "geçici 1", "169/a"), hata kaleminde ``requested``. ``durum``
  arka ucun ham ``items[].status`` değeridir ("text", "repealed", "annulled",
  "incorporated_elsewhere"; yoksa null): AYM iptali ve başka kanuna işlenmiş hüküm, mülgayla
  karıştırılmadan görünür. Eski ``madde_id`` kullanımında items yoktur: maddeler
  ``articles_in_node`` (yoksa ``madde_no``) listesinden, kanun numarası ``citation``'daki
  "No. N"den (yoksa ``args.number``) alınır; ``mulga`` ve ``durum`` null. ``tr_mevzuat_getir``
  için ``[{"kanun", "mevzuat_id"}]``; kanun yine ``citation``'dan ya da ``args.number``'dan.
  Diğer araçlarda ve handler istisna fırlattığında null.
* ``pages``: yalnız ``tr_mevzuat_getir``: ``{"sayfa": page, "toplam": total_pages,
  "maddeler": "ilk-son"}``. Aralık, dönen sayfa metnindeki ilk ve son madde başlık SATIRIDIR
  ("MADDE 12-", "Madde 12 –", "EK MADDE 3 –", "GEÇİCİ MADDE 1 –" → "12", "ek 3", "geçici 1").
  Tiresiz "MADDE 428 İşyerinin…" başlıkları arka ucun kurallarıyla sayılır. Desen ve katlama
  arka ucunkilerin kopyasıdır (``_BASLIK_SATIRI``, ``_BASLIK_SATIRI_GEVSEK``, ``_BASLIK_TIRESIZ``,
  ``textx._FOLD_1``); kopyanın kaymadığını bir test denetler. Başlık yoksa null; sonuç sayfa
  değil hata ise ``pages`` null. Sınır: kanun gövdesinin sonundaki "İŞLENEMEYEN HÜKÜMLER"
  bölümü BAŞKA kanunların "MADDE 1 –" başlıklarını taşır; o bölüme düşen sayfa onları da sayar.
* ``mulga``: yalnız arka ucun yapısal alanından: ``items[].status == "repealed"`` (arka ucun
  ``_durum`` sonucu). Çözülen maddelerden biri öyleyse true, ``status``'lu maddelerin hiçbiri
  değilse false, hiçbir maddede ``status`` yoksa null. ``"annulled"`` ve
  ``"incorporated_elsewhere"`` mülga sayılmaz (``durum``'da görünürler). Metinden tahmin
  yapılmaz; eski madde_id yolunda ``status`` olmadığı için null. Yalnız
  ``tr_mevzuat_madde_getir``'de anlamlıdır; diğer araçlarda null.
* ``status``: ok | partial | error | rate_limited | upstream_blocked | unavailable.
  İstisna → error; mesajı HTTP 429'u ya da hız sınırını gösteriyorsa ("HTTP 429",
  "Too Many Requests", "rate limit"/"RATE_LIMITED", "hız sınırı") rate_limited. Sözlük sonuçta
  sırayla: ``upstream_blocked`` doğruysa → upstream_blocked, ``unavailable`` → unavailable,
  ``error_code`` RATE_LIMITED (büyük/küçük harf fark etmez) ya da ``error`` metni aynı
  desenlere uyuyorsa → rate_limited, üst düzeyde ``"error"`` anahtarı → error,
  ``complete is False`` ya da bir ``items[].ok`` yanlışsa → partial, aksi hâlde ok. Bedesten
  hız sınırı çoğu araçta istisna olarak değil sözlük olarak döner: mevzuat ``_hata`` →
  ``{"error": "Bedesten hız sınırı …", "error_code": "RATE_LIMITED", …}``, içtihat
  ``_http_error`` → yalnız ``{"error": "Bedesten hız sınırı …", "retry": true}``.
  İptal çağrı anında bilinemez; ayrı ``iptal`` kaydına bakın.
* ``duration_ms`` (time.monotonic), ``sure_siniri_asildi`` (≥ 100000 ms), ``backend_version``
  (aracın önekinin arka ucu; bulunamazsa ağ geçidi sürümü).
* ``returned``: bütün araçlarda ``{"sha256", "ilk"}``: modele giden metnin
  (``mcpcore._as_text(value)``) UTF-8 baytlarının sha256'sı ve ilk 300 karakteri. İstisnada null.
* ``kimlikler``: sonuçtaki listelerde yer alan kayıtların kimlikleri, en çok ``KIMLIK_AZAMI``
  kayıt: her kayıttan yalnız ``KIMLIK_ANAHTARLARI``'ndan bulunanlar (``document_id``,
  ``karar_id``, ``ref``, ``esas_no``, ``karar_no``, ``citation`` …). Arama sonucundan yapılan
  atıf (karar listede vardı ama ayrıca getirilmedi) böyle görünür. Liste yoksa ya da istisnada null.

Kararlar ve kısıtlar
--------------------
* **Maskeleme yok.** Kayıt yalnız test uygulamasında ve sentetik sorularla (atıf sınav kümesi)
  açılır. Maskelenen her alan ölçülemeyen bir davranıştır: arama metni gizliyken "aradım, bulamadım"
  beyanı denetlenemez, modelin neyi aradığı ve arama sonucundan atıf yapıp yapmadığı görünmez.
* **Kısıt: kimlik doğrulaması yok.** Sunucu kimlik doğrulamasız; test uygulamasının adresine
  gerçek dosya bilgisiyle bağlanan olursa o bilgi de kayda girer. Uygulama yalnız sentetik
  sorularla kullanılır, adresi paylaşılmaz.
* **Dönen metnin tamamı değil özeti.** sha256 + ilk 300 karakter modelin hangi metni gördüğünü
  kesin tanımlar; aynı parametrelerle metin yeniden getirilebilir. Tamamı yazılmaz: bir kanun
  sayfası on binlerce karakter tutar ve log satırını şişirir. Bedeli: kaynak sonradan değişirse
  yeniden getirilen metin özetle tutmaz; o zaman fark özetten görünür ama eski metin geri gelmez.

``olay: "iptal"`` — istemcinin ``notifications/cancelled`` bildirimi:
``{"olay", "ts", "session_id", "request_id": params.requestId, "reason": params.reason}``.
Çağrı kaydıyla ``session_id`` + ``request_id`` üzerinden eşleşir. Python'da çalışan bir iş
parçacığı durdurulamaz; iptal edilen çağrının kendi kaydı da bittiğinde (genellikle sonra) yazılır.

Loglama çağrıyı asla bozmaz: içindeki her istisna yakalanır, stderr'e en çok bir kısa ASCII
satır yazılır; aracın dönüş değeri ve fırlattığı istisna AYNI nesne olarak geçer.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import mcpcore

MADDE_ARACI = "tr_mevzuat_madde_getir"
GETIR_ARACI = "tr_mevzuat_getir"

# Claude.ai bu sürede çağrıyı keser; sonuç modele hiç ulaşmaz.
SURE_SINIRI_MS = 100000

ILK_KARAKTER = 300

# Sonuç listelerindeki kayıtlardan alınan kimlik alanları (sıra kayıttaki sırayı belirler).
# Bedesten: document_id/esas_no/karar_no; Rekabet: karar_id; AYM: id; yerel indeks: ref;
# AB/NL/AT: celex/ecli/eli; hemen hepsinde hazır künye: citation. Başlık ve metin alınmaz.
KIMLIK_ANAHTARLARI = ("document_id", "documentId", "karar_id", "mevzuat_id", "id", "ref",
                      "ecli", "celex", "eli", "esas_no", "karar_no", "citation")
KIMLIK_AZAMI = 100
KIMLIK_DERINLIK = 4
# İstemcinin denetlediği metinlerin kayıttaki en büyük uzunluğu.
ISTEMCI_AZAMI = 200
# Canlı uygulamanın fly.toml adı: PRIVACY.md orada istek logu yok der, AUDIT_LOG=1 yok sayılır.
CANLI_UYGULAMA = "arthurlegal-mcp"

_AYAR: Dict[str, Any] = {"surumler": {}, "varsayilan": None}
_YAZMA_KILIDI = threading.Lock()


# --------------------------------------------------------------------------- #
# Açma                                                                         #
# --------------------------------------------------------------------------- #
def canli_uygulamada() -> bool:
    """Süreç canlı uygulamada mı: Fly her makinede ``FLY_APP_NAME``'i uygulamanın adıyla kurar.

    server.py öyleyse ``AUDIT_LOG=1``'i yok sayar: tek satır uyarı yazar, kayıtsız çalışır.
    Değişken yoksa (Fly dışı, yerel) ya da başka bir uygulamaysa kayıt açılabilir.
    """
    return os.environ.get("FLY_APP_NAME") == CANLI_UYGULAMA


def etkinlestir(tools: Iterable[Any], surumler: Optional[Dict[str, str]] = None,
                varsayilan_surum: Optional[str] = None) -> List[Any]:
    """Denetim logunu açar; araçların handler'ı sarılmış kopyalarını döndürür.

    server.py bunu YALNIZ ``AUDIT_LOG == "1"`` iken ve canlı uygulamada değilken
    (``canli_uygulamada()``), ``build()`` sonrası ``run()`` öncesi bir kez çağırır. Üç şey
    yapar: ``mcpcore.SESSION_IDS`` açılır (initialize yanıtı
    ``Mcp-Session-Id`` taşır; iki istemcinin 1'den başlayan JSON-RPC kimlikleri böylece
    karışmaz), bildirim kancası kurulur (``notifications/cancelled`` ancak böyle görünür) ve
    her aracın ``copy.copy`` ile alınmış kopyasında yalnız ``handler`` değişir: ad, açıklama ve
    şema AYNI nesnelerdir, model bir fark göremez; özgün Tool nesneleri olduğu gibi kalır.

    ``surumler`` önek → arka uç sürümü (``arka_uc_surumleri``); öneki eşleşmeyen araç
    (``status``, sürüm bildirmeyen arka uç) ``varsayilan_surum``'u (ağ geçidi) alır.
    """
    global _AYAR
    _AYAR = {"surumler": dict(surumler or {}), "varsayilan": varsayilan_surum}
    mcpcore.SESSION_IDS = True
    mcpcore.BILDIRIM_KANCASI = _bildirim
    sarili = []
    for t in tools:
        kopya = copy.copy(t)
        kopya.handler = _sarmala(t.name, t.handler, _surum(t.name))
        sarili.append(kopya)
    return sarili


def arka_uc_surumleri(yuklenenler: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    """server.py'nin ``_loaded`` listesinden önek → sürüm.

    Sürüm modülün kendi beyanıdır: ``__version__`` (v1.6 sunucuları, ``srv_tr`` 0.5.1 gibi),
    yoksa ``SERVER_VERSION`` (eski sözlük biçimli sunucular), yoksa paketin ``__version__``'ı.
    Modülü olmayan (de-eli vekili) ya da sürüm bildirmeyen önek listeye girmez.
    """
    out: Dict[str, str] = {}
    for kayit in yuklenenler or []:
        try:
            ad = str(kayit.get("module") or "")
            mod = sys.modules.get(ad) if ad else None
            surum = getattr(mod, "__version__", None) or getattr(mod, "SERVER_VERSION", None)
            if not surum and "." in ad:
                surum = getattr(sys.modules.get(ad.split(".")[0]), "__version__", None)
            if surum and kayit.get("prefix"):
                out[str(kayit["prefix"])] = str(surum)
        except Exception:  # noqa: BLE001 - sürüm bulunamazsa ağ geçidi sürümü kullanılır
            continue
    return out


def _surum(arac: str) -> Optional[str]:
    surumler = _AYAR["surumler"]
    for onek in sorted(surumler, key=len, reverse=True):
        if arac.startswith(onek + "_"):
            return surumler[onek]
    return _AYAR["varsayilan"]


# --------------------------------------------------------------------------- #
# Sarma                                                                        #
# --------------------------------------------------------------------------- #
def _sarmala(arac: str, handler: Callable[[Dict[str, Any]], Any],
             surum: Optional[str]) -> Callable[[Dict[str, Any]], Any]:
    """Handler'ı saran fonksiyon. Değer ve istisna AYNI nesne olarak geçer."""

    @functools.wraps(handler)
    def denetimli(args: Dict[str, Any]) -> Any:
        bas = time.monotonic()
        on = _guvenli(arac, _on_bilgi, arac, args)
        try:
            deger = handler(args)
        except Exception as exc:
            if on is not None:
                _guvenli(arac, _cagri_yaz, on, bas, surum, None, exc)
            raise
        if on is not None:
            _guvenli(arac, _cagri_yaz, on, bas, surum, deger, None)
        return deger

    return denetimli


def _kisalt(deger: Any) -> Any:
    """İstemcinin denetlediği metni kayıtta sınırla; metin olmayan değer olduğu gibi kalır."""
    return deger[:ISTEMCI_AZAMI] if isinstance(deger, str) else deger


def _on_bilgi(arac: str, args: Any) -> Dict[str, Any]:
    """Handler'dan ÖNCE: bağlam ve argümanların derin kopyası (handler değiştirse de)."""
    baglam = mcpcore.istek_baglami()
    return {"ts": _simdi(), "session_id": _kisalt(baglam["session_id"]),
            "request_id": _kisalt(baglam["request_id"]), "tool": arac,
            "params": _parametreler(args),
            "args": dict(args) if isinstance(args, dict) else {}}


def _cagri_yaz(on: Dict[str, Any], bas: float, surum: Optional[str],
               deger: Any, hata: Optional[BaseException]) -> None:
    sure_ms = int(round((time.monotonic() - bas) * 1000))
    arac = on["tool"]
    if hata is None:
        resolved = _cozulenler(arac, on["args"], deger)
        pages = _sayfalar(on["args"], deger) if arac == GETIR_ARACI else None
        mulga = _mulga_ozeti(resolved) if arac == MADDE_ARACI else None
        status = _durum_sinifla(deger)
        returned = _donen(deger)
        kimlikler = _kimlikler(deger)
    else:
        resolved = pages = mulga = returned = kimlikler = None
        status = _hata_durumu(hata)
    _yaz({
        "olay": "cagri", "ts": on["ts"], "session_id": on["session_id"],
        "request_id": on["request_id"], "tool": arac, "params": on["params"],
        "resolved": resolved, "pages": pages, "mulga": mulga, "status": status,
        "duration_ms": sure_ms, "sure_siniri_asildi": sure_ms >= SURE_SINIRI_MS,
        "backend_version": surum, "returned": returned, "kimlikler": kimlikler,
    })


def _bildirim(msg: Dict[str, Any]) -> None:
    """mcpcore bildirim kancası: yalnız ``notifications/cancelled`` kaydedilir."""
    try:
        if not isinstance(msg, dict) or msg.get("method") != "notifications/cancelled":
            return
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        baglam = mcpcore.istek_baglami()
        _yaz({"olay": "iptal", "ts": _simdi(), "session_id": _kisalt(baglam["session_id"]),
              "request_id": _kisalt(params.get("requestId")),
              "reason": _kisalt(params.get("reason"))})
    except Exception as exc:  # noqa: BLE001 - log hatası oturumu asla bozmaz
        _uyar("iptal: %s" % type(exc).__name__)


# --------------------------------------------------------------------------- #
# Yazma                                                                        #
# --------------------------------------------------------------------------- #
# json.dumps(ensure_ascii=False) bunları kaçışsız bırakır; str.splitlines() ise onlarda da
# satır böler. Kaçışlı hâlleri aynı JSON'dur ve kayıt her okuyucuda tek satır kalır.
_SATIR_AYIRICILAR = (("\u0085", "\\u0085"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029"))


def _yaz(kayit: Dict[str, Any]) -> None:
    satir = json.dumps(kayit, ensure_ascii=False, default=str, separators=(",", ":"))
    for ham, kacis in _SATIR_AYIRICILAR:
        satir = satir.replace(ham, kacis)
    with _YAZMA_KILIDI:                      # tek write: iş parçacıklarının satırları karışmaz
        sys.stderr.write(satir + "\n")
        sys.stderr.flush()


def _guvenli(arac: str, fn: Callable[..., Any], *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - log hatası çağrıyı asla bozmaz
        _uyar("%s: %s" % (arac, type(exc).__name__))
        return None


def _uyar(neden: str) -> None:
    """Tek kısa satır, yalnız ASCII: yazamadığımız akış kodlamaysa uyarı yine çıksın."""
    try:
        sys.stderr.write("denetim: kayit yazilamadi (%s)\n" % neden)
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass


def _simdi() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# Parametreler                                                                 #
# --------------------------------------------------------------------------- #
def _parametreler(args: Any) -> Any:
    """Argümanların derin kopyası: handler iç içe bir değeri değiştirse de kayıt öncekini tutar."""
    return copy.deepcopy(args)


# --------------------------------------------------------------------------- #
# Durum                                                                        #
# --------------------------------------------------------------------------- #
_HIZ_SINIRI = re.compile(
    r"rate[\s_-]?limit|too\s+many\s+requests|\bhttp(?:\s+error)?\s*429\b|\b429\s+too\b|"
    r"h[ıi]z\s+s[ıi]n[ıi]r", re.IGNORECASE)


def _hata_durumu(exc: BaseException) -> str:
    """İstisna → error; mesaj HTTP 429/hız sınırı diyorsa rate_limited.

    Çıplak "429" aranmaz: "429 numaralı KANUN bulunamadı" bir hız sınırı değildir.
    """
    return "rate_limited" if _HIZ_SINIRI.search(str(exc)) else "error"


def _durum_sinifla(deger: Any) -> str:
    if not isinstance(deger, dict):
        return "ok"
    if deger.get("upstream_blocked"):
        return "upstream_blocked"
    if deger.get("unavailable"):
        return "unavailable"
    if str(deger.get("error_code") or "").upper() == "RATE_LIMITED" or (
            "error" in deger and _HIZ_SINIRI.search(str(deger.get("error") or ""))):
        return "rate_limited"
    if "error" in deger:
        return "error"
    kalemler = deger.get("items")
    if deger.get("complete") is False or (
            isinstance(kalemler, list)
            and any(isinstance(k, dict) and "ok" in k and not k["ok"] for k in kalemler)):
        return "partial"
    return "ok"


# --------------------------------------------------------------------------- #
# Çözülen maddeler                                                             #
# --------------------------------------------------------------------------- #
_ATIF_NO = re.compile(r"\bNo\.\s*(\d{1,6})\b")


def _yazi(v: Any) -> Optional[str]:
    """Numara/kimlik metin olarak: Bedesten ``mevzuatNo``'yu int (6769) verir, şema "6769" ister."""
    if v is None or isinstance(v, bool):
        return None
    s = str(v).strip()
    return s or None


def _atiftan_numara(atif: Any) -> Optional[str]:
    """Künyedeki "(Kanun No. 6769, RG …)" → "6769" (biçim: bedesten_mevzuat._atif_tabani)."""
    if isinstance(atif, str):
        m = _ATIF_NO.search(atif)
        if m:
            return m.group(1)
    return None


def _cozulenler(arac: str, args: Dict[str, Any], deger: Any) -> Optional[List[Dict[str, Any]]]:
    if arac == MADDE_ARACI:
        return _madde_cozulenleri(args, deger)
    if arac == GETIR_ARACI:
        return _getir_cozulenleri(args, deger)
    return None


def _ham_durum(kalem: Dict[str, Any]) -> Optional[str]:
    durum = kalem.get("status")
    return durum if isinstance(durum, str) and durum else None


def _madde_cozulenleri(args: Dict[str, Any], deger: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(deger, dict):
        return None
    kalemler = deger.get("items")
    if isinstance(kalemler, list):           # article() ve _dugum_icinde()
        law = deger.get("law") if isinstance(deger.get("law"), dict) else {}
        kanun = _yazi(law.get("number")) or _yazi(args.get("number"))
        out = []
        for k in kalemler:
            if not isinstance(k, dict):
                continue
            durum = _ham_durum(k)
            out.append({"kanun": kanun, "madde": k.get("madde_no") or k.get("requested"),
                        "ok": bool(k.get("ok")),
                        "mulga": (durum == "repealed") if durum is not None else None,
                        "durum": durum})
        return out
    # Eski madde_id kullanımı (_eski_madde): items ve status yok, bu yüzden mulga/durum null;
    # künye ancak ağaç önbellekteyse gelir.
    kanun = _atiftan_numara(deger.get("citation")) or _yazi(args.get("number"))
    if "error" in deger:
        return [{"kanun": kanun, "madde": None, "ok": False, "mulga": None, "durum": None}]
    metin = deger.get("text")
    ok = isinstance(metin, str) and bool(metin.strip())
    dugumdekiler = deger.get("articles_in_node")
    tasarimlar = [str(d) for d in dugumdekiler if d] if isinstance(dugumdekiler, list) else []
    if not tasarimlar and deger.get("madde_no"):
        tasarimlar = [str(deger["madde_no"])]
    return [{"kanun": kanun, "madde": t, "ok": ok, "mulga": None, "durum": None}
            for t in tasarimlar or [None]]


def _getir_cozulenleri(args: Dict[str, Any], deger: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(deger, dict):
        return None
    kanun = _atiftan_numara(deger.get("citation")) or _yazi(args.get("number"))
    return [{"kanun": kanun, "mevzuat_id": _yazi(deger.get("mevzuat_id") or args.get("mevzuat_id"))}]


def _mulga_ozeti(cozulenler: Optional[List[Dict[str, Any]]]) -> Optional[bool]:
    degerler = [c.get("mulga") for c in cozulenler or []]
    if any(v is True for v in degerler):
        return True
    if any(v is False for v in degerler):
        return False
    return None


def _sayfalar(args: Dict[str, Any], deger: Any) -> Optional[Dict[str, Any]]:
    """tr_mevzuat_getir: textx.paginate çıktısı (page, total_pages, text). Hata sözlüğünde null."""
    if not isinstance(deger, dict) or not ("text" in deger or "page" in deger):
        return None
    return {"sayfa": deger.get("page", args.get("page")), "toplam": deger.get("total_pages"),
            "maddeler": _madde_araligi(deger.get("text"))}


# --------------------------------------------------------------------------- #
# Madde başlıkları (pages)                                                     #
# --------------------------------------------------------------------------- #
# Arka ucun desenlerinin KOPYASI (çalışma anında tr arka ucunu içe aktarmak, onun yüklenip
# yüklenmediğine ve çıplak `sources` paket adına bağımlılık olurdu). Kaymayı test denetler.
# Uzunluğu koruyan katlama = textx._FOLD_1: konumlar özgün metinde de geçerli.
_KATLA = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZÇçĞğİıÖöŞşÜüÂâÎîÛû",
                       "abcdefghijklmnopqrstuvwxyzccggiioossuuaaiiuu")
_TIRE = "\\-\u2013\u2014\u2212\u2010\u2011"
# = bedesten_mevzuat._BASLIK_SATIRI / _BASLIK_SATIRI_GEVSEK: satır başında
# [Ek|Geçici|Mükerrer] Madde N[/harf][ ila M] + tire (gevşekte "." ya da ":" da); tireden
# önce yumuşak tire (U+00AD) de olabilir.
_BASLIK_DESENI = (r"^[ \t]*(?:(ek|gecici|mukerrer)\s+)?madde\s+(\d{1,4})"
                  r"(?:[ \t]*/[ \t]*([a-z])(?![a-z0-9]))?"
                  r"(?:\s+ila\s+(?:madde\s+)?(\d{1,4}))?"
                  r"[ \t\u00ad]*[%s]")
_BASLIK = re.compile(_BASLIK_DESENI % _TIRE, re.M)
_BASLIK_GEVSEK = re.compile(_BASLIK_DESENI % (_TIRE + ".:"), re.M)
# = bedesten_mevzuat._BASLIK_TIRESIZ: tiresiz "MADDE 428 İşyerinin…" (TBK m.428). Özgün metinde
# aranır; kabul koşulları _tiresizler'de, arka ucun _tiresizleri_ekle'siyle aynı.
_BASLIK_TIRESIZ = re.compile(r"^[ \t]*MADDE[ \t]+(\d{1,4})(?=[ \t]+[A-ZÇĞİÖŞÜÂÎÛ(])", re.M)
_TUR_ETIKETI = {"ek": "ek ", "gecici": "geçici ", "mukerrer": "mükerrer "}   # = _KIND_TR


def _tasarim(metin: str, m: "re.Match[str]") -> str:
    """Arka ucun _tasarim biçimi: "12", "ek 3", "geçici 1", "169/a", "166 ila 184"."""
    s = _TUR_ETIKETI.get(m.group(1) or "", "") + str(int(m.group(2)))
    if m.group(3):
        harf = metin[m.start(3)]
        s += "/" + {"I": "ı", "İ": "i"}.get(harf, harf.lower())
    if m.group(4):
        s += " ila %d" % int(m.group(4))
    return s


def _tiresizler(metin: str, eslesmeler: List["re.Match[str]"]) -> List[Tuple[int, str]]:
    """= bedesten_mevzuat._tiresizleri_ekle: tiresiz başlıklar, yalnız güvenli durumda.

    Aynı numara düz başlık değil; numara önceki ve sonraki madde başlığının numaraları arasında;
    komşu başlık yoksa aday metnin ilk satırı. Satır başına düşmüş "Madde 12 uyarınca…" atfı
    küçük harfli ya da sırasız olduğundan sayılmaz.
    """
    maddeler = [m for m in eslesmeler if not m.group(1)]
    duz = {int(m.group(2)) for m in maddeler if not m.group(3)}
    ekler: List[Tuple[int, str]] = []
    for t in _BASLIK_TIRESIZ.finditer(metin):
        no = int(t.group(1))
        if no in duz:
            continue
        once = [m for m in maddeler if m.start() < t.start()]
        sonra = [m for m in maddeler if m.start() > t.start()]
        if not once and not sonra and metin[:t.start()].strip():
            continue
        if once and int(once[-1].group(4) or once[-1].group(2)) >= no:
            continue
        if sonra and int(sonra[0].group(2)) <= no:
            continue
        duz.add(no)
        ekler.append((t.start(), str(no)))
    return ekler


def _basliklar(metin: Any) -> List[str]:
    """Metindeki madde başlık satırları, sırayla (katı desen; hiç yoksa gevşek; tiresizler)."""
    if not isinstance(metin, str) or not metin:
        return []
    katli = metin.translate(_KATLA)
    eslesmeler = list(_BASLIK.finditer(katli)) or list(_BASLIK_GEVSEK.finditer(katli))
    basliklar = [(m.start(), _tasarim(metin, m)) for m in eslesmeler]
    basliklar += _tiresizler(metin, eslesmeler)
    return [s for _, s in sorted(basliklar)]


def _madde_araligi(metin: Any) -> Optional[str]:
    basliklar = _basliklar(metin)
    return "%s-%s" % (basliklar[0], basliklar[-1]) if basliklar else None


# --------------------------------------------------------------------------- #
# Dönen metin                                                                  #
# --------------------------------------------------------------------------- #
def _donen(deger: Any) -> Dict[str, Any]:
    """Modele giden metnin kendisi mcpcore._as_text(value): özet onun üzerinden alınır."""
    metin = mcpcore._as_text(deger)
    return {"sha256": hashlib.sha256(metin.encode("utf-8", "surrogatepass")).hexdigest(),
            "ilk": metin[:ILK_KARAKTER]}


# --------------------------------------------------------------------------- #
# Sonuç kimlikleri                                                             #
# --------------------------------------------------------------------------- #
def _kimlikler(deger: Any) -> Optional[List[Dict[str, Any]]]:
    """Sonuçtaki listelerde, kimlik alanı taşıyan sözlüklerin yalnız kimlik alanları.

    Kimlik taşıyan kayda inilmez (kaydın içindeki listeler o kaydın parçasıdır). Liste dışındaki
    tek sözlük (getirilen belgenin kendisi) sayılmaz: onun kimliği ``params``/``resolved``'da.
    """
    bulunan: List[Dict[str, Any]] = []

    def gez(d: Any, derinlik: int, listede: bool) -> None:
        if len(bulunan) >= KIMLIK_AZAMI or derinlik > KIMLIK_DERINLIK:
            return
        if isinstance(d, dict):
            kimlik = {k: d[k] for k in KIMLIK_ANAHTARLARI if d.get(k) not in (None, "")}
            if listede and kimlik:
                bulunan.append(kimlik)
                return
            for v in d.values():
                gez(v, derinlik + 1, False)
        elif isinstance(d, list):
            for v in d:
                gez(v, derinlik + 1, True)

    gez(deger, 0, False)
    return bulunan or None
