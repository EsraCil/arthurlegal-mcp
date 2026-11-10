#!/usr/bin/env python3
"""hesap-mcp — Türk avukatları için hesap kartları: avukatlık asgari ücreti, yargı harcı, gider avansı, faiz dökümü,
kıdem ve ihbar tazminatı.

    python server.py                                 # stdio
    python server.py --transport http --port 8070    # HTTP

Hesabı model değil KOD yapar (hesap.py, faiz.py, iscilik.py); tutar ve oranlar veri/tarife_2026.json'daki resmî
metinden gelir ve her kalem dayanağını ve kaynağını taşır. Dosya yazma yok, yalnız standart kütüphane.

Tek ağ erişimi tazelik denetimidir (tazelik.py): günde yaklaşık bir kez Resmî Gazete'nin günlük fihristine bakılır;
kartları etkileyebilecek yeni bir yayım varsa ya da bakılamadıysa sonuç bunu söyler (tazelik kilidi).
Kullanıcının girdileri hiçbir yere gönderilmez.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, Optional

import faiz
import hesap
import iscilik
import tazelik
from mcpcore import McpError, Tool, run

__version__ = "0.3.0"

TAZELIK_KURALI = ("Cevapta tazelik satırını (kaynak RG tarih/sayı, son kontrol) aynen yazın. Durum `guncel` "
                  "değilse toplamı kesin sonuç gibi sunmayın; UYARI satırını ve bağlantıyı verin.")

INSTRUCTIONS = """Hesap kartları: avukatlık asgari ücreti (AAÜT), yargı harcı ve gider avansı, faiz dökümü (kanuni, ticari
temerrüt, sabit oran), kıdem ve ihbar tazminatı. Hesabı KOD yapar.
- Tutarı kendiniz hesaplamayın, toplamayın, yuvarlamayın: aracın döndürdüğü kalemleri, dayanaklarını ve kaynak
  bağlantısını aynen aktarın. Önce `hesap_kalemleri` (işlem menüsü ve her işlemin istediği girdiler).
- Vekâlet ücreti çıktısı tarifenin ASGARİ tutarıdır; "önerilen" ya da "uygun" ücret diye sunmayın.
- `teyit_edin` ve `ayrica` kalemlerini kullanıcıya iletin; bunlar toplama katılmaz.
- Tebligat ücreti tarifede yazmaz: kullanıcıdan isteyin, tahmin etmeyin.
- Faiz: başlangıç (temerrüt) ve bitiş tarihini, işin ticari olup olmadığını kullanıcı söyler; oranı siz önermeyin.
- İşçilik alacağı BRÜTTÜR; hak kazanma koşullarını (fesih nedeni) ve net tutarı araç hesaplamaz.
- Süre (son gün) hesabı bu araçlarda yok.
- TAZELİK: her sonuç `tazelik` taşır (Resmî Gazete'nin günlük denetimi). Cevapta tazelik satırını (`tazelik.satir`:
  kaynak RG tarih/sayı, son kontrol) aynen yazın. Durum `guncel` değilse toplamı kesin sonuç gibi sunmayın; UYARI
  satırını ve bağlantıyı verin. `yeni_duzenleme`de toplam null'dır, `teyit_bekleyen_tutarlar` kesin değildir;
  `kontrol_edilemedi`de tutarlar verinin RG tarihine göredir ve teyit edilmemiştir."""


def _kisa(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"), default=str)


def _sayi(deger: Any, ad: str) -> Optional[float]:
    """Sayı ya da Türkçe yazım ("3.000.000", "1.312,40"); NaN, sonsuz ve eksi reddedilir."""
    if deger is None or deger == "":
        return None
    if isinstance(deger, bool):
        raise McpError("`%s` sayı olmalı." % ad)
    if isinstance(deger, str):
        s = deger.strip().replace(" ", "").replace("TL", "").replace("₺", "")
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
        elif "," in s:
            s = s.replace(",", ".")
        elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")
        deger = s
    try:
        x = float(deger)
    except (TypeError, ValueError):
        raise McpError("`%s` sayı olmalı." % ad) from None
    if not math.isfinite(x) or x < 0 or x > 1e13:
        raise McpError("`%s` geçerli bir tutar değil." % ad)
    return x


KARTLAR = ("vekalet", "harc", "faiz", "iscilik")


def _durum() -> Dict[str, Any]:
    return dict(hesap.durum(), faiz=faiz.durum(), iscilik=iscilik.durum())


def _tarih(deger: Any, ad: str):
    try:
        return iscilik.tarih_ayikla(deger, ad)
    except hesap.HesapHatasi as exc:
        raise McpError(str(exc)) from None


def _t_kalemler(args: Dict[str, Any]) -> Any:
    m = hesap.menu()
    m["faiz"] = faiz.menu()
    m["iscilik"] = iscilik.menu()
    kart = args.get("kart")
    if kart in KARTLAR:
        m = {"surum": m["surum"], kart: m[kart]}
    elif kart:
        raise McpError("`kart`: %s." % ", ".join("'%s'" % k for k in KARTLAR))
    m["veri"] = _durum()
    return _kisa(tazelik.kilitle(m, kart or None, kilit=False))


def _t_vekalet(args: Dict[str, Any]) -> Any:
    islem = str(args.get("islem") or "")
    g = {k: args.get(k) for k in ("mahkeme", "kalem", "takip_turu", "sonuc", "konu")}
    g["tutar"] = _sayi(args.get("tutar"), "tutar")
    g["saat"] = _sayi(args.get("saat"), "saat")
    g["odeme_suresinde"] = bool(args.get("odeme_suresinde"))
    try:
        out = hesap.vekalet(islem, **g)
    except hesap.HesapHatasi as exc:
        raise McpError(str(exc)) from None
    out["veri"] = hesap.durum()
    return _kisa(tazelik.kilitle(out, "vekalet"))


def _t_harc(args: Dict[str, Any]) -> Any:
    islem = str(args.get("islem") or "")
    g: Dict[str, Any] = {k: args.get(k) for k in ("mahkeme", "karar_turu", "yargi")}
    for ad in ("deger", "tebligat_ucreti", "pesin_odenen"):
        g[ad] = _sayi(args.get(ad), ad)
    taraf = _sayi(args.get("taraf_sayisi"), "taraf_sayisi")
    g["taraf_sayisi"] = int(taraf) if taraf is not None and taraf == int(taraf) else taraf
    g["olum_cismani"] = bool(args.get("olum_cismani"))
    try:
        out = hesap.harc(islem, **g)
    except hesap.HesapHatasi as exc:
        raise McpError(str(exc)) from None
    out["veri"] = hesap.durum()
    return _kisa(tazelik.kilitle(out, "harc"))


def _t_faiz(args: Dict[str, Any]) -> Any:
    tur = str(args.get("faiz_turu") or args.get("islem") or "")
    anapara = _sayi(args.get("anapara"), "anapara")
    bas, bit = _tarih(args.get("baslangic"), "baslangic"), _tarih(args.get("bitis"), "bitis")
    oran = _sayi(args.get("oran"), "oran")
    try:
        out = faiz.faiz(tur, anapara, bas, bit, oran)
    except hesap.HesapHatasi as exc:
        raise McpError(str(exc)) from None
    out["veri"] = _durum()
    return _kisa(tazelik.kilitle(out, "faiz"))


def _t_iscilik(args: Dict[str, Any]) -> Any:
    giris, cikis = _tarih(args.get("giris"), "giris"), _tarih(args.get("cikis"), "cikis")
    brut, ek = _sayi(args.get("brut_ucret"), "brut_ucret"), _sayi(args.get("ek_menfaat"), "ek_menfaat")
    try:
        out = iscilik.iscilik(str(args.get("islem") or "kidem_ihbar"), giris, cikis, brut, ek)
    except hesap.HesapHatasi as exc:
        raise McpError(str(exc)) from None
    out["veri"] = _durum()
    return _kisa(tazelik.kilitle(out, "iscilik"))


_TUTAR = {"type": ["number", "string"], "description": "TL; 1250000 ya da \"1.250.000,00\""}
_TARIH = {"type": "string", "description": "YYYY-AA-GG ya da GG.AA.YYYY"}

TOOLS = [
    Tool("kalemleri",
         "Hesap kartlarının işlem menüsü: avukatlık asgari ücreti (AAÜT 2026), yargı harcı/gider avansı, faiz dökümü ve "
         "işçilik alacağı (kıdem, ihbar) işlemleri, her işlemin istediği girdiler ve seçenekleri, kaynak künyeleri ve "
         "verinin tazeliği (Resmî Gazete'nin günlük denetimi). " + TAZELIK_KURALI,
         {"type": "object", "properties": {
             "kart": {"type": "string", "enum": list(KARTLAR), "description": "Yalnız bir kartın menüsü."}}},
         _t_kalemler),
    Tool("vekalet_ucreti",
         "Avukatlık Asgari Ücret Tarifesi'ne (RG 03.11.2026, 33419) göre ASGARİ avukatlık ücreti; hesap kodla yapılır, "
         "her adım ve dayanak çıktıda. islem: nispi_dava (tutar + mahkeme), maktu (kalem [+ saat]), icra "
         "(takip_turu para|tahliye [+ tutar] [+ odeme_suresinde]), arabuluculuk (sonuc + konu [+ tutar | mahkeme]), "
         "hakem_heyeti (tutar). Kodlar için hesap_kalemleri. " + TAZELIK_KURALI,
         {"type": "object", "required": ["islem"], "properties": {
             "islem": {"type": "string", "enum": ["nispi_dava", "maktu", "icra", "arabuluculuk", "hakem_heyeti"]},
             "tutar": _TUTAR,
             "mahkeme": {"type": "string", "description": "AAÜT mahkeme kalemi, örn. 2.2.10 (asliye)"},
             "kalem": {"type": "string", "description": "AAÜT maktu kalem kodu, örn. 1.1.1, 2.2.13"},
             "saat": {"type": ["integer", "string"], "description": "Danışma kalemlerinde toplam saat (tam sayı)"},
             "takip_turu": {"type": "string", "enum": ["para", "tahliye"]},
             "odeme_suresinde": {"type": "boolean", "description": "Borçlu ödeme süresi içinde ödedi (m. 11/4)"},
             "sonuc": {"type": "string", "enum": ["anlasma", "anlasmazlik"]},
             "konu": {"type": "string", "enum": ["para", "para_disi"]}}},
         _t_vekalet),
    Tool("yargi_harci",
         "492 sayılı Harçlar Kanunu (1) sayılı tarife (2026 tutarları: 98 Seri No'lu Tebliğ, RG 31.12.2025) ve HMK "
         "gider avansı tarifesine göre harç ön hesabı; hesap kodla yapılır, kalemler dayanaklarıyla. islem: "
         "dava_acilis (mahkeme [+ deger] [+ taraf_sayisi + tebligat_ucreti] [+ olum_cismani]), karar_harci "
         "(deger [+ karar_turu] [+ pesin_odenen]), istinaf|temyiz (yargi adli|idari), ihtiyati (mahkeme), kesif, aym. "
         + TAZELIK_KURALI,
         {"type": "object", "required": ["islem"], "properties": {
             "islem": {"type": "string", "enum": ["dava_acilis", "karar_harci", "istinaf", "temyiz", "ihtiyati", "kesif", "aym"]},
             "mahkeme": {"type": "string", "enum": ["sulh", "asliye", "bolge_yuksek"]},
             "deger": _TUTAR,
             "olum_cismani": {"type": "boolean", "description": "Ölüm ya da cismani zarar tazminatı davası (peşin harç yirmide bir)"},
             "taraf_sayisi": {"type": ["integer", "string"]},
             "tebligat_ucreti": dict(_TUTAR, description="Bir tebligatın ücreti (TL); tarifede yazmaz, kullanıcı verir"),
             "karar_turu": {"type": "string", "enum": ["genel", "izale_satis", "taksim", "nafaka"]},
             "pesin_odenen": _TUTAR,
             "yargi": {"type": "string", "enum": ["adli", "idari"]}}},
         _t_harc),
    Tool("faiz",
         "Faiz dökümü, dönem dönem ve gün sayılı (icra takibi ve alacak için): kanuni faiz (3095 m. 1; 31.07.2026'dan beri "
         "TCMB reeskont oranının %80'i, önceki dönemler BKK 2005/9831 ve CB Kararı 8485), ticari işlerde temerrüt faizi "
         "(3095 m. 2/2: TCMB avans oranı, 31 Aralık ve 30 Haziran kuralı) ya da sözleşmede yazılı sabit oran. Oranlar "
         "Resmî Gazete'deki TCMB tebliğlerinden; hesap kodla, basit faiz (m. 3). faiz_turu: kanuni|ticari|sabit; anapara, "
         "baslangic (temerrüt), bitis (takip/ödeme) [+ oran yalnız sabitte]. " + TAZELIK_KURALI,
         {"type": "object", "required": ["faiz_turu", "anapara", "baslangic", "bitis"], "properties": {
             "faiz_turu": {"type": "string", "enum": ["kanuni", "ticari", "sabit"]},
             "anapara": _TUTAR,
             "baslangic": dict(_TARIH, description="Faizin başlangıç (temerrüt) tarihi; YYYY-AA-GG ya da GG.AA.YYYY"),
             "bitis": dict(_TARIH, description="Bitiş (takip ya da ödeme) tarihi; faiz bu günden önceki güne kadar işler"),
             "oran": {"type": ["number", "string"], "description": "Yalnız sabit: sözleşmede yazılı yıllık oran, yüzde"}}},
         _t_faiz),
    Tool("iscilik_alacagi",
         "İşçilik alacağı ön hesabı, BRÜT: kıdem tazminatı (1475 m. 14; 30 günlük giydirilmiş ücret, çıkış tarihindeki "
         "dönemin kıdem tavanıyla sınırlı, tam yıl + artan gün) ve ihbar tazminatı (4857 m. 17; 2/4/6/8 hafta). Kıdem "
         "tavanı ÇSGB tablosundan (Resmî Gazete'de yayımlanmaz). islem: kidem_ihbar|kidem|ihbar; giris, cikis, brut_ucret "
         "[+ ek_menfaat: düzenli ek ödemeler, aylık]. Hak kazanma ve net tutar hesaba girmez. " + TAZELIK_KURALI,
         {"type": "object", "required": ["giris", "cikis", "brut_ucret"], "properties": {
             "islem": {"type": "string", "enum": ["kidem_ihbar", "kidem", "ihbar"]},
             "giris": dict(_TARIH, description="İşe giriş tarihi"),
             "cikis": dict(_TARIH, description="İş sözleşmesinin sona erdiği tarih"),
             "brut_ucret": dict(_TUTAR, description="Son aylık brüt ücret (TL)"),
             "ek_menfaat": dict(_TUTAR, description="Düzenli ek ödemeler (yol, yemek, ikramiye...), aylık toplam (TL)")}},
         _t_iscilik),
]


if __name__ == "__main__":
    run(TOOLS, name="hesap-mcp", version=__version__, instructions=INSTRUCTIONS)
