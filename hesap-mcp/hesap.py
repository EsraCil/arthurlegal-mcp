"""hesap — avukatlık asgari ücreti, yargı harcı ve gider avansı hesabı.

Bu modülde TARİFE TUTARI YOKTUR. Bütün tutar, oran, eşik ve çarpanlar `veri/tarife_2026.json`dan
gelir; oradaki her satır Resmî Gazete'deki metinden alıntısını ve kaynak kodunu taşır. Hesap
çıktısı her kalemin yanında dayanağını verir: avukat tutarın nereden geldiğini görür. Yıl
değişince kod değil veri değişir.

Kod yalnız metinde açıkça yazan kuralı uygular. Metinden çıkmayan bir sıra ya da birleşim hesaba
gizlice girmez: toplamın dışında, `teyit_edin` notuyla görünür (veri: `dogrulanamayanlar`).
"""

from __future__ import annotations

import json
import os
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple

_YOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veri", "tarife_2026.json")
_KURUS = Decimal("0.01")


class HesapHatasi(ValueError):
    """İstenen hesap yapılamıyor ya da girdi eksik."""


def _yukle() -> Dict[str, Any]:
    try:
        with open(_YOL, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        return {"surum": None, "kaynaklar": [], "aaut": {"kademeler": [], "maktu": [], "kurallar": {}, "bolumler": {}},
                "harc": {"kalemler": [], "mahkemeler": [], "karar_turleri": [], "kurallar": {}},
                "gider_avansi": {}, "dogrulanamayanlar": [], "uyarilar": [],
                "yukleme_hatasi": "%s: %s" % (type(exc).__name__, exc)}


_VERI = _yukle()
_KAYNAK = {k["kod"]: k for k in _VERI["kaynaklar"]}
_AAUT = _VERI["aaut"]
_MAKTU = {r["kod"]: r for r in _AAUT["maktu"]}
_KURAL = _AAUT["kurallar"]
_HARC = {r["kod"]: r for r in _VERI["harc"]["kalemler"]}
_HKURAL = _VERI["harc"]["kurallar"]
_GA = _VERI["gider_avansi"]


def _d(x: Any) -> Decimal:
    return Decimal(str(x))


def _tl(x: Decimal) -> float:
    return float(x.quantize(_KURUS, rounding=ROUND_HALF_UP))


def _bicim(x: Any) -> str:
    """12345.5 → '12.345,50' (Türkçe yazım; açıklama satırları için)."""
    s = "{:,.2f}".format(float(x))
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def kaynak(kod: str) -> Dict[str, Any]:
    k = _KAYNAK.get(kod) or {}
    rg = None
    if k.get("rg_tarih"):
        y, a, g = k["rg_tarih"].split("-")
        rg = "RG %s.%s.%s, S. %s%s" % (g, a, y, k.get("rg_sayi"),
                                       " (%s. Mükerrer)" % k["mukerrer"] if k.get("mukerrer") else "")
    return {"kod": kod, "ad": k.get("ad"), "rg": rg, "url": k.get("url"), "ek_url": k.get("ek_url")}


def durum() -> Dict[str, Any]:
    return {"surum": _VERI.get("surum"), "derlenme": _VERI.get("derlenme_tarihi"),
            "aaut_maktu_satiri": len(_MAKTU), "aaut_kademe": len(_AAUT["kademeler"]),
            "harc_satiri": len(_HARC), "hata": _VERI.get("yukleme_hatasi")}


def dogrulanamayanlar() -> List[str]:
    return list(_VERI.get("dogrulanamayanlar", []))


# --------------------------------------------------------------------------- avukatlık ücreti
def _aaut_dayanak(ek: str) -> str:
    k = kaynak(_AAUT["kaynak"])
    return "AAÜT %s (%s)" % (ek, k["rg"]) if k["rg"] else "AAÜT %s" % ek


def _maktu_dayanak(kod: str) -> str:
    """'2.2.10' → 'İkinci Kısım İkinci Bölüm 10. sıra' biçiminde okunur dayanak."""
    sira = ["", "Birinci", "İkinci", "Üçüncü", "Dördüncü"]
    k, b, s = kod.split(".", 2)
    return _aaut_dayanak("%s Kısım %s Bölüm %s. sıra" % (sira[int(k)], sira[int(b)], s))


def nispi(tutar: Decimal) -> Tuple[Decimal, List[Dict[str, Any]]]:
    """Üçüncü Kısım: kademeli (dilimli) hesap. Her dilim kendi yüzdesiyle, sıra sıra."""
    kalan, toplam, dokum = tutar, Decimal(0), []
    for k in _AAUT["kademeler"]:
        if kalan <= 0:
            break
        dilim = kalan if k["dilim_tl"] is None else min(kalan, _d(k["dilim_tl"]))
        pay = dilim * _d(k["yuzde"]) / Decimal(100)
        toplam += pay
        kalan -= dilim
        dokum.append({"sira": k["sira"], "dilim_tl": _tl(dilim), "yuzde": k["yuzde"], "ucret_tl": _tl(pay)})
    return toplam, dokum


def _maktu(kod: str) -> Dict[str, Any]:
    r = _MAKTU.get(kod)
    if r is None:
        raise HesapHatasi("AAÜT kalemi bulunamadı: %s. Menü için hesap_kalemleri." % kod)
    return r


def _tutar_iste(tutar: Optional[float], etiket: str) -> Decimal:
    if tutar is None or tutar <= 0:
        raise HesapHatasi("`tutar` gerekli: %s (TL)." % etiket)
    return _d(tutar)


def _ozet(islem: str, asgari: Decimal, kalemler: List[Dict[str, Any]], adimlar: List[str],
          ust: Optional[Decimal] = None, notlar: Optional[List[str]] = None,
          teyit: Optional[List[str]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"islem": islem, "asgari_tl": _tl(asgari), "kalemler": kalemler, "hesap": adimlar}
    if ust is not None:
        out["ust_sinir_tl"] = _tl(ust)
        out["ust_sinir_dayanak"] = _aaut_dayanak(_KURAL["uc_kat"]["dayanak"].replace("AAÜT ", ""))
    if notlar:
        out["notlar"] = notlar
    if teyit:
        out["teyit_edin"] = teyit
    out["kaynak"] = kaynak(_AAUT["kaynak"])
    out["asgari_notu"] = {"metin": _VERI["uyarilar"][0] if _VERI.get("uyarilar") else None,
                          "dayanak": [_KURAL["asgari_ucret"]["citation"], _KURAL["altinda_kararlastirilamaz"]["citation"]],
                          "alinti": _KURAL["altinda_kararlastirilamaz"]["alinti"]}
    return out


def _kural_satiri(ad: str) -> Dict[str, str]:
    r = _KURAL[ad]
    return {"dayanak": _aaut_dayanak(r["dayanak"].replace("AAÜT ", "")), "alinti": r["alinti"]}


def vekalet_nispi(tutar: Optional[float], mahkeme: Optional[str]) -> Dict[str, Any]:
    """Konusu para olan dava: Üçüncü Kısım, mahkemenin maktu ücretinden az olmamak üzere (m. 13/1),
    kabul ya da reddedilen miktarı geçmemek üzere (m. 13/2)."""
    t = _tutar_iste(tutar, "dava değeri ya da hüküm altına alınan miktar")
    if not mahkeme:
        raise HesapHatasi("`mahkeme` gerekli: davanın görüldüğü mahkemenin AAÜT kalemi (hesap_kalemleri).")
    m = _maktu(mahkeme)
    if not m.get("mahkeme"):
        raise HesapHatasi("%s bir mahkeme kalemi değil; mahkeme listesi için hesap_kalemleri." % mahkeme)
    hesap, dokum = nispi(t)
    taban = _d(m["tutar_tl"])
    adimlar = ["Üçüncü Kısım kademeli hesap: %s TL üzerinden %s TL." % (_bicim(t), _bicim(_tl(hesap)))]
    ucret = hesap
    if hesap < taban:
        ucret = taban
        adimlar.append("Mahkemenin maktu ücretinin (%s, %s TL) altında kaldı: maktu ücret esas alındı (m. 13/1)."
                       % (m["mahkeme"], _bicim(taban)))
    if ucret > t:
        ucret = t
        adimlar.append("Ücret kabul ya da reddedilen miktarı geçemez: %s TL ile sınırlandı (m. 13/2)." % _bicim(t))
    kalemler = [{"kalem": "Asgari avukatlık ücreti", "tutar_tl": _tl(ucret),
                 "dayanak": _aaut_dayanak("Üçüncü Kısım; m. 13/1-2"), "dokum": dokum}]
    notlar = [
        "Mahkemenin maktu ücreti (alt sınır): %s TL, %s." % (_bicim(taban), _maktu_dayanak(m["kod"])),
        "Kısmi red, %s: %s" % (_KURAL["kismi_red"]["dayanak"], _KURAL["kismi_red"]["alinti"]),
    ]
    return _ozet("nispi_dava", ucret, kalemler, adimlar, ust=min(ucret * _KURAL["uc_kat"]["carpan"], t),
                 notlar=notlar, teyit=[d for d in dogrulanamayanlar() if "AAÜT" in d or "m. 6" in d])


def vekalet_maktu(kalem: Optional[str], saat: Optional[float] = None) -> Dict[str, Any]:
    if not kalem:
        raise HesapHatasi("`kalem` gerekli: AAÜT maktu kalem kodu (hesap_kalemleri).")
    r = _maktu(kalem)
    ilk = _d(r["tutar_tl"])
    adimlar = ["%s: %s TL." % (r["is"], _bicim(ilk))]
    ucret = ilk
    teyit: List[str] = []
    if r.get("ek_saat_tl") is not None:
        s = 1 if saat in (None, 0) else saat
        if s != int(s) or s < 1 or s > 200:
            raise HesapHatasi("`saat` 1 ile 200 arasında tam sayı olmalı (tarife 'takip eden her saat için' der).")
        ek = _d(r["ek_saat_tl"]) * (int(s) - 1)
        if ek:
            ucret = ilk + ek
            adimlar.append("Takip eden %d saat × %s TL = %s TL." % (int(s) - 1, _bicim(r["ek_saat_tl"]), _bicim(_tl(ek))))
        teyit = [d for d in dogrulanamayanlar() if "kısmi saat" in d]
    elif saat not in (None, 0, 1):
        raise HesapHatasi("Bu kalem saatle artmaz; `saat` vermeyin.")
    kalemler = [{"kalem": r["is"] + (" (aylık)" if r.get("birim") == "aylık" else ""), "tutar_tl": _tl(ucret),
                 "dayanak": _maktu_dayanak(r["kod"]), "alinti": r["alinti"]}]
    notlar = [r["not"]] if r.get("not") else []
    ust = ucret * _KURAL["uc_kat"]["carpan"] if r["kod"].startswith("2.") else None
    return _ozet("maktu", ucret, kalemler, adimlar, ust=ust, notlar=notlar, teyit=teyit)


def vekalet_icra(takip_turu: str, tutar: Optional[float], odeme_suresinde: bool = False) -> Dict[str, Any]:
    adimlar: List[str] = []
    if takip_turu == "tahliye":
        kural = _KURAL["tahliye_takibi"]
        r = _maktu(kural["maktu_kod"])
        ucret = _d(r["tutar_tl"])
        adimlar.append("Tahliyeye ilişkin icra takibi: maktu %s TL (m. 11/5)." % _bicim(ucret))
        dayanak = _maktu_dayanak(r["kod"]) + "; m. 11/5"
    elif takip_turu == "para":
        t = _tutar_iste(tutar, "takip miktarı")
        kural = _KURAL["icra_nispi"]
        if t <= _d(kural["esik_tl"]):
            r = _maktu(kural["maktu_kod"])
            ucret = _d(r["tutar_tl"])
            adimlar.append("Takip miktarı %s TL'ye kadar: icra dairesindeki takipler için maktu %s TL (m. 11/1)."
                           % (_bicim(kural["esik_tl"]), _bicim(ucret)))
            if ucret > t:
                ucret = t
                adimlar.append("Maktu ücret takipte kesinleşen miktarı geçemez: %s TL ile sınırlandı (m. 11/1)."
                               % _bicim(t))
            dayanak = _maktu_dayanak(r["kod"]) + "; m. 11/1"
        else:
            ucret, _ = nispi(t)
            adimlar.append("Üçüncü Kısım kademeli hesap: %s TL üzerinden %s TL (m. 11/1)." % (_bicim(t), _bicim(_tl(ucret))))
            dayanak = _aaut_dayanak("Üçüncü Kısım; m. 11/1")
    else:
        raise HesapHatasi("`takip_turu`: 'para' ya da 'tahliye'.")
    if odeme_suresinde:
        carpan = _d(_KURAL["icra_odeme"]["carpan"])
        ucret = ucret * carpan
        adimlar.append("Borçlu ödeme süresi içinde ödedi: ücretin dörtte üçü, %s TL (m. 11/4)." % _bicim(_tl(ucret)))
        dayanak += "; m. 11/4"
    notlar = []
    if takip_turu == "para":
        notlar.append("Hesap takip miktarını kesinleşen miktar sayar; kesinleşen miktar farklıysa onu girin (m. 11/1).")
    kalemler = [{"kalem": "Asgari avukatlık ücreti (icra takibi)", "tutar_tl": _tl(ucret), "dayanak": dayanak}]
    return _ozet("icra", ucret, kalemler, adimlar, notlar=notlar)


def vekalet_arabuluculuk(sonuc: str, konu: str, tutar: Optional[float] = None,
                         mahkeme: Optional[str] = None) -> Dict[str, Any]:
    if sonuc not in ("anlasma", "anlasmazlik"):
        raise HesapHatasi("`sonuc`: 'anlasma' ya da 'anlasmazlik'.")
    if konu not in ("para", "para_disi"):
        raise HesapHatasi("`konu`: 'para' ya da 'para_disi'.")
    adimlar: List[str] = []
    t = _tutar_iste(tutar, "asıl alacak") if konu == "para" else None
    if sonuc == "anlasmazlik":
        kural = _KURAL["arabuluculuk_anlasmazlik"]
        r = _maktu(kural["maktu_kod"])
        ucret = _d(r["tutar_tl"])
        adimlar.append("Arabuluculuk anlaşmazlıkla sonuçlandı: %s TL (m. 16/2-c)." % _bicim(ucret))
        dayanak = _maktu_dayanak(r["kod"]) + "; m. 16/2-c"
    elif konu == "para":
        kural = _KURAL["arabuluculuk_anlasma"]
        carpan = _d(kural["carpan"])
        if t <= _d(kural["esik_tl"]):
            r = _maktu(kural["maktu_kod"])
            ucret = _d(r["tutar_tl"]) * carpan
            adimlar.append("Miktar %s TL'ye kadar: maktu %s TL'nin dörtte bir fazlası, %s TL (m. 16/2-a)."
                           % (_bicim(kural["esik_tl"]), _bicim(r["tutar_tl"]), _bicim(_tl(ucret))))
        else:
            hesap, _ = nispi(t)
            ucret = hesap * carpan
            adimlar.append("Üçüncü Kısım kademeli hesap %s TL; dörtte bir fazlası %s TL (m. 16/2-a)."
                           % (_bicim(_tl(hesap)), _bicim(_tl(ucret))))
        dayanak = _aaut_dayanak("m. 16/2-a")
    else:
        kural = _KURAL["arabuluculuk_para_disi"]
        if not mahkeme:
            raise HesapHatasi("`mahkeme` gerekli: davanın görüleceği mahkemenin AAÜT kalemi (hesap_kalemleri).")
        r = _maktu(mahkeme)
        if not r.get("mahkeme"):
            raise HesapHatasi("%s bir mahkeme kalemi değil." % mahkeme)
        ucret = _d(r["tutar_tl"]) * _d(kural["carpan"])
        adimlar.append("Konusu para olmayan iş, anlaşma: %s maktu ücreti %s TL'nin dörtte bir fazlası, %s TL (m. 16/2-b)."
                       % (r["mahkeme"], _bicim(r["tutar_tl"]), _bicim(_tl(ucret))))
        dayanak = _maktu_dayanak(r["kod"]) + "; m. 16/2-b"
    if t is not None and ucret > t:
        ucret = t
        adimlar.append("Ücret asıl alacağı geçemez: %s TL ile sınırlandı (m. 16/2)." % _bicim(t))
    kalemler = [{"kalem": "Asgari avukatlık ücreti (arabuluculuk)", "tutar_tl": _tl(ucret), "dayanak": dayanak}]
    return _ozet("arabuluculuk", ucret, kalemler, adimlar,
                 notlar=["Dava şartı arabuluculuk için (m. 16/2). Aynı vekille dava açılırsa anlaşmazlık ücreti "
                         "mahsup edilir (m. 16/2-ç)."])


def vekalet_hakem_heyeti(tutar: Optional[float]) -> Dict[str, Any]:
    t = _tutar_iste(tutar, "asıl alacak")
    kural = _KURAL["hakem_heyeti"]
    hesap, dokum = nispi(t)
    ucret = hesap
    adimlar = ["Üçüncü Kısım kademeli hesap: %s TL üzerinden %s TL." % (_bicim(t), _bicim(_tl(hesap)))]
    maktu = _d(kural["maktu_tl"])
    if hesap < maktu:
        ucret = min(maktu, t)
        adimlar.append("Maktu ücretin (%s TL) altında kaldı: asıl alacağı geçmemek kaydıyla %s TL." % (_bicim(maktu), _bicim(_tl(ucret))))
    kalemler = [{"kalem": "Asgari avukatlık ücreti (hakem heyeti)", "tutar_tl": _tl(ucret),
                 "dayanak": _aaut_dayanak("Birinci Kısım İkinci Bölüm 6. sıra"), "dokum": dokum}]
    return _ozet("hakem_heyeti", ucret, kalemler, adimlar)


def vekalet(islem: str, **g: Any) -> Dict[str, Any]:
    if islem == "nispi_dava":
        return vekalet_nispi(g.get("tutar"), g.get("mahkeme"))
    if islem == "maktu":
        return vekalet_maktu(g.get("kalem"), g.get("saat"))
    if islem == "icra":
        return vekalet_icra(g.get("takip_turu") or "para", g.get("tutar"), bool(g.get("odeme_suresinde")))
    if islem == "arabuluculuk":
        return vekalet_arabuluculuk(g.get("sonuc") or "", g.get("konu") or "", g.get("tutar"), g.get("mahkeme"))
    if islem == "hakem_heyeti":
        return vekalet_hakem_heyeti(g.get("tutar"))
    raise HesapHatasi("Bilinmeyen işlem: %s. Menü için hesap_kalemleri." % islem)


# --------------------------------------------------------------------------- yargı harcı
def _harc_dayanak(kod: str) -> str:
    k = kaynak(_VERI["harc"]["kaynak"])
    return "492 s. K. (1) sayılı tarife %s; tutar: 98 Seri No'lu Harçlar Kanunu Genel Tebliği eki (%s)" % (kod, k["rg"])


def _harc_kalemi(kod: str, etiket: Optional[str] = None, tutar: Optional[Decimal] = None,
                 hesap: Optional[str] = None) -> Dict[str, Any]:
    r = _HARC.get(kod)
    if r is None:
        raise HesapHatasi("Harç kalemi veride yok: %s" % kod)
    out = {"kod": kod, "kalem": etiket or "%s: %s" % (r["baslik"], r["kalem"]),
           "tutar_tl": _tl(tutar if tutar is not None else _d(r["tutar_tl"])), "dayanak": _harc_dayanak(kod)}
    if hesap:
        out["hesap"] = hesap
    return out


def _mahkeme(kod: Optional[str]) -> Dict[str, Any]:
    for m in _VERI["harc"]["mahkemeler"]:
        if m["kod"] == kod:
            return m
    raise HesapHatasi("`mahkeme`: %s." % ", ".join("'%s' (%s)" % (m["kod"], m["ad"]) for m in _VERI["harc"]["mahkemeler"]))


def karar_harci_tam(deger: Decimal, karar_turu: str = "genel") -> Tuple[Decimal, str, List[str]]:
    """Nispi karar ve ilam harcının tamamı; alt sınır tebliğ ekindeki 'Nispi harçlar ... aşağı olamaz' satırı."""
    tur = next((t for t in _VERI["harc"]["karar_turleri"] if t["kod"] == karar_turu), None)
    if tur is None:
        raise HesapHatasi("`karar_turu`: %s." % ", ".join(t["kod"] for t in _VERI["harc"]["karar_turleri"]))
    r = _HARC[tur["kalem"]]
    tam = deger * _d(r["oran_binde"]) / Decimal(1000)
    adimlar = ["%s TL × binde %s = %s TL." % (_bicim(deger), str(r["oran_binde"]).replace(".", ","), _bicim(_tl(tam)))]
    alt = _d(_HARC["A-III-1-asgari"]["tutar_tl"])
    if tam < alt:
        tam = alt
        adimlar.append("Nispi harç alt sınırı %s TL uygulandı." % _bicim(alt))
    return tam, tur["kalem"], adimlar


def gider_avansi(taraf_sayisi: Optional[int], tebligat_ucreti: Optional[float]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"dayanak": "%s (%s); %s" % (_GA["dayanak"], kaynak(_GA["kaynak"])["rg"], _GA["kanun"]["citation"]),
                           "alinti": _GA["alinti"],
                           "formul": "taraf sayısı × %d × tebligat ücreti + %s TL" % (_GA["tebligat_kati"], _bicim(_GA["diger_islemler_tl"]))}
    if not taraf_sayisi:
        out["eksik"] = "taraf_sayisi"
        return out
    if taraf_sayisi != int(taraf_sayisi) or not 2 <= taraf_sayisi <= 500:
        raise HesapHatasi("`taraf_sayisi` 2 ile 500 arasında tam sayı olmalı.")
    if tebligat_ucreti is None or tebligat_ucreti <= 0:
        out["eksik"] = "tebligat_ucreti"
        out["not"] = "Tarife tebligat ücretinin tutarını belirtmez; güncel bir tebligat ücretini girin."
        return out
    teb = _d(taraf_sayisi) * _d(_GA["tebligat_kati"]) * _d(tebligat_ucreti)
    toplam = teb + _d(_GA["diger_islemler_tl"])
    out.update({"tebligat_tl": _tl(teb), "diger_islemler_tl": _tl(_d(_GA["diger_islemler_tl"])), "toplam_tl": _tl(toplam),
                "hesap": "%d taraf × %d × %s TL + %s TL" % (int(taraf_sayisi), _GA["tebligat_kati"],
                                                            _bicim(tebligat_ucreti), _bicim(_GA["diger_islemler_tl"]))})
    return out


def _harc_ozet(islem: str, kalemler: List[Dict[str, Any]], ayrica: List[Dict[str, Any]],
               notlar: List[str], teyit: List[str], ek: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"islem": islem, "kalemler": kalemler,
                           "toplam_tl": _tl(sum((_d(k["tutar_tl"]) for k in kalemler), Decimal(0)))}
    if ek:
        out.update(ek)
    if ayrica:
        out["ayrica"] = ayrica
    out["notlar"] = notlar + ["Harç muafiyeti hesaba girmez; örnek: %s." % _HKURAL["muafiyet_ornegi"]["citation"]]
    if teyit:
        out["teyit_edin"] = teyit
    out["kaynaklar"] = [kaynak(_VERI["harc"]["kaynak"])] + ([kaynak(_GA["kaynak"])] if islem == "dava_acilis" else [])
    out["uyari"] = _VERI["uyarilar"][1] if len(_VERI.get("uyarilar", [])) > 1 else None
    return out


def _teyit(*anahtar: str) -> List[str]:
    return [d for d in dogrulanamayanlar() if any(a in d for a in anahtar)]


def harc_dava_acilis(mahkeme: Optional[str], deger: Optional[float], taraf_sayisi: Optional[int],
                     tebligat_ucreti: Optional[float], olum_cismani: bool = False) -> Dict[str, Any]:
    m = _mahkeme(mahkeme)
    kalemler = [_harc_kalemi(m["basvurma"], "Başvurma harcı (%s)" % m["ad"])]
    ayrica: List[Dict[str, Any]] = []
    teyit: List[str] = []
    notlar: List[str] = []
    if deger:
        if deger < 0:
            raise HesapHatasi("`deger` eksi olamaz.")
        tam, kod, adimlar = karar_harci_tam(_d(deger))
        kural = _HKURAL["pesin"]
        pay = _d(kural["olum_cismani_pay"] if olum_cismani else kural["pay"])
        pesin = tam * pay
        kalemler.append({"kod": kod, "kalem": "Peşin karar ve ilam harcı (%s)" % ("yirmide bir" if olum_cismani else "dörtte bir"),
                         "tutar_tl": _tl(pesin), "dayanak": "%s; %s" % (_harc_dayanak(kod), kural["citation"]),
                         "hesap": " ".join(adimlar) + " Peşin pay: %s TL." % _bicim(_tl(pesin))})
        notlar.append("Kalan karar ve ilam harcı kararın tebliğinden itibaren bir ay içinde ödenir (%s)." % kural["citation"])
        teyit += _teyit("Nispi karar ve ilam harcının alt sınırı", "Alt sınırın peşin")
    else:
        ayrica.append(dict(_harc_kalemi("A-III-2-a", "Karar ve ilam harcı (maktu)"),
                           teyit="Dava açılırken mi karar sırasında mı alındığını teyit edin (%s)."
                                 % _HKURAL["maktu_odeme"]["citation"]))
        teyit += _teyit("Maktu karar")
    ga = gider_avansi(taraf_sayisi, tebligat_ucreti)
    if "toplam_tl" in ga:
        kalemler.append({"kod": "gider_avansi", "kalem": "Gider avansı", "tutar_tl": ga["toplam_tl"],
                         "dayanak": ga["dayanak"], "hesap": ga["hesap"]})
    teyit += _teyit("Tebligat ücreti")
    return _harc_ozet("dava_acilis", kalemler, ayrica, notlar, teyit, {"gider_avansi": ga})


def harc_karar(deger: Optional[float], karar_turu: str = "genel", pesin_odenen: Optional[float] = None) -> Dict[str, Any]:
    if not deger or deger <= 0:
        raise HesapHatasi("`deger` gerekli: hüküm altına alınan değer (TL).")
    tam, kod, adimlar = karar_harci_tam(_d(deger), karar_turu or "genel")
    kalemler = [{"kod": kod, "kalem": "Karar ve ilam harcı (tamamı)", "tutar_tl": _tl(tam),
                 "dayanak": _harc_dayanak(kod), "hesap": " ".join(adimlar)}]
    ek: Dict[str, Any] = {}
    if pesin_odenen:
        kalan = tam - _d(pesin_odenen)
        ek = {"pesin_odenen_tl": _tl(_d(pesin_odenen)), "kalan_tl": _tl(max(kalan, Decimal(0))),
              "kalan_dayanak": _HKURAL["pesin"]["citation"]}
    return _harc_ozet("karar_harci", kalemler, [], [], _teyit("Nispi karar ve ilam harcının alt sınırı"), ek)


def harc_kanun_yolu(islem: str, yargi: Optional[str]) -> Dict[str, Any]:
    kodlar = {("istinaf", "adli"): "A-IV-e", ("istinaf", "idari"): "A-IV-d",
              ("temyiz", "adli"): "A-IV-a", ("temyiz", "idari"): "A-IV-c"}
    kod = kodlar.get((islem, yargi or ""))
    if kod is None:
        raise HesapHatasi("`yargi`: 'adli' ya da 'idari'.")
    ayrica = [dict(_harc_kalemi("A-I-3"), teyit="Kanun yolu harcıyla birlikte alınıp alınmadığını teyit edin.")]
    return _harc_ozet(islem, [_harc_kalemi(kod)], ayrica,
                      ["Bölge adliye mahkemesi ya da Yargıtay işin esasını karara bağlarsa nispi karar ve ilam harcı "
                       "aynı oranla alınır ((1) sayılı tarife A-III-1-e)."], _teyit("İstinaf ve temyiz"))


def harc_ihtiyati(mahkeme: Optional[str]) -> Dict[str, Any]:
    m = _mahkeme(mahkeme)
    ayrica = [dict(_harc_kalemi("A-III-2-d", "Karar harcı (tespit, ihtiyati haciz, ihtiyati tedbir kararı)"),
                   teyit="Talepte mi karar verilince mi alındığını teyit edin (%s)." % _HKURAL["maktu_odeme"]["citation"])]
    return _harc_ozet("ihtiyati", [_harc_kalemi(m["basvurma"], "Başvurma harcı (%s)" % m["ad"])], ayrica, [], [])


def harc(islem: str, **g: Any) -> Dict[str, Any]:
    if islem == "dava_acilis":
        return harc_dava_acilis(g.get("mahkeme"), g.get("deger"), g.get("taraf_sayisi"), g.get("tebligat_ucreti"),
                                bool(g.get("olum_cismani")))
    if islem == "karar_harci":
        return harc_karar(g.get("deger"), g.get("karar_turu") or "genel", g.get("pesin_odenen"))
    if islem in ("istinaf", "temyiz"):
        return harc_kanun_yolu(islem, g.get("yargi"))
    if islem == "ihtiyati":
        return harc_ihtiyati(g.get("mahkeme"))
    if islem == "kesif":
        return _harc_ozet("kesif", [_harc_kalemi("A-V")], [], [], [])
    if islem == "aym":
        return _harc_ozet("aym", [_harc_kalemi("A-I-4", "Başvurma harcı (Anayasa Mahkemesi)")], [], [], [])
    raise HesapHatasi("Bilinmeyen işlem: %s. Menü için hesap_kalemleri." % islem)


# --------------------------------------------------------------------------- menü
# Kullanıcı kod değil işlem seçer. Her işlemin istediği kutular burada; seçeneklerin kendisi
# (mahkemeler, maktu kalemler, karar türleri) veriden gelir. `yalniz`: kutu ancak öteki kutunun
# değeri buysa görünür ("dolu": herhangi bir değer).
def _mahkeme_secenekleri() -> List[Dict[str, Any]]:
    return [{"kod": r["kod"], "ad": r["mahkeme"], "tutar_tl": r["tutar_tl"]} for r in _AAUT["maktu"] if r.get("mahkeme")]


def _maktu_gruplari() -> List[Dict[str, Any]]:
    gruplar: Dict[str, Dict[str, Any]] = {}
    for r in _AAUT["maktu"]:
        b = ".".join(r["kod"].split(".")[:2])
        g = gruplar.setdefault(b, {"bolum": _AAUT["bolumler"].get(b, b), "secenekler": []})
        g["secenekler"].append({"kod": r["kod"], "ad": r["is"], "tutar_tl": r["tutar_tl"],
                                "ek_saat_tl": r.get("ek_saat_tl"), "birim": r.get("birim")})
    return list(gruplar.values())


def menu() -> Dict[str, Any]:
    tutar = lambda etiket, zorunlu=True, **k: dict({"ad": "tutar", "tur": "tutar", "etiket": etiket, "zorunlu": zorunlu}, **k)  # noqa: E731
    mahkeme = {"ad": "mahkeme", "tur": "secim", "etiket": "Mahkeme", "zorunlu": True, "secenekler": _mahkeme_secenekleri()}
    vekalet_islemleri = [
        {"kod": "nispi_dava", "ad": "Para alacağı ya da değeri belli dava",
         "girdiler": [tutar("Dava değeri ya da hüküm altına alınan miktar (TL)"), mahkeme]},
        {"kod": "maktu", "ad": "Maktu ücretli iş ya da dava",
         "girdiler": [{"ad": "kalem", "tur": "secim", "etiket": "İş", "zorunlu": True, "gruplar": _maktu_gruplari()},
                      {"ad": "saat", "tur": "sayi", "etiket": "Toplam saat", "zorunlu": False, "varsayilan": 1,
                       "yalniz": {"kalem": "ek_saat"}}]},
        {"kod": "icra", "ad": "İcra takibi",
         "girdiler": [{"ad": "takip_turu", "tur": "secim", "etiket": "Takip", "zorunlu": True,
                       "secenekler": [{"kod": "para", "ad": "Para alacağı"}, {"kod": "tahliye", "ad": "Tahliye"}]},
                      tutar("Takip miktarı (TL)", yalniz={"takip_turu": "para"}),
                      {"ad": "odeme_suresinde", "tur": "onay", "etiket": "Borçlu ödeme süresi içinde ödedi", "zorunlu": False}]},
        {"kod": "arabuluculuk", "ad": "Dava şartı arabuluculuk",
         "girdiler": [{"ad": "sonuc", "tur": "secim", "etiket": "Sonuç", "zorunlu": True,
                       "secenekler": [{"kod": "anlasma", "ad": "Anlaşma"}, {"kod": "anlasmazlik", "ad": "Anlaşmazlık"}]},
                      {"ad": "konu", "tur": "secim", "etiket": "Konu", "zorunlu": True,
                       "secenekler": [{"kod": "para", "ad": "Para ya da parayla ölçülebilir"},
                                      {"kod": "para_disi", "ad": "Parayla ölçülemez"}]},
                      tutar("Asıl alacak (TL)", yalniz={"konu": "para"}),
                      dict(mahkeme, etiket="Davanın görüleceği mahkeme", yalniz={"konu": "para_disi", "sonuc": "anlasma"})]},
        {"kod": "hakem_heyeti", "ad": "Tüketici ya da hal hakem heyeti", "girdiler": [tutar("Asıl alacak (TL)")]},
    ]
    harc_mahkeme = {"ad": "mahkeme", "tur": "secim", "etiket": "Mahkeme", "zorunlu": True,
                    "secenekler": [{"kod": m["kod"], "ad": m["ad"]} for m in _VERI["harc"]["mahkemeler"]]}
    harc_islemleri = [
        {"kod": "dava_acilis", "ad": "Dava açılışı: harçlar ve gider avansı",
         "girdiler": [harc_mahkeme,
                      {"ad": "deger", "tur": "tutar", "etiket": "Dava değeri (TL); değeri olmayan davada boş bırakın", "zorunlu": False},
                      {"ad": "olum_cismani", "tur": "onay", "etiket": "Ölüm ya da cismani zarar tazminatı davası",
                       "zorunlu": False, "yalniz": {"deger": "dolu"}},
                      {"ad": "taraf_sayisi", "tur": "sayi", "etiket": "Taraf sayısı", "zorunlu": True, "varsayilan": 2},
                      {"ad": "tebligat_ucreti", "tur": "tutar", "etiket": "Bir tebligatın ücreti (TL)", "zorunlu": False,
                       "aciklama": "Gider avansı tarifesi bu tutarı yazmaz; boş bırakırsanız gider avansı hesaplanmaz."}]},
        {"kod": "karar_harci", "ad": "Karar ve ilam harcı (karar sonrası)",
         "girdiler": [{"ad": "karar_turu", "tur": "secim", "etiket": "Karar", "zorunlu": True,
                       "secenekler": [{"kod": t["kod"], "ad": t["ad"]} for t in _VERI["harc"]["karar_turleri"]]},
                      {"ad": "deger", "tur": "tutar", "etiket": "Hüküm altına alınan değer (TL)", "zorunlu": True},
                      {"ad": "pesin_odenen", "tur": "tutar", "etiket": "Peşin ödenen harç (TL)", "zorunlu": False}]},
        {"kod": "istinaf", "ad": "İstinaf başvurusu",
         "girdiler": [{"ad": "yargi", "tur": "secim", "etiket": "Mahkeme", "zorunlu": True,
                       "secenekler": [{"kod": "adli", "ad": "Bölge adliye mahkemesi (hukuk)"},
                                      {"kod": "idari", "ad": "Bölge idare mahkemesi"}]}]},
        {"kod": "temyiz", "ad": "Temyiz başvurusu",
         "girdiler": [{"ad": "yargi", "tur": "secim", "etiket": "Mahkeme", "zorunlu": True,
                       "secenekler": [{"kod": "adli", "ad": "Yargıtay (hukuk daireleri)"}, {"kod": "idari", "ad": "Danıştay"}]}]},
        {"kod": "ihtiyati", "ad": "İhtiyati tedbir, ihtiyati haciz ya da delil tespiti talebi", "girdiler": [harc_mahkeme]},
        {"kod": "kesif", "ad": "Keşif harcı", "girdiler": []},
        {"kod": "aym", "ad": "Anayasa Mahkemesine bireysel başvuru", "girdiler": []},
    ]
    return {"surum": _VERI.get("surum"),
            "vekalet": {"islemler": vekalet_islemleri, "kaynak": kaynak(_AAUT["kaynak"]),
                        "asgari_notu": _VERI["uyarilar"][0] if _VERI.get("uyarilar") else None},
            "harc": {"islemler": harc_islemleri,
                     "kaynaklar": [kaynak(_VERI["harc"]["kaynak"]), kaynak(_GA["kaynak"])]}}
