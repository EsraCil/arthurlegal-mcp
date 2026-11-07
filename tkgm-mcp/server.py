#!/usr/bin/env python3
"""tkgm-mcp — tapu-kadastro parsel araçları. Kimlik doğrulama yok, yalnız stdlib.

    python server.py                                 # stdio (tam özellik: dosya okur/yazar)
    python server.py --transport http --port 8060    # paylaşılan kip: dosya erişimi kapalı
    python server.py --ui [--kapi 8765]              # tarayıcı arayüzü (ui/baslat.cmd ve masaüstü kısayolu bunu çağırır)

Parsel iki yoldan gelir: sohbette sorulan il/ilçe/mahalle + ada/parsel ya da konum TKGM Parsel
Sorgu'dan canlı getirilir (tkgm_canli: tek sıra, dakikada en çok 30 istek, önbellek, devre
kesici, toplu tarama engeli; docs/MANIFESTO.md), ya da kullanıcı GeoJSON/KML dosyasını verir.
Sonrası yereldir: ölçü, ölçekli kroki, harita, biçim dönüşümü, rapor ve parselden hukuka köprü.

Token disiplini: görsel ve dışa aktarım dosyaya yazılır, modele yol + birkaç
satır özet döner. Yanıtlar sıkıştırılmış JSON'dur. Parseller içerik özetinden
türeyen bir `ref` ile anılır; geometri her çağrıda yeniden gönderilmez.
"""
from __future__ import annotations

import datetime
import json
import math
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import tkgm_bina as bina
import tkgm_canli as canli
import tkgm_geo as geo
import tkgm_harc as harc
import tkgm_harita as harita
import tkgm_hukuk as hukuk
import tkgm_kaynak as kaynak
import tkgm_koridor as koridor
import tkgm_parsel as parseller
import tkgm_rapor as rapor
import tkgm_tapu as tapu
import tkgm_ui as arayuz
from mcpcore import McpError, Tool, run
from tkgm_aktar import BICIMLER, aktar
from tkgm_analiz import olc, tr_bicim
from tkgm_kroki import ciz

__version__ = "0.6.1"
canli.SURUM = __version__

PARSEL_SORGU = "https://parselsorgu.tkgm.gov.tr/"
BAGLANTILAR = {
    "parsel_sorgu": PARSEL_SORGU,
    "web_tapu": "https://webtapu.tkgm.gov.tr/",
    "e_devlet_tapu_bilgileri": "https://www.turkiye.gov.tr/tapu-bilgileri-sorgulama",
    "tkgm_randevu": "https://randevu.tkgm.gov.tr/",
}

INSTRUCTIONS = """Tapu-kadastro parsel araçları: TKGM Parsel Sorgu'dan canlı parsel, ölçü, ölçekli kroki, harita,
rapor, hukuk köprüsü.

CANLI SORGU. Kullanıcı yer, adres, koordinat ya da il/ilçe/mahalle + ada/parsel söylerse dosya İSTEMEYİN:
- `parsel_sorgula(il, ilce, mahalle, ada, parsel)` ya da `parsel_sorgula(metin="Kadıköy Caferağa 123 ada 45 parsel")`.
  İl söylenmemişse ilçeden çıkarıp siz doldurun (Kadıköy → İstanbul). Köy parsellerinde ada 0'dır.
- `konumdan_parsel(enlem, boylam)`: noktadaki parsel. `yer_bul(sorgu)`: yer adı/adres → koordinat adayları.
- `parsel_raporu(ref)`: kimlik, ölçü, konum bağlantıları, hukuki işaretler tek raporda.
ONAY. Bir sohbetteki ilk canlı çağrı onay kartı döndürür: kartı kullanıcıya olduğu gibi gösterin; kabul ederse aynı
çağrıyı onay=true ile yineleyin ve o sohbetteki sonraki canlı çağrılarda da onay=true gönderin (kart bir daha
gösterilmez). Kabul etmezse canlı sorgu yapmayın.
HIZ. İstekler sıraya alınır (dakikada en çok 30, bütün kullanıcılar toplamı). "sırası dolu" ya da "bekliyor"
yanıtında belirtilen süreden önce yinelemeyin. Aynı adada parsel numaralarını tek tek taramayın: toplu tarama
durdurulur. Birden çok eşleşme `belirsiz` + `adaylar` ile döner: kullanıcıya sorun, `mahalle_id` ile yineleyin.

AKIŞ. `parsel_sorgula` / `konumdan_parsel` / `parsel_oku` → `ref` → `geometri` / `kroki` / `harita` /
`disa_aktar` / `dayanak_koprusu` / `parsel_raporu` o `ref` ile. Kullanıcı GeoJSON/KML getirirse `parsel_oku`.
Komşu parselleri de getirip `kroki`ye `komsular` olarak verin. Geometri UYDURMAYIN.

VERİNİN DEĞERİ. Parsel Sorgu verisi bilgi amaçlıdır, resmî işlemde kullanılamaz; malik,
şerh, beyan, rehin İÇERMEZ. Bunlar için kullanıcı kendi e-Devlet/Web Tapu oturumundan
belge alır. Kroki, aplikasyon krokisi veya röperli kroki yerine geçmez. Hesap alanı ile
dosyadaki alan farkı bir İŞARETTİR, hata tespiti değildir.

HUKUK. `dayanak_koprusu` hüküm metni vermez, adres verir: dayanak madde numaraları, teyit
günündeki değişiklik uyarıları ve hazır `tr_mevzuat_madde_getir` / `tr_ictihat_ara` çağrıları.
Metni o araçlarla çekmeden alıntılamayın; süreleri teyitsiz yazmayın.

""" + ("""PAYLAŞILAN UÇ. Dosya yazılmaz: kroki/harita/dışa aktarım içeriği yanıtta döner. tapu_kaydi_oku,
parsel_foyu, portfoy_tablosu ve arazi_3d burada YOKTUR; tapu kaydı (malik adı, TCKN) bu uca
gönderilmez. Bellek kalıcı değildir: ref kaybolursa parsel_oku'nun `ref_kodu`nu verin."""
       if kaynak.paylasilan_mi() else
       """3D. `arazi_3d` araziyi ve çevre binaların TAHMİNİ kütlesini çizer (açık bina tabanı + kat × 3 m).
Yanıttaki `uyari_3d` metnini kullanıcıya her seferinde AYNEN iletin: görsel gerçek veriye dayanmaz,
yapay zekâ/otomatik kurallarla üretilmiş, fikir verme amaçlı bir tahmindir. Kat sayısını siz tahmin
etmeyin; kullanıcı söylerse `kat`a yazın. Uydu ya da Google görüntüsünden 3D üretilmez.

AÇMA. Yanıtta `arayuzde_ac` varsa kullanıcıya dosya yolu yerine o bağlantıyı verin: harita ve 3D,
ArthurLegal Tapu arayüzünde açılır. Dosyadan (çift tıklayarak) açılan haritada OpenStreetMap
karoları engellenebilir ("Access blocked"); uydu görünümü de yalnız arayüzde gelir.

TOKEN. Görseller dosyaya yazılır, yol döner. `inline=true` yalnız kullanıcı SVG/HTML
metnini açıkça istediğinde.
Yerel arayüzün Sor sekmesinden gelen mesaj "ArthurLegal'de sor:" ile başlar: cevabı sohbette ver ve
`arayuze_yaz` ile arayüze de teslim et (yalnız yerel kipte).""")

REHBER = """TKGM araçları — ne yapar, ne yapmaz
1) CANLI: parsel_sorgula(il, ilce, mahalle, ada, parsel | metin=…) ya da konumdan_parsel(enlem, boylam) parseli
   TKGM Parsel Sorgu'nun herkese açık verisinden getirir → ref. yer_bul(sorgu) yer adı/adresten koordinat
   adayları verir (OpenStreetMap). İlk canlı çağrı bir onay kartı döndürür; kullanıcı kabul edince onay=true.
2) SINIR: TKGM'ye tek sıra, dakikada en çok 30 istek (bütün kullanıcılar); parsel 24 saat önbellekte; TKGM
   yavaşlarsa bağlayıcı da yavaşlar, 429/503'te durur. Çağrı başına tek parsel; toplu tarama durdurulur.
   Tasarım: """ + canli.MANIFESTO + """
3) DOSYA: kullanıcı GeoJSON/KML getirirse parsel_oku(dosya=… | icerik=…) aynı ref'i üretir.
4) ÖLÇ/ÇİZ/RAPOR: geometri(ref) kenar-semt-açı; kroki(ref, komsular=[…]) A4 ölçekli SVG; harita(ref);
   parsel_raporu(ref) tek rapor; disa_aktar(ref, bicim=geojson|kml|dxf|csv); koordinat_donustur.
   arazi_3d(ref, kat?) yalnız yerel kipte: kot/eğim + çevre binaların TAHMİNİ kütlesi; `uyari_3d`
   kullanıcıya aynen iletilir. Yanıtta `arayuzde_ac` varsa dosya yolu yerine o bağlantı verilir.
5) HUKUK: dayanak_koprusu(konu=… | ref=…) → dayanak adresleri + hazır tr_ araç çağrıları.
6) KAPSAM DIŞI: malik, şerh, beyan, rehin Parsel Sorgu'da yoktur; kullanıcı kendi e-Devlet / Web Tapu
   kaydından alır (tapu_kaydi_oku yerel kipte maskeleyerek yapılandırır). Güzergâh boyunca toplu döküm yapılmaz."""


def _kisa(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"), default=str)


# Paylaşılan uçta CPU'yu gövde sınırı bağlamaz: 404 baytlık bir istek aynı ref'i 16 kez
# `komsular`a koyup dakikalarca kroki çizdirebiliyordu. Tekrarlar atılır, sayı ve toplam
# köşe sınırlanır. Yerel kipte yalnız tekrar atılır (300 parsellik güzergâh meşrudur).
_PAYLASILAN_AZAMI_PARSEL = 50
_PAYLASILAN_AZAMI_REF = 64
_PAYLASILAN_AZAMI_KOSE = 200_000
_PAYLASILAN_AZAMI_HAT_NOKTASI = 10_000


def _refler(liste: Any, alan: str, haric: str = "") -> List[str]:
    if liste is None:
        return []
    if not isinstance(liste, list):
        raise McpError("`%s` bir ref listesi olmalı." % alan)
    tekil = [r for r in dict.fromkeys(str(x) for x in liste) if r != haric]
    if kaynak.paylasilan_mi() and len(tekil) > _PAYLASILAN_AZAMI_REF:
        raise McpError("Paylaşılan uçta `%s` en çok %d ref alır." % (alan, _PAYLASILAN_AZAMI_REF))
    return tekil


def _kose_siniri(secilen: List[Dict[str, Any]]) -> None:
    if kaynak.paylasilan_mi():
        toplam = sum(parseller.kose_sayisi(p) for p in secilen)
        if toplam > _PAYLASILAN_AZAMI_KOSE:
            raise McpError("Paylaşılan uçta bir çağrıdaki toplam köşe en çok %d (bu çağrıda %d)."
                           % (_PAYLASILAN_AZAMI_KOSE, toplam))


def _parsel(args: Dict[str, Any], anahtar: str = "ref") -> Dict[str, Any]:
    ref = str(args.get(anahtar) or "").strip()
    if not ref:
        raise McpError("`%s` gerekli: önce parsel_oku çağırın." % anahtar)
    if parseller.kod_mu(ref):
        # Taşınabilir ref: parsel kodun içinde; bellek kaybolmuş olsa da kurulur.
        try:
            p = parseller.koddan(ref)
        except parseller.ParselHatasi as exc:
            raise McpError(str(exc)) from exc
        parseller.sakla(p)
        return p
    p = parseller.getir(ref)
    if p is None:
        if kaynak.paylasilan_mi():
            raise McpError("ref '%s' bu sunucu sürecinin belleğinde yok: paylaşılan uçta bellek kalıcı "
                           "değildir (yeniden başlatma ya da başka makine). parsel_oku'nun döndürdüğü "
                           "`ref_kodu`nu ref yerine verin ya da aynı içeriği parsel_oku ile yeniden okutun "
                           "(aynı içerik aynı ref'i verir)." % ref)
        raise McpError("ref '%s' bellekte yok (sunucu yeniden başlamış olabilir). Dosyayı "
                       "parsel_oku ile yeniden okutun." % ref)
    return p


# parsel_oku yanıtındaki ref_kodu'ların toplam boyutu (karakter). Kod parselin kendisidir;
# büyük dosyada hepsini yanıta koymak, modelin bağlamını geometriyle doldurmak olur.
_REF_KODU_BUTCE = 12_000

# Türkiye'yi kapsayan kutu; tkgm_baglanti ile aynı sınır. Bunun dışındaki nokta ya başka ülkedir
# ya da enlem/boylam (Y/X) sırası ters verilmiştir — ikisinde de TUREF dilimi anlamsızdır.
_TR_ENLEM = (35.0, 43.0)
_TR_BOYLAM = (25.0, 45.5)
# TM 3° ve UTM 6° dilim yarı genişlikleri (derece).
_YARI_GENISLIK = {"tm3": 1.5, "utm6": 3.0}
# Türkiye için makul düzlem koordinatları: sağa değer 500.000 ± ~4°, yukarı değer enlem 35-43.
_Y_ARALIK = (100_000.0, 900_000.0)
_X_ARALIK = (3_800_000.0, 4_800_000.0)


def _turkiyede(enlem: float, boylam: float) -> bool:
    return _TR_ENLEM[0] <= enlem <= _TR_ENLEM[1] and _TR_BOYLAM[0] <= boylam <= _TR_BOYLAM[1]


def _sistem(args: Dict[str, Any]) -> str:
    sistem = str(args.get("sistem") or "tm3")
    if sistem not in geo.SISTEMLER:
        raise McpError("sistem: %s" % " | ".join(geo.SISTEMLER))
    return sistem


def _gecerli_domlar(sistem: str) -> tuple:
    # UTM 6° dilimlerinin Türkiye'deki orta meridyenleri 27, 33, 39, 45 (35-38. dilimler).
    return geo.DOM_3 if sistem == "tm3" else (27, 33, 39, 45)


def _dom(args: Dict[str, Any], sistem: str):
    dom = args.get("dom")
    if dom is None:
        return None
    try:
        dom_f = float(dom)
    except (TypeError, ValueError):
        raise McpError("`dom` sayı olmalı: %s" % ", ".join(map(str, _gecerli_domlar(sistem))))
    if dom_f not in _gecerli_domlar(sistem):
        raise McpError("`dom` %s için orta meridyen olmalı: %s (verilen: %s)."
                       % (geo.SISTEMLER[sistem]["ad"], ", ".join(map(str, _gecerli_domlar(sistem))), dom))
    return int(dom_f)


def _nokta_cifti(n: Any, i: int) -> tuple:
    if (not isinstance(n, (list, tuple)) or len(n) != 2
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in n)):
        raise McpError("`noktalar`[%d]: iki sayılık çift bekleniyor, gelen: %s" % (i, _kisa(n)[:60]))
    return float(n[0]), float(n[1])


def _cikti(parsel: Dict[str, Any], ek: str, metin: str, inline: bool, alan: str) -> Dict[str, Any]:
    """Dosyaya yaz ve yolu döndür; paylaşılan sunucuda ya da istenirse metni döndür.

    Arayüz aynı süreçte açıksa (`--ui-ile`) harita, 3D ve kroki için `arayuzde_ac` de döner:
    yalnız o dosyayı gösteren yerel bağlantı. Dosyadan açılan harita karo alamayabilir."""
    if inline or kaynak.uzak_mi():
        return {alan: metin}
    yol = kaynak.dosya_yaz(kaynak.dosya_adi(parsel, ek), metin)
    out: Dict[str, Any] = {"dosya": yol, "boyut_kb": round(len(metin.encode("utf-8")) / 1024.0, 1)}
    bag = arayuz.arayuz_baglantisi(yol)
    if bag:
        out["arayuzde_ac"] = bag
    return out


# --------------------------------------------------------------------------- #
def _t_rehber(args: Dict[str, Any]) -> Any:
    return REHBER


def _t_baglanti(args: Dict[str, Any]) -> Any:
    enlem, boylam = args.get("enlem"), args.get("boylam")
    if args.get("ref"):
        olcu = olc(_parsel(args))
        # Köşe ortalaması L/U biçimli parselde dışarı düşer ve bağlantı komşuyu açar;
        # sınıra en uzak İÇ nokta kullanılır.
        (ic_y, ic_x), _ = geo.etiket_noktasi(olcu["tm"][0])
        enlem, boylam = geo.tm_geri(ic_y, ic_x, olcu["dom"])
    out: Dict[str, Any] = {"diger": {k: v for k, v in BAGLANTILAR.items() if k != "parsel_sorgu"}}
    if enlem is not None and boylam is not None:
        enlem, boylam = float(enlem), float(boylam)
        if not (35.0 <= enlem <= 43.0 and 25.0 <= boylam <= 45.5):
            raise McpError("Koordinat Türkiye sınırları dışında; enlem/boylam sırasını kontrol edin.")
        out["parsel_sorgu"] = "%s#ara/cografi/%.6f/%.6f" % (PARSEL_SORGU, enlem, boylam)
    else:
        out["parsel_sorgu"] = PARSEL_SORGU
        yer = [str(args[k]) for k in ("il", "ilce", "mahalle") if args.get(k)]
        out["adimlar"] = [
            "Bağlantıyı açın, kullanım koşullarını onaylayın.",
            "İdari sorguda seçin: %s" % (" > ".join(yer) if yer else "İl > İlçe > Mahalle"),
            "Ada: %s, Parsel: %s girip sorgulayın." % (args.get("ada") or "…", args.get("parsel") or "…"),
            "Haritada parsele tıklayın; açılan bilgi kutusunun köşesindeki ⋮ (üç nokta) menüsünden "
            "GeoJSON (ya da KML) olarak indirin. Komşu parseller için aynısını tek tek yapın.",
            "İndirilen dosyanın yolunu (ya da metnini) parsel_oku aracına verin.",
        ]
    out["not"] = ("Bağlantıyı kullanıcı kendi tarayıcısında açar; bu araç TKGM'ye istek göndermez. Parseli sohbete "
                  "getirmek için parsel_sorgula ya da konumdan_parsel.")
    return _kisa(out)


# --------------------------------------------------------------------------- #
# Canlı sorgu (tkgm_canli)                                                     #
# --------------------------------------------------------------------------- #
def _onay(args: Dict[str, Any]) -> Optional[str]:
    """Sohbet başına tek onay: kart dönerse model kullanıcıya gösterir, kabulde onay=true ile yineler."""
    if not canli.onay_gerekli() or args.get("onay") is True or str(args.get("onay")).lower() in ("true", "evet"):
        return None
    return _kisa({"onay_gerekli": True, "kart": canli.onay_karti(),
                  "talimat": "Kartı kullanıcıya olduğu gibi gösterin ve onay isteyin. Kabul ederse aynı çağrıyı "
                             "onay=true ile yineleyin; bu sohbetteki sonraki canlı çağrılarda da onay=true gönderin, "
                             "kartı bir daha göstermeyin. Kabul etmezse canlı sorgu yapmayın (dosya varsa parsel_oku)."})


def _tr_saat(t: float) -> str:
    return time.strftime("%d.%m.%Y %H:%M", time.gmtime(t + 3 * 3600))


def _kaynak_metni(bilgi: Dict[str, Any]) -> str:
    if bilgi.get("kaynak") == "önbellek":
        yas = int(bilgi.get("yas_sn") or 0)
        return "TKGM Parsel Sorgu, önbellekten (%s önce getirildi)" % (
            "%d dk" % max(1, yas // 60) if yas < 7200 else "%d saat" % (yas // 3600))
    return "TKGM Parsel Sorgu, canlı (%s)" % _tr_saat(bilgi.get("zaman") or time.time())


def _canli_kaydet(ozellik: Dict[str, Any], bilgi: Dict[str, Any],
                  yer: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """TKGM'nin Feature'ını parsel belleğine koyar: bundan sonra parsel_oku ile okunmuş bir parselden farkı yoktur."""
    ozellik = json.loads(json.dumps(ozellik))            # önbellekteki nesne değişmez
    oz = ozellik.setdefault("properties", {})
    katli = {parseller._katla(k) for k in oz}
    for alan, anahtar in (("il", "ilAd"), ("ilce", "ilceAd"), ("mahalle", "mahalleAd")):
        if yer and yer.get(alan) and parseller._katla(anahtar) not in katli:
            oz[anahtar] = yer[alan]["ad"]
    try:
        p = parseller.oku(json.dumps(ozellik, ensure_ascii=False), ad="TKGM Parsel Sorgu")[0]
    except parseller.ParselHatasi as exc:
        raise McpError("TKGM yanıtı parsel olarak okunamadı: %s" % exc) from exc
    p["kaynak"].update({"canli": True, "metin": _kaynak_metni(bilgi)})
    parseller.sakla(p, paylasilan=kaynak.paylasilan_mi())
    return p


def _ic_nokta(p: Dict[str, Any], olcu: Dict[str, Any]) -> tuple:
    (ic_y, ic_x), _ = geo.etiket_noktasi(olcu["tm"][0])
    return geo.tm_geri(ic_y, ic_x, olcu["dom"])


def _baglantilar(enlem: float, boylam: float) -> Dict[str, str]:
    return {"parsel_sorgu": "%s#ara/cografi/%.6f/%.6f" % (PARSEL_SORGU, enlem, boylam),
            "openstreetmap": "https://www.openstreetmap.org/?mlat=%.6f&mlon=%.6f#map=18/%.6f/%.6f"
                             % (enlem, boylam, enlem, boylam)}


def _parsel_ozeti(p: Dict[str, Any]) -> Dict[str, Any]:
    olcu = olc(p)
    oz = p["oznitelik"]
    out: Dict[str, Any] = {"ref": p["ref"], "parsel": parseller.etiket(p)}
    for k in ("il", "ilce", "mahalle", "mevkii", "ada", "parsel", "nitelik", "pafta", "zemin_tipi", "durum",
              "gittigi_parseller", "gittigi_sebep"):
        if oz.get(k):
            out["parsel_no" if k == "parsel" else k] = oz[k]
    if oz.get("tapu_alani_m2") is not None:
        out["tapu_alani_m2"] = oz["tapu_alani_m2"]
    out["hesap_alani_m2"] = olcu["alan_m2"]
    for k in ("fark_yuzde", "fark_yorumu", "alan_belirsizligi_m2"):
        if k in olcu:
            out[k] = olcu[k]
    enlem, boylam = _ic_nokta(p, olcu)
    out["konum"] = {"enlem": round(enlem, 6), "boylam": round(boylam, 6)}
    out["baglantilar"] = _baglantilar(enlem, boylam)
    rejim = hukuk.nitelik_rejimleri(oz.get("nitelik", ""))
    if rejim:
        out["rejim"] = [r["uyari"] for r in rejim]
    out["kaynak"] = p["kaynak"].get("metin") or "dosya"
    if kaynak.paylasilan_mi():
        kod = parseller.kodla(p)
        if len(kod) <= _REF_KODU_BUTCE:
            out["ref_kodu"] = kod
    out["sonraki"] = "parsel_raporu / kroki / harita / geometri / dayanak_koprusu — hepsi bu ref ile"
    out["not"] = "Bilgi amaçlıdır; malik, şerh, beyan ve rehin içermez."
    return out


def _sorgula(args: Dict[str, Any]):
    """Canlı getirme. Dönüş: (parsel, None) ya da (None, kullanıcıya dönecek yanıt: onay kartı / belirsiz)."""
    kart = _onay(args)
    if kart:
        return None, kart
    son = time.monotonic() + canli.CAGRI_BUTCESI_SN
    ada, parsel_no = args.get("ada"), args.get("parsel")
    isimli = any(str(args.get(k) or "").strip() for k in ("il", "ilce", "mahalle"))
    yer = None
    try:
        if args.get("enlem") is not None and args.get("boylam") is not None:
            ozellik, bilgi = canli.konum_getir(args["enlem"], args["boylam"], son)
            return _canli_kaydet(ozellik, bilgi), None
        if args.get("mahalle_id") not in (None, ""):
            mahalle_id = args["mahalle_id"]
        elif str(args.get("metin") or "").strip() and not isimli:
            c = canli.metin_coz(args["metin"], son, ada, parsel_no)
            if "enlem" in c:
                ozellik, bilgi = canli.konum_getir(c["enlem"], c["boylam"], son)
                return _canli_kaydet(ozellik, bilgi), None
            yer = {k: c[k] for k in ("il", "ilce", "mahalle")}
            ada, parsel_no, mahalle_id = c["ada"], c["parsel"], c["mahalle"]["id"]
        else:
            if ada in (None, "") or parsel_no in (None, ""):
                raise canli.BilgiEksik("`ada` ve `parsel` gerekli (ya da hepsini `metin` içinde verin).")
            yer = canli.yer_coz(args.get("il"), args.get("ilce"), args.get("mahalle"), son)
            mahalle_id = yer["mahalle"]["id"]
        ozellik, bilgi = canli.parsel_getir(mahalle_id, ada, parsel_no, son)
    except canli.Belirsiz as exc:
        yanit = exc.yanit()
        if ada not in (None, "") and parsel_no not in (None, ""):
            yanit.setdefault("ada", ada)
            yanit.setdefault("parsel", parsel_no)
        return None, _kisa(yanit)
    except canli.CanliHata as exc:
        raise McpError(str(exc)) from exc
    return _canli_kaydet(ozellik, bilgi, yer), None


def _t_parsel_sorgula(args: Dict[str, Any]) -> Any:
    p, yanit = _sorgula(args)
    return yanit if p is None else _kisa(_parsel_ozeti(p))


def _t_konumdan_parsel(args: Dict[str, Any]) -> Any:
    if args.get("enlem") is None or args.get("boylam") is None:
        raise McpError("`enlem` ve `boylam` gerekli (Türkiye içi, derece).")
    p, yanit = _sorgula({"enlem": args["enlem"], "boylam": args["boylam"], "onay": args.get("onay")})
    return yanit if p is None else _kisa(_parsel_ozeti(p))


def _t_yer_bul(args: Dict[str, Any]) -> Any:
    kart = _onay(args)
    if kart:
        return kart
    try:
        adaylar = canli.yer_ara(args.get("sorgu"), time.monotonic() + canli.CAGRI_BUTCESI_SN)
    except canli.CanliHata as exc:
        raise McpError(str(exc)) from exc
    out: Dict[str, Any] = {"sorgu": str(args.get("sorgu") or "")[:200], "adaylar": adaylar,
                           "atif": "© OpenStreetMap katkıcıları (ODbL)"}
    if adaylar:
        out["sonraki"] = ("Doğru adayı seçip konumdan_parsel(enlem, boylam) ile o noktadaki parseli getirin. Mahalle ya "
                          "da ilçe gibi geniş bir yer döndüyse nokta rastgele bir parsele düşer: kullanıcıdan ada/parsel "
                          "ya da daha kesin bir adres isteyin.")
    else:
        out["not"] = "Eşleşme yok; il/ilçe/mahalle ile ya da ada/parsel ile deneyin."
    return _kisa(out)


def _kenar_ozeti(olcu: Dict[str, Any], sinir: int = 12) -> List[str]:
    kaba = "kenar_belirsizligi_m" in olcu
    satirlar = []
    for k in geo.kenarlar(olcu["tm"][0][0])[:sinir]:
        satirlar.append("%d-%d: %s m" % (k["bas"], k["son"], ("≈" + tr_bicim(k["boy"], 1)) if kaba
                                         else tr_bicim(k["boy"])))
    return satirlar


def _rapor_md(p: Dict[str, Any], konu: Optional[str]) -> str:
    oz, olcu = p["oznitelik"], olc(p)
    enlem, boylam = _ic_nokta(p, olcu)
    bag = _baglantilar(enlem, boylam)
    ya = lambda k: oz.get(k) or "—"   # noqa: E731
    satirlar = [
        "# Parsel raporu: %s" % parseller.etiket(p),
        "Kaynak: %s · ArthurLegal Tapu %s" % (p["kaynak"].get("metin") or "kullanıcının dosyası", __version__),
        "", "## Kimlik", "| Alan | Değer |", "|---|---|",
        "| İl / İlçe | %s / %s |" % (ya("il"), ya("ilce")),
        "| Mahalle / Mevkii | %s / %s |" % (ya("mahalle"), ya("mevkii")),
        "| Ada / Parsel | %s / %s |" % (ya("ada"), ya("parsel")),
        "| Nitelik | %s |" % ya("nitelik"),
        "| Pafta | %s |" % ya("pafta"),
        "| Zemin tipi / Durum | %s / %s |" % (ya("zemin_tipi"), ya("durum")),
    ]
    if oz.get("gittigi_parseller"):
        satirlar.append("| Gittiği parseller | %s (%s) |" % (oz["gittigi_parseller"], ya("gittigi_sebep")))
    satirlar += ["", "## Ölçü", "| Ölçü | Değer |", "|---|---|"]
    if oz.get("tapu_alani_m2") is not None:
        satirlar.append("| Kayıtlı alan | %s m² |" % tr_bicim(oz["tapu_alani_m2"]))
    satirlar.append("| Hesap alanı (%s) | %s m² |" % (olcu["projeksiyon"], tr_bicim(olcu["alan_m2"])))
    if "fark_yuzde" in olcu:
        satirlar.append("| Fark | %s m² (%%%s) |" % (tr_bicim(olcu["fark_m2"]), tr_bicim(olcu["fark_yuzde"], 3)))
    satirlar += ["| Çevre | %s m |" % tr_bicim(olcu["cevre_m"]),
                 "| Köşe sayısı | %d |" % olcu["kose_sayisi"],
                 "| İç nokta | %.6f K, %.6f D |" % (enlem, boylam)]
    if olcu.get("fark_yorumu"):
        satirlar += ["", "> " + olcu["fark_yorumu"]]
    kenar = _kenar_ozeti(olcu)
    if kenar:
        satirlar += ["", "Kenarlar (dış sınır, kroki numaralarıyla): " + " · ".join(kenar)]
    satirlar += ["", "## Konum", "- Parsel Sorgu: %s" % bag["parsel_sorgu"], "- OpenStreetMap: %s" % bag["openstreetmap"],
                 "", "## Hukuki işaretler"]
    rejim = hukuk.nitelik_rejimleri(oz.get("nitelik", ""))
    satirlar += ["- %s" % r["uyari"] for r in rejim] or ["- Nitelikten doğan özel rejim işareti yok."]
    if konu:
        k = hukuk.kopru(konu)
        satirlar += ["", "### %s" % k["baslik"]]
        satirlar += ["- %s %s: md. %s" % (d["mevzuat_no"], d["ad"], d["maddeler"]) for d in k["dayanak"]]
        for alan, ad in (("sure", "Süre"), ("uyari", "Uyarı"), ("parsel_verisi", "Parsel verisinin rolü")):
            if k.get(alan):
                satirlar.append("- %s: %s" % (ad, k[alan]))
        satirlar.append("- Teyit: %s. Maddeyi alıntılamadan önce tr_mevzuat_madde_getir ile çekin." % k["teyit"])
    else:
        satirlar.append("- Uyuşmazlık konusu seçilirse (dayanak_koprusu konu=…) dayanak maddeleri eklenir: %s."
                        % ", ".join(x["konu"] for x in hukuk.konu_listesi()[:8]))
    satirlar += ["", "## Sınırlar",
                 "Parsel Sorgu verisi bilgi amaçlıdır, resmî işlemde kullanılamaz. Malik, şerh, beyan ve rehin bu "
                 "raporda yoktur; kullanıcının kendi e-Devlet / Web Tapu kaydından alınır. Hesap alanı ile kayıtlı alan "
                 "farkı bir işarettir, hata tespiti değildir. Kroki aplikasyon krokisi yerine geçmez."]
    return "\n".join(satirlar)


def _t_parsel_raporu(args: Dict[str, Any]) -> Any:
    konu = args.get("konu")
    if konu and konu not in hukuk.KONULAR:
        raise McpError("Bilinmeyen konu. Geçerli: %s" % ", ".join(hukuk.KONULAR))
    if args.get("ref"):
        p = _parsel(args)
    else:
        p, yanit = _sorgula(args)
        if p is None:
            return yanit
    out: Dict[str, Any] = {"ref": p["ref"], "rapor_md": _rapor_md(p, konu)}
    if kaynak.paylasilan_mi() and not args.get("ref"):
        kod = parseller.kodla(p)
        if len(kod) <= _REF_KODU_BUTCE:
            out["ref_kodu"] = kod
    tarih = datetime.date.today().isoformat()
    if not kaynak.uzak_mi() and args.get("dosyalar", True) is not False:
        svg, _ = ciz(p, [], False, tarih)
        out["dosyalar"] = {
            "kroki_svg": kaynak.dosya_yaz(kaynak.dosya_adi(p, "kroki.svg"), svg),
            "harita_html": kaynak.dosya_yaz(kaynak.dosya_adi(p, "harita.html"), harita.harita_html(p, [])),
            "foy_docx": kaynak.dosya_yaz(kaynak.dosya_adi(p, "foy.docx"), rapor.foy_docx(p, [], konu, tarih))}
    elif args.get("kroki"):
        out["kroki_svg"] = ciz(p, [], False, tarih)[0]
    out["not"] = "rapor_md'yi kullanıcıya olduğu gibi gösterin; bağlantılar tıklanabilir."
    return _kisa(out)


def _t_parsel_oku(args: Dict[str, Any]) -> Any:
    dosya, icerik = args.get("dosya"), args.get("icerik")
    if bool(dosya) == bool(icerik):
        raise McpError("`dosya` (yerel yol) ya da `icerik` (GeoJSON/KML metni) — yalnız biri.")
    sistem = _sistem(args)
    dom = _dom(args, sistem)
    try:
        metin = kaynak.dosya_oku(dosya) if dosya else str(icerik)
        okunan = parseller.oku(metin, ad=str(dosya or "icerik"), dom=dom,
                               k0=geo.SISTEMLER[sistem]["k0"])
    except (kaynak.ErisimHatasi, parseller.ParselHatasi) as exc:
        raise McpError(str(exc)) from exc

    paylasilan = kaynak.paylasilan_mi()
    if paylasilan and len(okunan) > _PAYLASILAN_AZAMI_PARSEL:
        raise McpError("Paylaşılan uçta bir çağrıda en çok %d parsel okunur; dosyada %d var."
                       % (_PAYLASILAN_AZAMI_PARSEL, len(okunan)))
    kod_butcesi = _REF_KODU_BUTCE
    kodsuz = 0
    liste: List[Dict[str, Any]] = []
    for p in okunan:
        parseller.sakla(p, paylasilan=paylasilan)
        olcu = olc(p)
        kayit: Dict[str, Any] = {"ref": p["ref"], "parsel": parseller.etiket(p),
                                 "alan_m2": olcu["alan_m2"], "kose": olcu["kose_sayisi"]}
        oz = p["oznitelik"]
        for k in ("nitelik", "mevkii", "pafta", "zemin_tipi", "durum", "gittigi_parseller"):
            if oz.get(k):
                kayit[k] = oz[k]
        if "fark_yuzde" in olcu:
            kayit["dosyadaki_alan_m2"] = olcu["tapu_alani_m2"]
            kayit["fark_yuzde"] = olcu["fark_yuzde"]
        for k in ("alan_belirsizligi_m2", "fark_yorumu", "hassasiyet_notu"):
            if k in olcu:
                kayit[k] = olcu[k]
        rejim = hukuk.nitelik_rejimleri(oz.get("nitelik", ""))
        if rejim:
            kayit["rejim"] = [r["uyari"] for r in rejim]
        if paylasilan and len(liste) < 40:
            kod = parseller.kodla(p)
            if len(kod) <= kod_butcesi:
                kayit["ref_kodu"] = kod
                kod_butcesi -= len(kod)
            else:
                kodsuz += 1
        liste.append(kayit)
    out: Dict[str, Any] = {"okunan": len(liste), "parseller": liste[:40]}
    if len(liste) > 40:
        out["not"] = "İlk 40 parsel listelendi; tamamı bellekte."
    if paylasilan:
        out["ref_notu"] = ("Paylaşılan uçta bellek kalıcı değildir. Bir araç 'bellekte yok' derse ref yerine "
                           "`ref_kodu`nu verin (komsular/refs listelerinde de geçer)" +
                           ("; %d parsel için ref_kodu yanıt boyutu yüzünden üretilmedi, onlar için aynı "
                            "içeriği yeniden parsel_oku'ya verin." % kodsuz if kodsuz else "."))
    return _kisa(out)


def _t_geometri(args: Dict[str, Any]) -> Any:
    p = _parsel(args)
    olcu = olc(p)
    tm = olcu.pop("tm")
    out: Dict[str, Any] = {"ref": p["ref"], "parsel": parseller.etiket(p)}
    out.update(olcu)
    if args.get("ayrinti", True):
        satirlar: List[str] = []
        acilar: List[str] = []
        kayma = 0
        for c in tm:
            for k in geo.kenarlar(c[0]):
                # Kaba dosyada (5 ondalık) santimetre yazmak sahte kesinliktir; kroki ile aynı kural.
                boy = "≈" + tr_bicim(k["boy"], 1) if "kenar_belirsizligi_m" in olcu else tr_bicim(k["boy"])
                satirlar.append("%d-%d %s m %sg" % (k["bas"] + kayma, k["son"] + kayma, boy,
                                                    tr_bicim(k["semt_g"], 1 if "kenar_belirsizligi_m" in olcu else 2)))
            acilar += ["%d:%s" % (i + 1 + kayma, tr_bicim(a, 2))
                       for i, a in enumerate(geo.ic_acilar(c[0]))]
            kayma += len(c[0])
        out["kenarlar"] = satirlar[:60]
        out["ic_acilar_g"] = " ".join(acilar[:60])
        if len(satirlar) > 60:
            out["not"] = "İlk 60 kenar; tamamı için disa_aktar bicim=csv."
        out["aciklama"] = "kenar: bas-son boy semt(grad, grid kuzeyinden saat yönünde); açılar grad"
    return _kisa(out)


def _t_kroki(args: Dict[str, Any]) -> Any:
    p = _parsel(args)
    komsular = _komsular(args, p)
    svg, ozet = ciz(p, komsular, bool(args.get("koordinat_tablosu")),
                    datetime.date.today().isoformat())
    try:
        ozet.update(_cikti(p, "kroki.svg", svg, bool(args.get("inline")), "svg"))
    except kaynak.ErisimHatasi as exc:
        raise McpError(str(exc)) from exc
    return _kisa(ozet)


def _t_disa_aktar(args: Dict[str, Any]) -> Any:
    p = _parsel(args)
    bicim = str(args.get("bicim") or "geojson").lower()
    if bicim not in BICIMLER:
        raise McpError("bicim: %s" % " | ".join(BICIMLER))
    out: Dict[str, Any] = {"parsel": parseller.etiket(p), "bicim": bicim}
    # Dosyayı alan (bilirkişi, harita mühendisi) yanlış dilimde içe aktarırsa parsel başka yere düşer;
    # koordinat sistemi hem dosyada (CSV sütunu, DXF metni) hem burada yazılır.
    out["koordinat_sistemi"] = {
        "dxf": olc(p)["projeksiyon"],
        "csv": olc(p)["projeksiyon"] + " (Y_saga, X_yukari); enlem/boylam sütunları coğrafi (ITRF96≈WGS84)",
    }.get(bicim, "WGS84 coğrafi (EPSG:4326), [boylam, enlem]")
    try:
        out.update(_cikti(p, "parsel.%s" % bicim, aktar(p, bicim), bool(args.get("inline")), "icerik"))
    except kaynak.ErisimHatasi as exc:
        raise McpError(str(exc)) from exc
    return _kisa(out)


def _t_koordinat(args: Dict[str, Any]) -> Any:
    yon = args.get("yon")
    if yon not in ("cografi_tm", "tm_cografi"):
        raise McpError("yon: cografi_tm | tm_cografi")
    sistem = _sistem(args)
    k0 = geo.SISTEMLER[sistem]["k0"]
    yari = _YARI_GENISLIK[sistem]
    noktalar = args.get("noktalar") or []
    if not isinstance(noktalar, list) or not noktalar or len(noktalar) > 500:
        raise McpError("`noktalar`: 1-500 çift.")
    ciftler = [_nokta_cifti(n, i) for i, n in enumerate(noktalar)]
    dom = _dom(args, sistem)
    sonuc = []
    # Dilim dışı nokta matematiksel olarak dönüşür ama resmî kadastro değeri o noktanın KENDİ
    # diliminde üretilir; sessizce başka dilimde vermek karşılaştırılamaz bir sayı vermektir.
    dilim_disi: List[Dict[str, Any]] = []
    if yon == "cografi_tm":
        for i, (enlem, boylam) in enumerate(ciftler):
            if not _turkiyede(enlem, boylam):
                raise McpError("Nokta %d (%s, %s) Türkiye sınırları dışında; [enlem, boylam] sırasını kontrol "
                               "edin. Bu araç TUREF/ITRF96 dilimleri içindir." % (i + 1, enlem, boylam))
        if dom is None:
            dom = geo.dom_sec(sum(b for _, b in ciftler) / len(ciftler), sistem)
        for i, (enlem, boylam) in enumerate(ciftler):
            y, x = geo.tm_ileri(enlem, boylam, dom, k0)
            sonuc.append([round(y, 3), round(x, 3)])
            if abs(boylam - dom) > yari + 1e-9:
                dilim_disi.append({"nokta": i + 1, "boylam": boylam, "fark_derece": round(abs(boylam - dom), 3),
                                   "kendi_dom": geo.dom_sec(boylam, sistem)})
        sira = "girdi [enlem, boylam] → çıktı [Y sağa, X yukarı] m"
    else:
        if dom is None:
            raise McpError("tm_cografi için `dom` zorunlu: koordinat hangi dilimde üretildi?")
        for i, (y, x) in enumerate(ciftler):
            y_makul = _Y_ARALIK[0] <= y <= _Y_ARALIK[1]
            x_makul = _X_ARALIK[0] <= x <= _X_ARALIK[1]
            if not (y_makul and x_makul):
                if _Y_ARALIK[0] <= x <= _Y_ARALIK[1] and _X_ARALIK[0] <= y <= _X_ARALIK[1]:
                    raise McpError("Nokta %d: [Y, X] sırası ters görünüyor (%s, %s). Beklenen [Y sağa, X yukarı]: "
                                   "Y ~100.000-900.000, X ~3.800.000-4.800.000 m." % (i + 1, y, x))
                raise McpError("Nokta %d (%s, %s) Türkiye için makul bir TM/UTM koordinatı değil: Y (sağa) "
                               "~100.000-900.000, X (yukarı) ~3.800.000-4.800.000 m beklenir. Dilim numarası "
                               "önekli Y (ör. 33487175) ya da ED50/başka sistem olabilir; sırayı ve sistemi "
                               "kontrol edin." % (i + 1, y, x))
            enlem, boylam = geo.tm_geri(y, x, dom, k0)
            if not _turkiyede(enlem, boylam):
                raise McpError("Nokta %d DOM %s ile Türkiye dışına düşüyor (%.5f, %.5f): `dom` ya da Y/X sırası "
                               "yanlış." % (i + 1, dom, enlem, boylam))
            sonuc.append([round(enlem, 8), round(boylam, 8)])
            if abs(boylam - dom) > yari + 1e-9:
                dilim_disi.append({"nokta": i + 1, "boylam": round(boylam, 5),
                                   "fark_derece": round(abs(boylam - dom), 3),
                                   "kendi_dom": geo.dom_sec(boylam, sistem)})
        sira = "girdi [Y sağa, X yukarı] m → çıktı [enlem, boylam]"
    out: Dict[str, Any] = {"sistem": "%s DOM %s" % (geo.SISTEMLER[sistem]["ad"], dom), "sira": sira,
                           "noktalar": sonuc}
    if sistem == "tm3":
        out["epsg"] = geo.EPSG_TM3[dom]
    if dilim_disi:
        out["dilim_disi"] = dilim_disi[:50]
        out["uyari"] = ("%d nokta DOM %s diliminin (±%s°) dışında. Dönüşüm matematiksel olarak doğrudur ama resmî "
                        "kadastro koordinatı noktanın kendi diliminde (kendi_dom) üretilir; bilirkişi/kadastro "
                        "değeriyle karşılaştırmak için o noktaları `dom=kendi_dom` ile ayrıca dönüştürün."
                        % (len(dilim_disi), dom, str(yari).replace(".", ",")))
    out["not"] = ("ITRF96 ile WGS84 bu amaçla özdeş alınır; ED50 (eski paftalar) DEĞİLDİR, "
                  "ED50 koordinatı datum dönüşümü ister.")
    return _kisa(out)


def _t_hukuk(args: Dict[str, Any]) -> Any:
    konu = args.get("konu")
    out: Dict[str, Any] = {}
    nitelik = args.get("nitelik") or ""
    if args.get("ref"):
        p = _parsel(args)
        out["parsel"] = parseller.etiket(p)
        nitelik = nitelik or p["oznitelik"].get("nitelik", "")
    if nitelik:
        out["nitelik"] = nitelik
        out["rejim"] = hukuk.nitelik_rejimleri(nitelik)
    if konu:
        if konu not in hukuk.KONULAR:
            raise McpError("Bilinmeyen konu. Geçerli: %s" % ", ".join(hukuk.KONULAR))
        out["konu"] = hukuk.kopru(konu)
    else:
        out["konular"] = hukuk.konu_listesi()
    out["dogrulama"] = hukuk.DOGRULAMA
    return _kisa(out)


def _t_koridor(args: Dict[str, Any]) -> Any:
    refs = _refler(args.get("refs"), "refs")
    if not refs:
        raise McpError("`refs`: koridorla karşılaştırılacak parsellerin ref listesi.")
    secilen = [_parsel({"ref": r}) for r in refs]
    _kose_siniri(secilen)
    verilen = [k for k in ("hat", "hat_icerik", "hat_dosya") if args.get(k)]
    if len(verilen) != 1:
        raise McpError("Hat için `hat` ([[enlem, boylam]…]), `hat_icerik` ya da `hat_dosya` — yalnız biri.")
    try:
        if args.get("hat"):
            hatlar = [[(float(n[1]), float(n[0])) for n in args["hat"]]]
            if len(hatlar[0]) < 2:
                raise koridor.KoridorHatasi("`hat` en az iki nokta ister.")
        else:
            metin = (kaynak.dosya_oku(args["hat_dosya"]) if args.get("hat_dosya")
                     else str(args["hat_icerik"]))
            hatlar = koridor.hat_oku(metin)
        if kaynak.paylasilan_mi() and sum(len(h) for h in hatlar) > _PAYLASILAN_AZAMI_HAT_NOKTASI:
            raise koridor.KoridorHatasi("Paylaşılan uçta hat en çok %d nokta alır."
                                        % _PAYLASILAN_AZAMI_HAT_NOKTASI)
        sonuc = koridor.kesisim(secilen, hatlar, float(args.get("genislik_m") or 0))
    except (koridor.KoridorHatasi, kaynak.ErisimHatasi) as exc:
        raise McpError(str(exc)) from exc

    satirlar = sonuc.pop("satirlar")
    sonuc["satirlar"] = ["%s | %s m² kesişim (%%%s) | eksen %s m%s" % (
        s["parsel"], tr_bicim(s["kesisim_m2"]), tr_bicim(s["oran_yuzde"]), tr_bicim(s["eksen_m"]),
        "" if s["eksen_geciyor"] else " (yalnız tampon)") for s in satirlar[:40]]
    if len(satirlar) > 40:
        sonuc["not"] = "İlk 40 satır (kesişime göre azalan); tamamı CSV'de."
    sonuc["yontem"] = ("Yarı-analitik: tarama çizgisinde kapalı çözüm, dikey doğrultuda olay noktalarında "
                       "bölünmüş Gauss-Legendre. sayisal_hata_yuzde_en_kotu koridor alanının 4 ve 2 noktalı "
                       "kurallar arasındaki farkıdır. Her parsel kendi TM 3° diliminde.")
    if satirlar and not kaynak.uzak_mi():
        sonuc["dosya"] = kaynak.dosya_yaz("koridor_%dm_%dparsel.csv" % (round(sonuc["genislik_m"]),
                                                                       len(satirlar)),
                                          koridor.csv_yaz({"satirlar": satirlar}))
    return _kisa(sonuc)


def _komsular(args: Dict[str, Any], p: Dict[str, Any]) -> List[Dict[str, Any]]:
    kom = [_parsel({"ref": r}) for r in _refler(args.get("komsular"), "komsular", p["ref"])]
    _kose_siniri([p] + kom)
    return kom


def _t_harita(args: Dict[str, Any]) -> Any:
    p = _parsel(args)
    html = harita.harita_html(p, _komsular(args, p))
    out: Dict[str, Any] = {"parsel": parseller.etiket(p), "altlik": "OpenStreetMap (tarayıcı yükler)"}
    if not kaynak.uzak_mi():
        out["uydu"] = "Tapu arayüzünde, kullanıcının kendi Esri anahtarı girilmişse"
    try:
        out.update(_cikti(p, "harita.html", html, bool(args.get("inline")), "html"))
    except kaynak.ErisimHatasi as exc:
        raise McpError(str(exc)) from exc
    return _kisa(out)


def _t_arazi_3d(args: Dict[str, Any]) -> Any:
    # Yükseklik verisi sunucunun Copernicus'tan MB'larca indirmesini gerektirir. Kimlik
    # doğrulamasız paylaşılan uçta bu, isteyen herkesin sunucuya bant genişliği harcatması olur.
    if kaynak.uzak_mi():
        raise McpError("arazi_3d yalnız yerel (stdio) kipte çalışır: DEM indirmesi paylaşılan uçta açılmadı.")
    p = _parsel(args)
    kom = _komsular(args, p)
    try:
        # Girdi, ağa çıkmadan denetlenir: yanlış `kat` 2 MB indirdikten sonra değil, önce reddedilsin.
        kat, varsayilan = bina.girdi_denetle(args.get("kat"), args.get("varsayilan_kat"))
    except bina.BinaHatasi as exc:
        raise McpError(str(exc)) from exc
    try:
        kutu = harita.parsel_kutusu(p, kom)
        izgara = harita.dem_izgara(*kutu)
        ozet = harita.arazi_ozeti(p, izgara)
        uyarilar = [izgara["uyari"]] if isinstance(izgara.get("uyari"), str) else list(izgara.get("uyari") or [])
        kutle, bina_ozeti = None, None
        if args.get("binalar", True) is not False:
            try:
                veri = bina.binalar(*kutu)
                kutle = bina.kutle_modeli(p, veri, kat, varsayilan)
                k = kutle["ozet"]
                bina_ozeti = {"cizilen": k["bina_sayisi"], "parsel_binasi": k["parsel_binasi"], "kip": k["kip"],
                              "overture_surumu": veri["surum"], "notlar": k["notlar"], "atif": k["atif"],
                              "indirilen_kb": round(veri.get("indirilen_bayt", 0) / 1024.0)}
            except bina.BinaHatasi as exc:
                # Bina verisi gelmezse arazi yine çizilir; kullanıcı eksik katmanı bilir.
                uyarilar.append("Binalar çizilmedi: %s" % exc)
        html = harita.arazi_3d_html(p, kom, izgara, ozet, kutle)
        out: Dict[str, Any] = {"parsel": parseller.etiket(p), "uyari_3d": harita.UYARI_3D, "arazi": ozet,
                               "kaynak": izgara.get("kaynak"),
                               "indirilen_kb": round(izgara.get("indirilen_bayt", 0) / 1024.0)}
        if bina_ozeti:
            out["binalar"] = bina_ozeti
        if uyarilar:
            out["uyari"] = uyarilar
        out.update(_cikti(p, "arazi3d.html", html, bool(args.get("inline")), "html"))
    except harita.HaritaHatasi as exc:
        raise McpError(str(exc)) from exc
    except kaynak.ErisimHatasi as exc:
        raise McpError(str(exc)) from exc
    return _kisa(out)


def _t_foy(args: Dict[str, Any]) -> Any:
    if kaynak.uzak_mi():
        raise McpError("parsel_foyu dosya üretir; yalnız yerel (stdio) kipte çalışır.")
    p = _parsel(args)
    konu = args.get("konu")
    if konu and konu not in hukuk.KONULAR:
        raise McpError("Bilinmeyen konu. Geçerli: %s" % ", ".join(hukuk.KONULAR))
    veri = rapor.foy_docx(p, _komsular(args, p), konu, datetime.date.today().isoformat())
    yol = kaynak.dosya_yaz(kaynak.dosya_adi(p, "foy.docx"), veri)
    return _kisa({"parsel": parseller.etiket(p), "dosya": yol, "boyut_kb": round(len(veri) / 1024.0, 1)})


def _t_portfoy(args: Dict[str, Any]) -> Any:
    if kaynak.uzak_mi():
        raise McpError("portfoy_tablosu dosya üretir; yalnız yerel (stdio) kipte çalışır.")
    refs = _refler(args.get("refs"), "refs")
    if not refs:
        raise McpError("`refs`: tabloya girecek parsellerin ref listesi.")
    secilen = [_parsel({"ref": r}) for r in refs]
    veri = rapor.portfoy_xlsx(secilen, datetime.date.today().isoformat())
    yol = kaynak.dosya_yaz("portfoy_%dparsel_%s.xlsx" % (len(secilen), datetime.date.today().isoformat()), veri)
    return _kisa({"parsel_sayisi": len(secilen), "dosya": yol, "boyut_kb": round(len(veri) / 1024.0, 1)})


def _t_tarife_ara(args: Dict[str, Any]) -> Any:
    sorgu = str(args.get("sorgu") or "").strip()
    if len(sorgu) < 2:
        raise McpError("`sorgu`: işlem adı, örn. 'satış', 'ipotek', 'aplikasyon', 'kat irtifakı'.")
    return _kisa({"veri": harc.durum(), "sonuclar": harc.ara(sorgu),
                  "not": "kod'u harc_hesapla'ya verin. isaret: YK = yöresel katsayıyla çarpılır, M = maktu."})


def _sayi(deger: Any, ad: str) -> Optional[float]:
    """Sayı ya da Türkçe yazım ("3.000.000", "1.312,40"); NaN, sonsuz ve eksi reddedilir."""
    if deger is None or deger == "":
        return None
    if isinstance(deger, bool):
        raise McpError("`%s` sayı olmalı." % ad)
    x = parseller.tr_sayi(deger) if isinstance(deger, str) else deger
    try:
        x = float(x)
    except (TypeError, ValueError):
        raise McpError("`%s` sayı olmalı." % ad) from None
    if not math.isfinite(x) or x < 0 or x > 1e13:
        raise McpError("`%s` geçerli bir tutar değil." % ad)
    return x


def _t_harc(args: Dict[str, Any]) -> Any:
    th, ds = args.get("tapu_harci_islem"), args.get("doner_sermaye_kod")
    if not th and not ds:
        raise McpError("`tapu_harci_islem` ve/veya `doner_sermaye_kod` verin; kodlar için tarife_kalemi.")
    out: Dict[str, Any] = {"veri": harc.durum()}
    adet = _sayi(args.get("adet"), "adet")
    if adet is not None and (adet != int(adet) or not 1 <= adet <= 10_000):
        raise McpError("`adet` 1 ile 10.000 arasında tam sayı olmalı.")
    adet = int(adet or 1)
    try:
        if th:
            out["tapu_harci"] = harc.tapu_harci(str(th), _sayi(args.get("bedel"), "bedel") or 0.0,
                                                _sayi(args.get("emlak_vergi_degeri"), "emlak_vergi_degeri"),
                                                adet)
        if ds:
            out["doner_sermaye"] = harc.doner_sermaye(str(ds), _sayi(args.get("yoresel_katsayi"),
                                                                     "yoresel_katsayi"), adet)
    except harc.HarcHatasi as exc:
        raise McpError(str(exc)) from exc
    parcalar = [out[k].get("toplam_tl") for k in ("tapu_harci", "doner_sermaye") if k in out]
    if parcalar and all(p is not None for p in parcalar):
        out["genel_toplam_tl"] = round(sum(parcalar), 2)
    out["uyari"] = harc.ON_HESAP
    return _kisa(out)


def _t_tapu(args: Dict[str, Any]) -> Any:
    # TKGM_DOSYA_ERISIMI dosya erişimini açar; kimliksiz uca kişisel veri göndermeye izin vermez.
    # Paylaşılan uçta bu araç listelenmez (TOOLS süzgeci); buraya yalnız çalışma sırasında kip
    # değiştiyse gelinir. Mesaj olanı söyler: metin sunucuya ULAŞTI, işlenmeden reddedildi.
    if kaynak.paylasilan_mi():
        raise McpError("Tapu kaydı kişisel veri içerir; bu araç yalnız yerel (stdio) kipte çalışır. Kimlik "
                       "doğrulamasız paylaşılan uca malik bilgisi gönderilmez. Gönderilen metin sunucuya "
                       "ulaştı ama işlenmeden reddedildi — ayrıştırılmadı, saklanmadı, yanıtta geri dönmedi. "
                       "Tapu kaydını (malik adı, TCKN, rehin) bu uca tekrar göndermeyin; aracı yerel (stdio) "
                       "kurulumda kullanın.")
    metin = str(args.get("metin") or "")
    if len(metin.strip()) < 20:
        raise McpError("`metin`: kullanıcının kendi e-Devlet/Web Tapu oturumundan aldığı belgenin metni.")
    try:
        kayit = tapu.ayristir(metin,
                              maskele=args.get("tc_maskele", True) is not False,
                              ad_maskele=args.get("ad_maskele", True) is not False,
                              ucuncu_kisi=args.get("ucuncu_kisi_maskele", True) is not False)
    except tapu.TapuHatasi as exc:
        raise McpError(str(exc)) from exc
    if args.get("ref"):
        p = _parsel(args)
        # Çapraz kontrol maskelemeden SONRA çalışır: ada/parsel/yüzölçümü maskelenmeyen
        # alanlardır, dolayısıyla karşılaştırma maskeden etkilenmez.
        kayit["capraz_kontrol"] = tapu.capraz_kontrol(kayit, p, olc(p)["alan_m2"])
    return _kisa(kayit)


def _t_arayuze_yaz(args: Dict[str, Any]) -> Any:
    metin = str(args.get("metin") or "")
    if not metin.strip():
        raise McpError("`metin` gerekli: arayüzde gösterilecek cevap.")
    try:
        yol = kaynak.cevap_yaz(str(args.get("baslik") or "Cevap").strip() or "Cevap", metin, args.get("ref"))
    except kaynak.ErisimHatasi as exc:
        raise McpError(str(exc)) from exc
    return _kisa({"teslim": True, "dosya": yol,
                  "not": "Arayüzün Sor sekmesinde, Cevaplar altında görünür (düz metin)."})


def _t_status(args: Dict[str, Any]) -> Any:
    return {
        "server": "tkgm-mcp",
        "version": __version__,
        "upstream": "TKGM Parsel Sorgu (canlı, hız sınırlı: `canli`), yer adı için OpenStreetMap Nominatim, arazi_3d için "
                    "Copernicus DEM ve Overture bina tabanları (yalnız yerel kipte). Harita karolarını kullanıcının "
                    "tarayıcısı yükler.",
        "arayuz": None if kaynak.uzak_mi() else arayuz.durum(),
        "canli": canli.durum(sayilar=not kaynak.paylasilan_mi()),
        "kip": "paylaşılan (dosya erişimi kapalı)" if kaynak.uzak_mi() else "yerel (dosya okur/yazar)",
        "cikti_klasoru": None if kaynak.uzak_mi() else kaynak.cikti_klasoru(),
        "listelenmeyen_araclar": [t for t in YEREL_ARACLAR if t not in {a.name for a in TOOLS}],
        # Paylaşılan uçta sayı, başka kullanıcıların etkinliğine yan kanal olurdu.
        "bellekteki_parsel": None if kaynak.paylasilan_mi() else parseller.bellek_sayisi(),
        "bellek_notu": ("Süreç belleği: yeniden başlatmada ya da başka makinede ref kaybolur; ref_kodu "
                        "kalıcıdır." if kaynak.paylasilan_mi() else "Süreç belleği (en çok 256 parsel)."),
        "canli_kaynak": kaynak.canli_durum(),
        "hukuk_konulari": len(hukuk.KONULAR),
        "tarife": harc.durum(),
    }


# Araç listesi yükleme anında kurulur ve kip ona göre yazılır: paylaşılan (HTTP) uçta dosya
# yazılmaz, görsel yanıtta döner; "dosyaya yazar, yol döner" diyen açıklama orada yanlıştır.
# Birleşik uç (Dockerfile MCP_TRANSPORT=http) bu modülü içe aktarırken uzak_mi() zaten True'dur.
_UZAK = kaynak.uzak_mi()
_PAYLASILAN = kaynak.paylasilan_mi()

_REF = {"type": "string",
        "description": "parsel_oku'nun döndürdüğü ref" + (" ya da ref_kodu (bellek kaybolduğunda da çalışır)"
                                                          if _PAYLASILAN else "")}
_INLINE = {"type": "boolean", "default": False,
           "description": ("Bu uçta etkisiz: dosya yazılmaz, içerik her zaman yanıtta döner." if _UZAK else
                           "true: içerik yanıtta döner (token harcar). Varsayılan: dosyaya yazılır, yol döner.")}


def _cikti_notu(yerel: str, uzak: str) -> str:
    return uzak if _UZAK else yerel


# Paylaşılan uçta ÇALIŞAMAYAN araçlar orada listelenmez. Listede duran bir araç modeli onu
# çağırmaya davet eder: tapu_kaydi_oku için bu, müvekkilin tapu kaydının (malik adı, TCKN,
# rehin) kimlik doğrulamasız bir uca gönderilmesi demektir — araç sonra reddetse bile veri
# gitmiştir. Diğer üçü dosya üretir ya da sunucuya indirme yaptırır; her çağrı boşa gider.
YEREL_ARACLAR = ("tapu_kaydi_oku", "parsel_foyu", "portfoy_tablosu", "arazi_3d")

_ONAY = {"type": "boolean",
         "description": "Kullanıcı bu sohbette canlı sorgu onay kartını kabul ettiyse true. İlk çağrıda vermeyin: "
                        "kart döner, kullanıcıya gösterin."}

TUM_ARACLAR = [
    Tool("baslangic",
         "BURADAN BAŞLAYIN. Canlı parsel sorgusu, dosyayla çalışma, araç akışı, hız sınırları ve kapsam dışı "
         "kalanlar. ~300 token.",
         {"type": "object", "properties": {}}, _t_rehber),
    Tool("parsel_sorgula",
         "TKGM Parsel Sorgu'dan CANLI parsel: il/ilçe/mahalle + ada/parsel (ya da hepsi `metin` içinde: "
         "'Kadıköy Caferağa 123 ada 45 parsel'). Kimlik, nitelik, kayıtlı ve hesap alanı, konum, Parsel Sorgu / "
         "OpenStreetMap bağlantıları ve nitelik rejimi uyarıları döner; sonraki araçlar `ref` ile çalışır. İl "
         "verilmemişse ilçeden çıkarıp doldurun. Köy parsellerinde ada 0. Birden çok mahalle eşleşirse `adaylar` "
         "döner: kullanıcıya sorup `mahalle_id` ile yineleyin. İlk canlı çağrı onay kartı döndürür. Malik, şerh, "
         "rehin İÇERMEZ.",
         {"type": "object", "properties": {
             "il": {"type": "string"}, "ilce": {"type": "string"},
             "mahalle": {"type": "string", "description": "mahalle ya da köy adı"},
             "ada": {"type": "integer", "description": "ada no (köyde 0)"}, "parsel": {"type": "integer"},
             "metin": {"type": "string", "description": "serbest metin: yer adları + 'X ada Y parsel' ya da koordinat"},
             "mahalle_id": {"type": "integer", "description": "belirsizlikte `adaylar`dan seçilen kimlik"},
             "onay": _ONAY}},
         _t_parsel_sorgula),
    Tool("konumdan_parsel",
         "Koordinattaki parseli TKGM Parsel Sorgu'dan canlı getirir (enlem, boylam; derece, WGS84). yer_bul'un "
         "adayından ya da kullanıcının verdiği noktadan. Çıktı parsel_sorgula ile aynı.",
         {"type": "object", "properties": {
             "enlem": {"type": "number"}, "boylam": {"type": "number"}, "onay": _ONAY},
          "required": ["enlem", "boylam"]},
         _t_konumdan_parsel),
    Tool("yer_bul",
         "Yer adı ya da adres → en çok beş koordinat adayı (OpenStreetMap Nominatim, yalnız Türkiye): 'Moda "
         "Parkı Kadıköy', 'Bodrum Gümüşlük'. Parsel için sonra konumdan_parsel. Sorgu metni OpenStreetMap'e gider; "
         "müvekkil adı gibi kişisel veri yazmayın.",
         {"type": "object", "properties": {"sorgu": {"type": "string"}, "onay": _ONAY}, "required": ["sorgu"]},
         _t_yer_bul),
    Tool("parsel_raporu",
         "Tek parsel için okunaklı rapor (markdown): kimlik, ölçü, kenarlar, konum bağlantıları, hukuki işaretler, "
         "sınırlar; `konu` verilirse o uyuşmazlığın dayanak adresleri. `ref` ile ya da parsel_sorgula'nın "
         "argümanlarıyla (getirir ve raporlar). " +
         _cikti_notu("Yerel kipte kroki (SVG), harita (HTML) ve Word föyü de dosyaya yazılır.",
                     "Bu uçta dosya yazılmaz; kroki=true ile SVG yanıta eklenir."),
         {"type": "object", "properties": {
             "ref": _REF, "konu": {"type": "string", "enum": list(hukuk.KONULAR)},
             "il": {"type": "string"}, "ilce": {"type": "string"}, "mahalle": {"type": "string"},
             "ada": {"type": "integer"}, "parsel": {"type": "integer"}, "metin": {"type": "string"},
             "mahalle_id": {"type": "integer"}, "onay": _ONAY,
             "kroki": {"type": "boolean", "default": False, "description": "paylaşılan uçta SVG krokiyi de ekle"},
             "dosyalar": {"type": "boolean", "default": True, "description": "yerel kipte kroki/harita/föy dosyaları"}}},
         _t_parsel_raporu),
    Tool("baglanti",
         "Kullanıcının kendi tarayıcısında açacağı Parsel Sorgu bağlantısı (koordinat ya da ref "
         "verilirse doğrudan o noktaya) ve idari sorgu + dosya indirme adımları; ayrıca Web Tapu / "
         "e-Devlet tapu bağlantıları. Bu araç TKGM'ye istek göndermez.",
         {"type": "object", "properties": {
             "enlem": {"type": "number"}, "boylam": {"type": "number"}, "ref": _REF,
             "il": {"type": "string"}, "ilce": {"type": "string"}, "mahalle": {"type": "string"},
             "ada": {"type": "string"}, "parsel": {"type": "string"}}},
         _t_baglanti),
    Tool("parsel_oku",
         "Kullanıcının getirdiği GeoJSON/KML dosyasını okur (canlı sorgu gerekmediğinde); her parsel için ref, "
         "öznitelikler, TM 3° düzleminde hesap alanı, dosyadaki alanla fark ve nitelikten doğan "
         "rejim uyarıları (5403, 6831, 3621…) döner. Sonraki bütün araçlar ref ile çalışır.",
         {"type": "object", "properties": {
             "dosya": {"type": "string", "description": "yerel yol (.geojson/.json/.kml); yalnız yerel kipte"},
             "icerik": {"type": "string", "description": "dosyanın metni; paylaşılan sunucuda tek yol"},
             "dom": {"type": "integer", "description": "yalnız dosya projeksiyon koordinatlıysa: 27…45"},
             "sistem": {"type": "string", "enum": ["tm3", "utm6"], "default": "tm3"}}},
         _t_parsel_oku),
    Tool("geometri",
         "Parselin ölçüleri: alan, çevre, köşe sayısı, projeksiyon; ayrinti=true ile kenar boyları, "
         "semtler ve iç açılar (grad). Köşe numaraları kroki ile aynıdır.",
         {"type": "object", "properties": {"ref": _REF, "ayrinti": {"type": "boolean", "default": True}},
          "required": ["ref"]},
         _t_geometri),
    Tool("kroki",
         "A4, milimetre birimli, ÖLÇEKLİ SVG kroki: kenar boyları, köşe numaraları, kuzey oku, ölçek "
         "çubuğu, bilgi bloğu; `komsular` verilirse çevre parseller gri çizilir. " +
         _cikti_notu("Dosyaya yazar, yol döner (~150 token).",
                     "Bu uçta dosya yazılmaz: SVG metni yanıtın `svg` alanında döner (tipik 5-10 KB, köşe ve "
                     "komşu sayısıyla büyür).") +
         " Aplikasyon/röperli kroki yerine geçmez.",
         {"type": "object", "properties": {
             "ref": _REF,
             "komsular": {"type": "array", "items": {"type": "string"},
                          "description": "çevre parsellerin ref'leri"},
             "koordinat_tablosu": {"type": "boolean", "default": False},
             "inline": _INLINE},
          "required": ["ref"]},
         _t_kroki),
    Tool("disa_aktar",
         "Parseli GeoJSON, KML, DXF (R12, TM koordinatlı — CAD/NetCAD) veya CSV (köşe listesi; dom/epsg "
         "sütunlu) olarak " +
         _cikti_notu("dosyaya yazar.", "verir; bu uçta dosya yazılmaz, içerik yanıtın `icerik` alanında döner.") +
         " `koordinat_sistemi` alanı dosyanın dilimini (DOM/EPSG) söyler.",
         {"type": "object", "properties": {
             "ref": _REF, "bicim": {"type": "string", "enum": list(BICIMLER)}, "inline": _INLINE},
          "required": ["ref", "bicim"]},
         _t_disa_aktar),
    Tool("koordinat_donustur",
         "Coğrafi (enlem, boylam) ↔ ITRF96 TM 3° (DOM 27-45) ya da UTM 6°. Krüger serisi, dilim içinde "
         "mm-altı. Türkiye dışına düşen ya da Y/X sırası ters nokta hata verir; seçilen dilimin (TM 3°'de "
         "±1,5°) dışındaki nokta `dilim_disi` ve `uyari` ile işaretlenir. ED50 datum dönüşümü YAPMAZ.",
         {"type": "object", "properties": {
             "yon": {"type": "string", "enum": ["cografi_tm", "tm_cografi"]},
             "noktalar": {"type": "array", "items": {"type": "array", "items": {"type": "number"}},
                          "description": "cografi_tm: [[enlem, boylam]…]; tm_cografi: [[Y sağa, X yukarı]…]"},
             "dom": {"type": "integer", "description": "dilim orta meridyeni; tm_cografi'de zorunlu"},
             "sistem": {"type": "string", "enum": ["tm3", "utm6"], "default": "tm3"}},
          "required": ["yon", "noktalar"]},
         _t_koordinat),
    Tool("dayanak_koprusu",
         "Parselden hukuka köprü. Argümansız: konu listesi. `konu` ile: dayanak madde ADRESLERİ, teyit "
         "tarihindeki değişiklik uyarıları (`uyari`, `degisiklikler`), parsel verisinin o uyuşmazlıktaki rolü "
         "ve hazır çağrılar (`madde_cagrilari`: tr_mevzuat_madde_getir, tek çağrıda başlık + metin; "
         "tr_ictihat_ara). `ref` ya da `nitelik` ile: niteliğe bağlı özel rejim uyarıları. Hüküm metni vermez: "
         "maddeyi çekmeden alıntılamayın.",
         {"type": "object", "properties": {
             "konu": {"type": "string", "enum": list(hukuk.KONULAR)},
             "ref": _REF, "nitelik": {"type": "string"}}},
         _t_hukuk),
    Tool("koridor_kesisim",
         "Güzergâh (hat + genişlik) ile parsellerin kesişimi: her parselden kesilen alan (m², %), eksenin "
         "parsel içindeki boyu, yalnız tamponla dokunulan parseller. İrtifak/kamulaştırma hesabının ham "
         "verisi. Sonuç ölçülmüş sayısal hata payıyla döner; tam tablo CSV'ye yazılır.",
         {"type": "object", "properties": {
             "refs": {"type": "array", "items": {"type": "string"}, "description": "parsel ref'leri"},
             "hat": {"type": "array", "items": {"type": "array", "items": {"type": "number"}},
                     "description": "[[enlem, boylam]…] — eksen köşe noktaları"},
             "hat_icerik": {"type": "string", "description": "GeoJSON LineString ya da KML metni"},
             "hat_dosya": {"type": "string", "description": "yerel .geojson/.kml; yalnız yerel kipte"},
             "genislik_m": {"type": "number", "description": "koridor TOPLAM genişliği (m)"}},
          "required": ["refs", "genislik_m"]},
         _t_koridor),
    Tool("harita",
         "OpenStreetMap altlıklı etkileşimli HTML harita: parsel vurgulu, `komsular` gri. Sunucu ağ çağrısı "
         "yapmaz; karoları kullanıcının tarayıcısı yükler. " +
         _cikti_notu("Dosyaya yazar, yol döner.",
                     "Bu uçta dosya yazılmaz: HTML metni yanıtın `html` alanında döner (tipik 4-8 KB)."),
         {"type": "object", "properties": {
             "ref": _REF, "komsular": {"type": "array", "items": {"type": "string"}}, "inline": _INLINE},
          "required": ["ref"]},
         _t_harita),
    Tool("arazi_3d",
         "Copernicus GLO-30 yükseklik modeli üstüne parsel: min/max/ortalama kot, kot farkı, ortalama eğim %, "
         "hâkim bakı + çevre binaların TAHMİNİ kütlesi (Overture/OSM tabanı; boy = kat × 3 m: `kat`, yoksa tapu "
         "niteliğindeki 'Altı Katlı' gibi ifade, yoksa açık veri, yoksa varsayılan) + döndürülebilir 3D HTML. "
         "Yanıttaki `uyari_3d` kullanıcıya AYNEN iletilir: görsel gerçek veriye dayanmaz, fikir verme amaçlı "
         "tahmindir. 30 m'lik YÜZEY modelidir; ölçü yerine geçmez. ~1-2 MB indirir. Yalnız yerel kipte.",
         {"type": "object", "properties": {
             "ref": _REF, "komsular": {"type": "array", "items": {"type": "string"}},
             "kat": {"type": "integer", "minimum": 1, "maximum": 80,
                     "description": "parseldeki binanın zemin üstü kat sayısı; YALNIZ kullanıcı söylediyse"},
             "binalar": {"type": "boolean", "default": True, "description": "false: yalnız arazi"},
             "varsayilan_kat": {"type": "integer", "minimum": 1, "maximum": 80, "default": 3,
                                "description": "yüksekliği bilinmeyen binalar için"},
             "inline": _INLINE},
          "required": ["ref"]},
         _t_arazi_3d),
    Tool("parsel_foyu",
         "Tek parsel için Word föyü (.docx): kroki görseli, öznitelik ve ölçü tabloları, kenar tablosu, "
         "hukuki notlar (`konu` verilirse o köprünün dayanak adresleri). Yalnız yerel kipte.",
         {"type": "object", "properties": {
             "ref": _REF, "komsular": {"type": "array", "items": {"type": "string"}},
             "konu": {"type": "string", "enum": list(hukuk.KONULAR)}},
          "required": ["ref"]},
         _t_foy),
    Tool("portfoy_tablosu",
         "Çok parsel için Excel dökümü (.xlsx): konum, ada/parsel, nitelik, alanlar, fark, çevre, merkez, "
         "rejim uyarıları, Parsel Sorgu bağlantısı. Yalnız yerel kipte.",
         {"type": "object", "properties": {"refs": {"type": "array", "items": {"type": "string"}}},
          "required": ["refs"]},
         _t_portfoy),
    Tool("tarife_kalemi",
         "2026 tapu harcı ((4) sayılı tarife) ve TKGM döner sermaye cetvelinde işlem arar; kod, oran/tutar ve "
         "YK/M işaretini döner. Rakamlar kaynak belgeden birebir alıntıyla tutulur.",
         {"type": "object", "properties": {"sorgu": {"type": "string"}}, "required": ["sorgu"]},
         _t_tarife_ara),
    Tool("harc_hesapla",
         "Tapu harcı ve/veya döner sermaye bedeli ön hesabı; her kalem dayanağı ve alıntısıyla. Nispi harçta "
         "matrah emlak vergisi değerinin altına inmez. Tapu işlemlerinin döner sermaye bedeli yöresel katsayı "
         "ister; verilmezse araç UYDURMAZ, nereden alınacağını söyler.",
         {"type": "object", "properties": {
             "tapu_harci_islem": {"type": "string", "description": "tarife_kalemi'dan kod, örn. satis, ipotek"},
             "bedel": {"type": "number", "description": "beyan edilen bedel / ipotekte borç miktarı (TL)"},
             "emlak_vergi_degeri": {"type": "number"},
             "doner_sermaye_kod": {"type": "string", "description": "tarife_kalemi'dan kod, örn. 1.1.2.1"},
             "yoresel_katsayi": {"type": "number", "description": "ilgili tapu müdürlüğünün katsayısı"},
             "adet": {"type": "integer", "default": 1, "description": "taşınmaz / bağımsız bölüm adedi"}}},
         _t_harc),
    Tool("tapu_kaydi_oku",
         "Kullanıcının KENDİ e-Devlet/Web Tapu oturumundan aldığı tapu kaydı METNİNİ yapılandırır: taşınmaz, "
         "malik-hisse, şerh/beyan/irtifak/rehin, hukuki işaretler; `ref` verilirse geometriyle çapraz kontrol. "
         "VARSAYILAN OLARAK MASKELER: TCKN (sağlama doğrulanır, ayraçlı yazım dâhil), IBAN, etiketli VKN, "
         "telefon, e-posta ve MALİK ADLARI ({{MALİK-nn}}, kayıdın her yerinde tutarlı); ArthurMask kuruluysa "
         "şerh/rehin satırlarındaki üçüncü kişi ve kurum adlarını da ({{KİŞİ-nn}}, {{KURUM-nn}}). Malik satırı "
         "tanınmazsa metin işlenmez. Yanıttaki `kvkk` alanı neyin maskelenMEDİĞİNİ de sayar. Eşleştirme "
         "döndürülmez, hiçbir şey saklanmaz. Yalnız yerel kipte; sunucu hiçbir oturuma girmez, PDF'i siz okuyup "
         "metnini verin.",
         {"type": "object", "properties": {
             "metin": {"type": "string"}, "ref": _REF,
             "tc_maskele": {"type": "boolean", "default": True,
                            "description": "kimlik no / IBAN / telefon / e-posta maskesi"},
             "ad_maskele": {"type": "boolean", "default": True,
                            "description": "false: malik adları AÇIK döner — bilinçli bir karardır"},
             "ucuncu_kisi_maskele": {"type": "boolean", "default": True,
                                     "description": "ArthurMask kuruluysa şerh/rehin satırlarındaki üçüncü "
                                                    "kişi ve kurum adlarını da maskeler"}},
          "required": ["metin"]},
         _t_tapu),
    Tool("arayuze_yaz",
         "Yerel arayüzün (python server.py --ui) Sor sekmesinden gelen soruya verdiğin cevabı arayüze teslim "
         "eder: cevap çıktı klasörüne yazılır, arayüz Cevaplar altında gösterir. Kullanıcı mesajı "
         "\"ArthurLegal'de sor:\" ile başlıyorsa cevabı sohbette ver VE bununla arayüze de gönder. Yalnız "
         "yerel kipte; metin düz metin olarak gösterilir.",
         {"type": "object", "properties": {
             "baslik": {"type": "string", "description": "kısa başlık, örn. sorunun özeti"},
             "metin": {"type": "string", "description": "cevabın tamamı (düz metin/markdown)"},
             "ref": _REF},
          "required": ["metin"]},
         _t_arayuze_yaz),
    Tool("server_status", "Çalışma kipi, çıktı klasörü, bellekteki parsel sayısı, canlı sorgu durumu (sınırlar, "
                          "duraklatma, doğrulanmamış uçlar).",
         {"type": "object", "properties": {}}, _t_status),
]

# Yerel kipte hepsi; paylaşılan uçta yalnız orada çalışabilenler. İşleyiciler yine kendi kip
# denetimini yapar (kip çalışma sırasında TKGM_DOSYA_ERISIMI ile değişebilir).
TOOLS = [t for t in TUM_ARACLAR if not (_PAYLASILAN and t.name in YEREL_ARACLAR)]


def _bayrak(ad: str) -> bool:
    """Kendi bayrağımızı argv'den çıkarır; mcpcore'un argparse'ı tanımadığı bayrakta çıkar."""
    if ad in sys.argv:
        sys.argv.remove(ad)
        return True
    return False


def _kapi(varsayilan: int = 8765) -> int:
    """--kapi N; mcpcore'un kendi --port'u MCP taşıması içindir, arayüzünki ayrıdır."""
    if "--kapi" in sys.argv:
        i = sys.argv.index("--kapi")
        deger = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
        del sys.argv[i:i + 2]
        try:
            return int(deger)
        except ValueError:
            sys.stderr.write("--kapi sayı bekliyor; %d kullanılıyor\n" % varsayilan)
    return varsayilan


if __name__ == "__main__":
    # --ui      : yalnız tarayıcı arayüzü (http://127.0.0.1:8765), tarayıcıyı açar.
    # --ui-ile  : MCP (stdio) + aynı süreçte arayüz; Claude'un okuttuğu parseller arayüzde de görünür.
    if _bayrak("--ui"):
        arayuz.calistir(TOOLS, _kapi())
    else:
        if _bayrak("--ui-ile"):
            arayuz.yoklamayi_ac()
            threading.Thread(target=arayuz.calistir, args=(TOOLS, _kapi(), False), daemon=True).start()
        run(TOOLS, name="tkgm-mcp", version=__version__, instructions=INSTRUCTIONS)
