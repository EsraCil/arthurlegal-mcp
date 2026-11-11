"""iscilik — kıdem tazminatı (1475 s. K. m. 14) ve ihbar tazminatı (4857 s. K. m. 17) ön hesabı, BRÜT.

Bu modülde TUTAR YOKTUR. Kıdem tazminatı tavanı dönem dönem `veri/tarife_2026.json` → `iscilik.kidem_tavani`
tablosundadır (kaynak: ÇSGB tablosu, SHA-256'sıyla; tavan Resmî Gazete'de yayımlanmaz). İhbar süreleri m. 17'nin
bentlerinden veride durur. Kod yalnız şunu yapar:

* hizmet süresini yıl, ay, gün olarak sayar (giriş ve çıkış günleri dahil);
* kıdem: 30 günlük ücret (giydirilmiş, tavanla sınırlı) × (tam yıl + artan gün / 365);
* ihbar: m. 17 bendine göre hafta × 7 gün × günlük ücret (aylık / 30).

Uygulamaya dayanan yöntem seçimleri `teyit_edin` olarak görünür; hak kazanma koşulları ve kesintiler hesaba girmez.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

import hesap
from hesap import HesapHatasi, _bicim, _d, _tl, kaynak

_I: Dict[str, Any] = hesap._VERI.get("iscilik") or {}
_KURAL: Dict[str, Any] = _I.get("kurallar") or {}
_TAVAN: List[Dict[str, Any]] = sorted(_I.get("kidem_tavani") or [], key=lambda r: r["bas"])
_IHBAR: List[Dict[str, Any]] = list(_I.get("ihbar") or [])


def _tarih(g: date) -> str:
    return g.strftime("%d.%m.%Y")


def _yil_ekle(g: date, n: int) -> date:
    try:
        return g.replace(year=g.year + n)
    except ValueError:                       # 29 Şubat → 28 Şubat
        return g.replace(year=g.year + n, day=28)


def _ay_ekle(g: date, n: int) -> date:
    y, m = divmod(g.month - 1 + n, 12)
    yil, ay = g.year + y, m + 1
    for gun in (g.day, 30, 29, 28):
        try:
            return date(yil, ay, gun)
        except ValueError:
            continue
    raise ValueError(g)


def hizmet_suresi(giris: date, cikis: date) -> Dict[str, int]:
    """Giriş ve çıkış günleri dahil: (yıl, ay, gün) ve toplam gün."""
    son = cikis + timedelta(days=1)
    yil = 0
    while _yil_ekle(giris, yil + 1) <= son:
        yil += 1
    g = _yil_ekle(giris, yil)
    ay = 0
    while _ay_ekle(g, ay + 1) <= son:
        ay += 1
    gun = (son - _ay_ekle(g, ay)).days
    return {"yil": yil, "ay": ay, "gun": gun, "toplam_gun": (son - giris).days,
            "yildan_artan_gun": (son - _yil_ekle(giris, yil)).days}


def _sure_yazi(s: Dict[str, int]) -> str:
    return "%d yıl %d ay %d gün" % (s["yil"], s["ay"], s["gun"])


def tavan(gun: date) -> Dict[str, Any]:
    for r in _TAVAN:
        if date.fromisoformat(r["bas"]) <= gun <= date.fromisoformat(r["son"]):
            return r
    if not _TAVAN:
        raise HesapHatasi("Kıdem tazminatı tavanı verisi yüklenemedi.")
    raise HesapHatasi("Çıkış tarihi %s için kıdem tazminatı tavanı veride yok (veri %s–%s). Tavanı ÇSGB ya da Hazine ve "
                      "Maliye Bakanlığı genelgesinden teyit edin." % (_tarih(gun), _tarih(date.fromisoformat(_TAVAN[0]["bas"])),
                                                                       _tarih(date.fromisoformat(_TAVAN[-1]["son"]))))


def ihbar_bendi(s: Dict[str, int]) -> Dict[str, Any]:
    """4857 m. 17/2: altı aydan az 2; altı aydan birbuçuk yıla kadar 4; birbuçuk yıldan üç yıla kadar 6; fazlası 8 hafta."""
    ay = s["yil"] * 12 + s["ay"]
    for b in _IHBAR:
        if b["ay_alti"] is None:
            return b
        if ay < b["ay_alti"] or (b["sinir_dahil"] and ay == b["ay_alti"] and s["gun"] == 0):
            return b
    raise HesapHatasi("İhbar süreleri veride yok.")


def _tavan_dayanak(r: Dict[str, Any]) -> str:
    k = kaynak(_I.get("kaynak", "CSGBKT"))
    return "%s–%s dönemi tavanı %s TL (%s)" % (_tarih(date.fromisoformat(r["bas"])), _tarih(date.fromisoformat(r["son"])),
                                               _bicim(r["tutar_tl"]), k["ad"])


def iscilik(islem: str, giris: Optional[date], cikis: Optional[date], brut_ucret: Optional[float],
            ek_menfaat: Optional[float] = None) -> Dict[str, Any]:
    if islem not in ("kidem_ihbar", "kidem", "ihbar"):
        raise HesapHatasi("`islem`: 'kidem_ihbar', 'kidem' ya da 'ihbar'.")
    if giris is None or cikis is None:
        raise HesapHatasi("`giris` ve `cikis` gerekli: işe giriş ve iş sözleşmesinin sona erdiği tarih.")
    if cikis < giris:
        raise HesapHatasi("Çıkış tarihi girişten önce olamaz.")
    if giris.year < 1950 or cikis.year > 2100:
        raise HesapHatasi("Tarihler 1950-2100 arasında olmalı.")
    if brut_ucret is None or brut_ucret <= 0:
        raise HesapHatasi("`brut_ucret` gerekli: son aylık brüt ücret (TL).")
    ek = _d(ek_menfaat or 0)
    if ek < 0:
        raise HesapHatasi("`ek_menfaat` eksi olamaz.")
    s = hizmet_suresi(giris, cikis)
    aylik = _d(brut_ucret) + ek
    gunluk = aylik / _d(_I.get("ay_gun", 30))
    kalemler: List[Dict[str, Any]] = []
    notlar: List[str] = []
    ek_alan: Dict[str, Any] = {}
    kodlar: List[str] = []
    if islem in ("kidem_ihbar", "kidem"):
        kodlar += ["K1475", _I.get("kaynak", "CSGBKT")]
        if s["yil"] < 1:
            notlar.append("Hizmet süresi bir yıldan kısa (%s): kıdem tazminatı hesaplanmadı (%s: \"her geçen tam yıl\")."
                          % (_sure_yazi(s), _KURAL["k14"]["citation"]))
        else:
            t = tavan(cikis)
            tv = _d(t["tutar_tl"])
            esas = min(aylik, tv)
            oran = Decimal(s["yil"]) + Decimal(s["yildan_artan_gun"]) / Decimal(365)
            kidem = esas * oran
            tavan_yazi = ("tavan %s TL uygulandı" % _bicim(t["tutar_tl"])) if aylik > tv else \
                         ("tavanın (%s TL) altında" % _bicim(t["tutar_tl"]))
            kalemler.append({
                "kalem": "Kıdem tazminatı (brüt)", "tutar_tl": _tl(kidem),
                "dayanak": "%s; %s" % (_KURAL["k14"]["citation"], _tavan_dayanak(t)),
                "hesap": "30 günlük ücret %s TL (%s) × (%d yıl + %d gün / 365)" % (
                    _bicim(_tl(esas)), tavan_yazi, s["yil"], s["yildan_artan_gun"])})
            ek_alan["kidem"] = {"esas_ucret_tl": _tl(esas), "tavan_tl": t["tutar_tl"], "tavan_donemi": [t["bas"], t["son"]],
                                "tavan_uygulandi": aylik > tv, "tam_yil": s["yil"], "artan_gun": s["yildan_artan_gun"]}
            notlar.append("Tavan: %s: %s" % (_KURAL["k14_tavan"]["citation"], _KURAL["k14_tavan"]["alinti"]))
            notlar.append("Kıdem tazminatının geç ödenmesinde faiz: %s: %s Bu oran veride yok."
                          % (_KURAL["k14_faiz"]["citation"], _KURAL["k14_faiz"]["alinti"]))
    if islem in ("kidem_ihbar", "ihbar"):
        kodlar += ["K4857"]
        b = ihbar_bendi(s)
        gun = b["hafta"] * 7
        ihbar = gunluk * Decimal(gun)
        kalemler.append({
            "kalem": "İhbar tazminatı (brüt, %d hafta)" % b["hafta"], "tutar_tl": _tl(ihbar),
            "dayanak": "%s/2-%s ve son fıkra: %s" % (_KURAL["m17"]["citation"], b["bent"], b["alinti"]),
            "hesap": "Günlük ücret %s TL (aylık %s / 30) × %d gün" % (_bicim(_tl(gunluk)), _bicim(_tl(aylik)), gun)})
        ek_alan["ihbar"] = {"hafta": b["hafta"], "gun": gun, "bent": b["bent"]}
        notlar.append("Bildirim süreleri asgaridir (%s: \"%s\"); sözleşmeyle artırılmış süre ve kötü niyet tazminatı "
                      "(bildirim süresinin üç katı) hesaba girmez." % (_KURAL["m17_artirma"]["citation"],
                                                                        _KURAL["m17_artirma"]["alinti"]))
    toplam = sum((_d(k["tutar_tl"]) for k in kalemler), Decimal(0))
    notlar.insert(0, "Hizmet süresi %s (%s–%s, giriş ve çıkış günleri dahil %d gün)."
                  % (_sure_yazi(s), _tarih(giris), _tarih(cikis), s["toplam_gun"]))
    notlar.insert(1, "Esas ücret: brüt %s TL%s = %s TL (giydirilmiş). %s: %s" % (
        _bicim(brut_ucret), (" + ek ödemeler %s TL" % _bicim(_tl(ek))) if ek else "", _bicim(_tl(aylik)),
        _KURAL["k14_giydirilmis"]["citation"] if islem != "ihbar" else _KURAL["m17_giydirilmis"]["citation"],
        _KURAL["k14_giydirilmis"]["alinti"] if islem != "ihbar" else _KURAL["m17_giydirilmis"]["alinti"]))
    notlar.append("Tutarlar brüttür; net tutar hesaplanmaz. Kıdem tazminatı gelir vergisinden müstesnadır: %s."
                  % _KURAL["gvk25"]["citation"])
    notlar.append("Hak kazanma koşulları (fesih nedeni; 1475 m. 14'te sayılan haller, bildirim süresine uyulup uyulmadığı) "
                  "hesap tarafından denetlenmez.")
    teyit = [x for x in hesap.dogrulanamayanlar() if x.startswith("İşçilik:")]
    if islem == "ihbar":
        teyit = [x for x in teyit if "kıdem tavanı" not in x and "bir yıldan kısa" not in x]
    elif islem == "kidem":
        teyit = [x for x in teyit if "17'deki 'birbuçuk" not in x]
    adlar = {"kidem_ihbar": "Kıdem ve ihbar tazminatı", "kidem": "Kıdem tazminatı", "ihbar": "İhbar tazminatı"}
    out: Dict[str, Any] = {"islem": islem, "ad": adlar[islem], "toplam_tl": _tl(toplam), "toplam_etiketi": "Toplam (brüt)",
                           "kalemler": kalemler, "hizmet_suresi": s,
                           "ucret": {"brut_tl": _tl(_d(brut_ucret)), "ek_menfaat_tl": _tl(ek), "aylik_esas_tl": _tl(aylik),
                                     "gunluk_tl": _tl(gunluk)}}
    out.update(ek_alan)
    out["notlar"] = notlar
    out["teyit_edin"] = teyit
    out["kaynaklar"] = [kaynak(k) for k in kodlar]
    out["uyari"] = _I.get("uyari")
    return out


def menu() -> Dict[str, Any]:
    ilk = _TAVAN[0]["bas"] if _TAVAN else None
    son = _TAVAN[-1]["son"] if _TAVAN else None
    girdiler = [
        {"ad": "giris", "tur": "tarih", "etiket": "İşe giriş tarihi", "zorunlu": True},
        {"ad": "cikis", "tur": "tarih", "etiket": "İş sözleşmesinin sona erdiği tarih", "zorunlu": True},
        {"ad": "brut_ucret", "tur": "tutar", "etiket": "Son aylık brüt ücret (TL)", "zorunlu": True},
        {"ad": "ek_menfaat", "tur": "tutar", "etiket": "Düzenli ek ödemeler, aylık (TL)", "zorunlu": False,
         "aciklama": "Yol, yemek, ikramiye gibi düzenli para ve para ile ölçülebilen menfaatlerin aylık karşılığı; "
                     "hangilerinin ekleneceğini teyit edin. Kıdem tavanı verisi %s–%s arasındaki çıkış tarihleri içindir."
                     % (ilk and _tarih(date.fromisoformat(ilk)), son and _tarih(date.fromisoformat(son)))},
    ]
    return {"surum": hesap._VERI.get("surum"),
            "islemler": [{"kod": "kidem_ihbar", "ad": "Kıdem ve ihbar tazminatı", "girdiler": girdiler},
                         {"kod": "kidem", "ad": "Yalnız kıdem tazminatı", "girdiler": girdiler},
                         {"kod": "ihbar", "ad": "Yalnız ihbar tazminatı", "girdiler": girdiler}],
            "kaynaklar": [kaynak(_I.get("kaynak", "CSGBKT"))],
            "tavan_donemi": {"bas": ilk, "son": son}}


def durum() -> Dict[str, Any]:
    return {"kidem_tavani_donem": len(_TAVAN), "ihbar_bendi": len(_IHBAR)}


def tarih_ayikla(deger: Any, ad: str) -> Optional[date]:
    """'2026-03-15' ya da '15.03.2026' (sunucu girdisi)."""
    if deger is None or deger == "":
        return None
    s = str(deger).strip()
    try:
        if "." in s or "/" in s:
            g, a, y = s.replace("/", ".").split(".")
            return date(int(y), int(a), int(g))
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        raise HesapHatasi("`%s` tarih olmalı: GG.AA.YYYY ya da YYYY-AA-GG." % ad) from None
