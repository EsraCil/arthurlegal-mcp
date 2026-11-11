"""faiz — kanuni faiz, ticari işlerde temerrüt faizi ve sabit oranlı faiz dökümü (dönem dönem, gün sayılı).

Bu modülde ORAN YOKTUR. Kanuni faiz dönemleri, TCMB reeskont ve avans oranları `veri/tarife_2026.json` → `faiz`
bölümündedir; her TCMB oranı kendi Resmî Gazete tebliğinin künyesini ve SHA-256'sını taşır. Kod yalnız 3095 sayılı
Kanunun kuralını uygular:

* m. 1 (7589 ile değişik, 31.07.2026'dan beri): kanuni faiz, önceki yılın 31 Aralık günü uygulanan reeskont oranının
  yüzde sekseni; 30 Haziran'daki oran beş puan ya da daha çok farklıysa yılın ikinci yarısında o oranın yüzde sekseni.
  Daha önceki günler için oran veride tarihli dönem olarak durur (Bakanlar Kurulu ve Cumhurbaşkanı kararları).
* m. 2/2: ticari işlerde temerrüt faizi, önceki yılın 31 Aralık günü uygulanan avans oranı (kanuni faizden fazlaysa);
  30 Haziran kuralı aynı.
* m. 3: mürekkep faiz yürütülmez; hesap basit faizdir.

Metinden çıkmayan yöntem seçimleri (gün sayımı, yıl esası, yuvarlama) toplamı değiştirmeden `teyit_edin` olarak görünür.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple

import hesap
from hesap import HesapHatasi, _bicim, _d, _tl, kaynak

_F: Dict[str, Any] = hesap._VERI.get("faiz") or {}
_KURAL: Dict[str, Any] = _F.get("kurallar") or {}
_TCMB: List[Dict[str, Any]] = sorted(_F.get("tcmb") or [], key=lambda r: r["yururluk"])
_KANUNI: List[Dict[str, Any]] = sorted(_F.get("kanuni") or [], key=lambda r: r["bas"])
_KURUS = Decimal("0.01")


def _gun(s: str) -> date:
    return date.fromisoformat(s)


def _tarih(g: date) -> str:
    return g.strftime("%d.%m.%Y")


def _yuzde(x: Decimal) -> str:
    """Decimal('12.5') → '%12,5'; Decimal('8.000') → '%8'."""
    s = format(x.normalize(), "f")
    return "%" + s.replace(".", ",")


def kapsam() -> Tuple[date, date]:
    k = _F.get("kapsam") or {}
    if not k.get("bas") or not k.get("son"):
        raise HesapHatasi("Faiz verisi yüklenemedi.")
    return _gun(k["bas"]), _gun(k["son"])


def _rg(r: Dict[str, Any]) -> str:
    y, a, g = r["rg_tarih"].split("-")
    return "RG %s.%s.%s, S. %s" % (g, a, y, r["rg_sayi"])


def tcmb_satiri(gun: date) -> Dict[str, Any]:
    """O gün uygulanan TCMB oranları (yürürlük tarihi o gün ya da önce olan son tebliğ)."""
    son = None
    for r in _TCMB:
        if _gun(r["yururluk"]) <= gun:
            son = r
        else:
            break
    if son is None:
        raise HesapHatasi("%s tarihinde uygulanan TCMB oranı veride yok." % _tarih(gun))
    return son


def yari_yil(yil: int, yari: int, tur: str) -> Tuple[Decimal, Dict[str, Any], str]:
    """3095 m. 1 ve m. 2/2: yılın ilk yarısında önceki yılın 31 Aralık oranı; ikinci yarıda 30 Haziran oranı beş puan ya
    da daha çok farklıysa o oran. Döner: (oran, tebliğ satırı, belirleme açıklaması)."""
    aralik = date(yil - 1, 12, 31)
    teyit = (_F.get("kapsam") or {}).get("oranlar_teyit")
    son_gun = date(yil, 6, 30) if yari == 2 else aralik
    if not teyit or son_gun > _gun(teyit):
        # Oranı belirleyen gün verinin teyit edildiği günden sonra: o gün uygulanacak oran henüz bilinmiyor.
        raise HesapHatasi("%d yılının %s yarısındaki oran %s günü uygulanan TCMB oranına bağlıdır; veri %s tarihine kadar "
                          "teyitli." % (yil, "ilk" if yari == 1 else "ikinci", _tarih(son_gun),
                                        _tarih(_gun(teyit)) if teyit else "-"))
    r1 = tcmb_satiri(aralik)
    o1 = _d(r1[tur])
    if yari == 1:
        return o1, r1, "31.12.%d günü uygulanan oran" % (yil - 1)
    haziran = date(yil, 6, 30)
    r2 = tcmb_satiri(haziran)
    o2 = _d(r2[tur])
    if abs(o2 - o1) >= _d(_F.get("esik_puan", 5)):
        return o2, r2, "30.06.%d oranı 31.12.%d oranından en az %s puan farklı" % (yil, yil - 1, _F.get("esik_puan", 5))
    return o1, r1, "31.12.%d günü uygulanan oran (30.06.%d farkı %s puandan az)" % (yil - 1, yil, _F.get("esik_puan", 5))


def _teblig_dayanak(r: Dict[str, Any]) -> str:
    s = "TCMB tebliği, %s" % _rg(r)
    if r.get("duzeltme"):
        d = r["duzeltme"]
        y, a, g = d["rg_tarih"].split("-")
        s += " (düzeltme RG %s.%s.%s, S. %s)" % (g, a, y, d["rg_sayi"])
    return s


def kanuni_orani(gun: date) -> Tuple[Decimal, str, str]:
    """(oran, dayanak, değişim anahtarı). Anahtar aynı kaldıkça oran ve dayanak aynıdır."""
    donem = None
    for d in _KANUNI:
        if _gun(d["bas"]) <= gun:
            donem = d
    if donem is None:
        raise HesapHatasi("%s tarihi için kanuni faiz oranı veride yok (veri %s'ten başlar)."
                          % (_tarih(gun), _tarih(_gun(_KANUNI[0]["bas"])) if _KANUNI else "-"))
    k = kaynak(donem["kaynak"])
    if donem.get("kural") == "reeskont":
        yari = 1 if gun.month <= 6 else 2
        r, satir, _ = yari_yil(gun.year, yari, "reeskont")
        oran = r * _d(donem["carpan"])
        dayanak = "%s (7589 ile değişik, %s): reeskont oranı %s × %%%s = %s; %s" % (
            _KURAL["m1"]["citation"], k["rg"], _yuzde(r), _bicim(float(_d(donem["carpan"]) * 100)).replace(",00", ""),
            _yuzde(oran), _teblig_dayanak(satir))
        return oran, dayanak, "k:%s:%s" % (donem["bas"], satir["yururluk"])
    oran = _d(donem["oran"])
    ad = (k["ad"] or "").split(":")[0]
    return oran, "%s; %s (%s)" % (_KURAL["m1"]["citation"], ad, k["rg"]), "k:%s" % donem["bas"]


def ticari_orani(gun: date) -> Tuple[Decimal, str, str]:
    """3095 m. 2/2: avans oranı kanuni faizden fazlaysa avans oranı, değilse kanuni faiz."""
    yari = 1 if gun.month <= 6 else 2
    a, satir, _ = yari_yil(gun.year, yari, "avans")
    k, k_dayanak, k_anahtar = kanuni_orani(gun)
    if a > k:
        return a, "%s: TCMB avans oranı %s; %s" % (_KURAL["m2_2"]["citation"], _yuzde(a), _teblig_dayanak(satir)), \
            "t:%s" % satir["yururluk"]
    return k, "%s: avans oranı (%s) kanuni faizden fazla değil, kanuni faiz uygulandı; %s" % (
        _KURAL["m2_2"]["citation"], _yuzde(a), k_dayanak), "tk:%s:%s" % (satir["yururluk"], k_anahtar)


def _sinirlar(bas: date, bit: date) -> List[date]:
    """bas ile bit arasında oranın değişebileceği günler: 1 Ocak, 1 Temmuz ve kanuni faiz dönem başları."""
    out = set()
    for y in range(bas.year, bit.year + 1):
        for g in (date(y, 1, 1), date(y, 7, 1)):
            if bas < g < bit:
                out.add(g)
    for d in _KANUNI:
        g = _gun(d["bas"])
        if bas < g < bit:
            out.add(g)
    return sorted(out)


def _oranlayici(tur: str, sabit: Optional[Decimal]):
    if tur == "kanuni":
        return kanuni_orani
    if tur == "ticari":
        return ticari_orani
    if tur == "sabit":
        return lambda g: (sabit, "Sözleşmede yazılı yıllık oran (kullanıcı girdisi)", "s")
    raise HesapHatasi("`faiz_turu`: 'kanuni', 'ticari' ya da 'sabit'.")


def faiz(faiz_turu: str, anapara: Optional[float], baslangic: Optional[date], bitis: Optional[date],
         oran: Optional[float] = None) -> Dict[str, Any]:
    if anapara is None or anapara <= 0:
        raise HesapHatasi("`anapara` gerekli: faiz işletilecek tutar (TL).")
    if baslangic is None or bitis is None:
        raise HesapHatasi("`baslangic` ve `bitis` gerekli (GG.AA.YYYY ya da YYYY-AA-GG).")
    if bitis <= baslangic:
        raise HesapHatasi("Bitiş tarihi başlangıçtan sonra olmalı.")
    sabit = None
    if faiz_turu == "sabit":
        if oran is None or oran <= 0 or oran > 1000:
            raise HesapHatasi("`oran` gerekli: sözleşmede yazılı yıllık faiz oranı (yüzde, örn. 36).")
        sabit = _d(oran)
        if (bitis - baslangic).days > 366 * 100:
            raise HesapHatasi("Süre en çok 100 yıl olabilir.")
    elif oran is not None:
        raise HesapHatasi("`oran` yalnız 'sabit' faiz türünde verilir; kanuni ve ticari faizde oran veriden gelir.")
    if faiz_turu in ("kanuni", "ticari"):
        kbas, kson = kapsam()
        if baslangic < kbas:
            raise HesapHatasi("Başlangıç %s veri kapsamından önce: faiz oranları %s'ten başlar."
                              % (_tarih(baslangic), _tarih(kbas)))
        if bitis > kson + timedelta(days=1):
            raise HesapHatasi("Bitiş %s veri kapsamını aşıyor: oranlar %s'e kadar belirli. %s"
                              % (_tarih(bitis), _tarih(kson), (_F.get("kapsam") or {}).get("not", "")))
    oranla = _oranlayici(faiz_turu, sabit)
    a = _d(anapara)
    yil = _d(_F.get("gun_esasi", 365))
    kesimler = [baslangic] + _sinirlar(baslangic, bitis) + [bitis]
    donemler: List[Dict[str, Any]] = []
    for g1, g2 in zip(kesimler, kesimler[1:]):
        o, dayanak, anahtar = oranla(g1)
        if donemler and donemler[-1]["_anahtar"] == anahtar:
            donemler[-1]["_son"] = g2
            continue
        donemler.append({"_bas": g1, "_son": g2, "_oran": o, "_dayanak": dayanak, "_anahtar": anahtar})
    kalemler: List[Dict[str, Any]] = []
    toplam = Decimal(0)
    for d in donemler:
        gun = (d["_son"] - d["_bas"]).days
        tutar = (a * d["_oran"] / Decimal(100) * Decimal(gun) / yil).quantize(_KURUS, rounding=ROUND_HALF_UP)
        toplam += tutar
        kalemler.append({
            "kalem": "%s – %s (%d gün, yıllık %s)" % (_tarih(d["_bas"]), _tarih(d["_son"] - timedelta(days=1)), gun,
                                                      _yuzde(d["_oran"])),
            "bas": d["_bas"].isoformat(), "son": (d["_son"] - timedelta(days=1)).isoformat(), "gun": gun,
            "oran": float(d["_oran"]), "tutar_tl": _tl(tutar), "dayanak": d["_dayanak"],
            "hesap": "%s TL × %s × %d / %s" % (_bicim(anapara), _yuzde(d["_oran"]), gun, yil)})
    toplam_gun = (bitis - baslangic).days
    adlar = {"kanuni": "Kanuni faiz", "ticari": "Ticari işlerde temerrüt faizi (avans faizi)",
             "sabit": "Sözleşmede yazılı sabit oranla faiz"}
    notlar = [
        "Faiz %s'ten %s'e kadar %d gün işletildi (bitiş günü hariç); her dönemin faizi kuruşa yuvarlandı."
        % (_tarih(baslangic), _tarih(bitis - timedelta(days=1)), toplam_gun),
        "Anapara %s TL + işlemiş faiz %s TL = %s TL." % (_bicim(anapara), _bicim(_tl(toplam)), _bicim(_tl(a + toplam))),
        "Basit faiz: %s: %s" % (_KURAL["m3"]["citation"], _KURAL["m3"]["alinti"]),
        "Kısmi ödemeler hesaba girmez; mahsup için %s." % _KURAL["tbk100"]["citation"],
    ]
    if faiz_turu == "ticari":
        notlar.insert(2, "Oran her yarıyıl için belirlenir: %s: %s" % (_KURAL["m2_2"]["citation"], _KURAL["m2_2"]["alinti"]))
        notlar.append("İşin ticari iş olup olmadığını hesap denetlemez; ticari değilse kanuni faizi seçin (%s)."
                      % _KURAL["m2_1"]["citation"])
    elif faiz_turu == "kanuni":
        notlar.insert(2, "Temerrüt faizi, sözleşmede aksi yoksa kanuni faiz oranıyla işler: %s." % _KURAL["m2_1"]["citation"])
    else:
        notlar.append("Girilen oranın sözleşme sınırına uygunluğu denetlenmez: %s: %s"
                      % (_KURAL["tbk120"]["citation"], _KURAL["tbk120"]["alinti"]))
    out: Dict[str, Any] = {"islem": faiz_turu, "ad": adlar[faiz_turu], "anapara_tl": _tl(a),
                           "toplam_tl": _tl(toplam), "toplam_etiketi": "İşlemiş faiz",
                           "anapara_ile_tl": _tl(a + toplam), "gun": toplam_gun, "kalemler": kalemler}
    if faiz_turu != "sabit":
        try:
            o, dayanak, _ = oranla(bitis)
            out["bitis_orani"] = {"tarih": bitis.isoformat(), "oran": float(o), "dayanak": dayanak}
            notlar.append("Bitiş tarihinde (%s) uygulanan oran %s; takipten sonra işleyecek faiz için dönemlere göre "
                          "değişebilir." % (_tarih(bitis), _yuzde(o)))
        except HesapHatasi:
            pass
    out["notlar"] = notlar
    teyit = [x for x in hesap.dogrulanamayanlar() if x.startswith("Faiz:")]
    if faiz_turu == "sabit":
        teyit = [x for x in teyit if "gün sayımı" in x or "yuvarlan" in x]
    elif faiz_turu == "kanuni":
        teyit = [x for x in teyit if "avans oranındaki" not in x]
    if bitis <= date(2026, 7, 31):
        teyit = [x for x in teyit if "7589" not in x]
    if baslangic >= date(2024, 6, 1) or faiz_turu == "sabit":
        teyit = [x for x in teyit if "01.01.2006-01.06.2024" not in x]
    out["teyit_edin"] = teyit
    kodlar = ["K3095"]
    if faiz_turu != "sabit":
        for d in _KANUNI:
            if _gun(d["bas"]) < bitis and d["kaynak"] not in kodlar:
                kodlar.append(d["kaynak"])
        kodlar.append("TCMBTABLO")
    out["kaynaklar"] = [kaynak(k) for k in kodlar]
    out["uyari"] = _F.get("uyari")
    return out


def menu() -> Dict[str, Any]:
    kbas, kson = kapsam()
    ortak = [
        {"ad": "anapara", "tur": "tutar", "etiket": "Anapara (TL)", "zorunlu": True},
        {"ad": "baslangic", "tur": "tarih", "etiket": "Faizin başlangıç tarihi (temerrüt)", "zorunlu": True},
        {"ad": "bitis", "tur": "tarih", "etiket": "Bitiş tarihi (takip ya da ödeme)", "zorunlu": True,
         "aciklama": "Faiz bitiş gününden önceki güne kadar işletilir. Kanuni ve ticari faiz için veri %s ile %s arasıdır."
                     % (_tarih(kbas), _tarih(kson))},
    ]
    return {"surum": hesap._VERI.get("surum"),
            "islemler": [
                {"kod": "kanuni", "ad": "Kanuni faiz (yasal faiz)", "girdiler": ortak},
                {"kod": "ticari", "ad": "Ticari işlerde temerrüt faizi (avans faizi)", "girdiler": ortak},
                {"kod": "sabit", "ad": "Sözleşmede yazılı sabit yıllık oran",
                 "girdiler": ortak + [{"ad": "oran", "tur": "oran", "etiket": "Yıllık faiz oranı (%)", "zorunlu": True}]},
            ],
            "kapsam": {"bas": kbas.isoformat(), "son": kson.isoformat()},
            "kaynaklar": [kaynak(k) for k in ("K7589", "CBK8485", "TCMB20251220")]}


def durum() -> Dict[str, Any]:
    return {"tcmb_teblig": len(_TCMB), "kanuni_donem": len(_KANUNI),
            "kapsam": (_F.get("kapsam") or {}).get("bas"), "kapsam_sonu": (_F.get("kapsam") or {}).get("son")}
