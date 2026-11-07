#!/usr/bin/env python3
"""hesap-mcp — Türk avukatları için hesap kartları: avukatlık asgari ücreti, yargı harcı, gider avansı.

    python server.py                                 # stdio
    python server.py --transport http --port 8070    # HTTP

Hesabı model değil KOD yapar (hesap.py); tutarlar veri/tarife_2026.json'daki resmî metinden gelir ve
her kalem dayanağını ve kaynağını taşır. Ağ yok, dosya yazma yok, yalnız standart kütüphane.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, Optional

import hesap
from mcpcore import McpError, Tool, run

__version__ = "0.1.0"

INSTRUCTIONS = """Hesap kartları: avukatlık asgari ücreti (AAÜT), yargı harcı ve gider avansı. Hesabı KOD yapar.
- Tutarı kendiniz hesaplamayın, toplamayın, yuvarlamayın: aracın döndürdüğü kalemleri, dayanaklarını ve kaynak
  bağlantısını aynen aktarın. Önce `hesap_kalemleri` (işlem menüsü ve her işlemin istediği girdiler).
- Vekâlet ücreti çıktısı tarifenin ASGARİ tutarıdır; "önerilen" ya da "uygun" ücret diye sunmayın.
- `teyit_edin` ve `ayrica` kalemlerini kullanıcıya iletin; bunlar toplama katılmaz.
- Tebligat ücreti tarifede yazmaz: kullanıcıdan isteyin, tahmin etmeyin.
- Faiz, işçilik alacağı ve süre hesabı bu araçlarda yok."""


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


def _t_kalemler(args: Dict[str, Any]) -> Any:
    m = hesap.menu()
    kart = args.get("kart")
    if kart in ("vekalet", "harc"):
        m = {"surum": m["surum"], kart: m[kart]}
    elif kart:
        raise McpError("`kart`: 'vekalet' ya da 'harc'.")
    m["veri"] = hesap.durum()
    return _kisa(m)


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
    return _kisa(out)


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
    return _kisa(out)


_TUTAR = {"type": ["number", "string"], "description": "TL; 1250000 ya da \"1.250.000,00\""}

TOOLS = [
    Tool("kalemleri",
         "Hesap kartlarının işlem menüsü: avukatlık asgari ücreti (AAÜT 2026) ve yargı harcı/gider avansı işlemleri, "
         "her işlemin istediği girdiler ve seçenekleri (mahkemeler, maktu kalemler), kaynak Resmî Gazete künyeleri.",
         {"type": "object", "properties": {
             "kart": {"type": "string", "enum": ["vekalet", "harc"], "description": "Yalnız bir kartın menüsü."}}},
         _t_kalemler),
    Tool("vekalet_ucreti",
         "Avukatlık Asgari Ücret Tarifesi'ne (RG 03.11.2026, 33419) göre ASGARİ avukatlık ücreti; hesap kodla yapılır, "
         "her adım ve dayanak çıktıda. islem: nispi_dava (tutar + mahkeme), maktu (kalem [+ saat]), icra "
         "(takip_turu para|tahliye [+ tutar] [+ odeme_suresinde]), arabuluculuk (sonuc + konu [+ tutar | mahkeme]), "
         "hakem_heyeti (tutar). Kodlar için hesap_kalemleri.",
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
         "(deger [+ karar_turu] [+ pesin_odenen]), istinaf|temyiz (yargi adli|idari), ihtiyati (mahkeme), kesif, aym.",
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
]


if __name__ == "__main__":
    run(TOOLS, name="hesap-mcp", version=__version__, instructions=INSTRUCTIONS)
