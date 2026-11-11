"""tkgm_tapu — kullanıcının KENDİ e-Devlet / Web Tapu oturumundan aldığı tapu kaydı
metnini yapılandırır. Bu sunucu hiçbir oturuma girmez, belgeyi kendisi çekmez.

Girdi metindir: PDF'i model kendisi okuyup metnini verir ya da kullanıcı yapıştırır.
Burada PDF çözümleyici yazılmadı; stdlib'le yazılan bir PDF metin çıkarıcı Türkçe
harfleri (ToUnicode CMap) sessizce bozar ve bozuk bir malik adı, okunamayan bir
belgeden daha tehlikelidir.

Katkısı şema, maskeleme, çapraz kontrol ve işarettir:
- MASKELEME iki katmandır. (1) Belirlenimci kalıplar: TCKN (NVİ sağlaması doğrulanır,
  ayraçlı yazım dâhil), IBAN (mod-97), etiketli VKN, telefon, e-posta. (2) Malik ADLARI:
  ayrıştırıcı adın hangi dizge olduğunu `Malik:` satırından ZATEN bilir, bu yüzden ad
  tespiti için NLP gerekmez — ad {{MALİK-nn}} olur ve kayıdın HER yerinde, şerh ve rehin
  satırları dâhil, aynı etiketi alır. Eşleştirme döndürülmez, diske yazılmaz.
  Maskelenmeyenler çıktının `kvkk` alanında ADIYLA sayılır: şerhteki üçüncü kişi ve şirket
  adları, adres, doğum tarihi. Tam takma adlandırma ArthurMask'in işidir; bu sunucu
  yalnız standart kütüphane kullandığı için onu içe almaz.
- Paylaşılan sunucuda bu araç hiç çalışmaz (kişisel veri: KVKK).
- Ada/parsel/yüzölçümü, okunmuş parsel geometrisiyle karşılaştırılır.
- Şerh/beyan/rehin satırlarındaki anahtar sözcükler hukuki işarete çevrilir.

Belge düzeni gerçek bir örnekle SABİTLENMEDİ: etiket sözlüğü toleranslıdır ve
çıktı, tanıyamadığı satır sayısını ve düşük güveni açıkça bildirir.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from tkgm_parsel import tr_sayi

_TR = str.maketrans("ıİşŞğĞüÜöÖçÇ", "iisSgGuUoOcC")

# Katlanmış etiket → şema alanı. Sıra önemli: uzun etiket kısadan önce denenir.
_ETIKETLER = [
    ("ana tasinmaz nitelik", "nitelik"), ("tasinmaz nitelik", "nitelik"), ("nitelik", "nitelik"),
    ("mahalle/koy", "mahalle"), ("mahalle koy", "mahalle"), ("mahalle", "mahalle"), ("koy", "mahalle"),
    ("ilce", "ilce"), ("il", "il"), ("mevkii", "mevkii"), ("mevki", "mevkii"),
    ("ada no", "ada"), ("ada", "ada"), ("parsel no", "parsel"), ("parsel", "parsel"),
    ("yuzolcumu", "yuzolcumu_m2"), ("yuzolcum", "yuzolcumu_m2"),
    ("zemin tipi", "zemin_tipi"), ("zemin tip", "zemin_tipi"),
    ("cilt/sayfa", "cilt_sayfa"), ("cilt sayfa no", "cilt_sayfa"), ("cilt no", "cilt"), ("sayfa no", "sayfa"),
    ("blok/kat/giris/bbno", "bagimsiz_bolum"), ("bagimsiz bolum no", "bagimsiz_bolum"),
    ("bagimsiz bolum nitelik", "bb_nitelik"), ("arsa payi", "arsa_payi"), ("arsa pay/payda", "arsa_payi"),
    ("pafta no", "pafta"), ("pafta", "pafta"),
]
_BOLUMLER = [("serh", "serhler"), ("beyan", "beyanlar"), ("irtifak", "irtifaklar"),
             ("rehin", "rehinler"), ("ipotek", "rehinler")]

# Anahtar sözcük → işaret. Adresler başlangıç noktasıdır; `dayanak_koprusu` gibi, metni
# tr_mevzuat_* ile çekmeden alıntılanmaz. 2026-09-22'de resmî metinle (Bedesten) karşılaştırılanlar:
# TMK 194, 735, 736, 794, 881, 1009; Tapu K. m. 26 (beş yıl); TBK 312 (yalnız şerhin
# kararlaştırılabileceğini söyler — yeni malike karşı etki TMK 1009'dadır); 2942 m. 7 (idari şerh,
# altı ay). 6292, 6831, 2863, 6306 ve 634 kanun numaraları yürürlükteki metinlerde bu adlarla geçer.
# İİK işaretinde madde numarası bilerek yok.
_ISARETLER = [
    ("aile konutu", "Aile konutu şerhi: eşin açık rızası olmadan devir/sınırlama (TMK 194)."),
    ("kamulastirma", "Kamulaştırma şerhi (2942 m. 7): idare, şerh tarihinden itibaren altı ay içinde m. 10'a göre "
                     "dava açtığını gösteren mahkeme belgesini tapuya vermezse şerh re'sen silinir — tarihleri "
                     "kontrol edin."),
    ("ihtiyati tedbir", "İhtiyati tedbir: devir engeli olabilir; kararı veren mahkeme ve dosya no teyit edilmeli."),
    ("haciz", "Haciz / ihtiyati haciz: alacaklı, dosya no ve tarih sırası (İİK)."),
    ("ipotek", "İpotek: derece, tutar, alacaklı; fekki satış koşulu olabilir (TMK 881 vd.)."),
    ("intifa", "İntifa hakkı: çıplak mülkiyet devri değeri düşürür (TMK 794 vd.)."),
    ("satis vaadi", "Satış vaadi şerhi: beş yıl içinde satış yapılmazsa re'sen terkin (Tapu K. m. 26)."),
    ("kira", "Kira şerhi: şerh verilmekle sonradan hak kazananlara karşı ileri sürülebilir (TMK 1009; "
             "şerhin kararlaştırılması TBK 312)."),
    ("onalim", "Sözleşmeden doğan önalım şerhi: etkisi şerh tarihinden on yıl sonra sona erer (TMK 735)."),
    ("geri alim", "Geri alım (vefa) şerhi (TMK 736)."),
    ("2/b", "2/B beyanı: 6292 s. K."),
    ("orman", "Orman beyanı/şerhi: 6831 s. K."),
    ("korunmasi gerekli", "Kültür/tabiat varlığı beyanı: 2863 s. K."),
    ("sit alani", "Sit alanı beyanı: 2863 s. K."),
    ("riskli yapi", "Riskli yapı beyanı: 6306 s. K."),
    ("yonetim plani", "Yönetim planı beyanı: 634 s. K."),
]


def _katla(s: str) -> str:
    return re.sub(r"\s+", " ", s.translate(_TR).lower()).strip()


# Belirlenimci kimlik kalıpları. NLP yok: her biri ya kontrol basamağı ya sabit
# uzunluk ya da satır bağlamı taşır. Ayraç toleransı şart — "123 456 789 01" ile
# "12345678901" aynı numaradır; PDF'ten kopyalanan metin NBSP, uzun tire ve satır
# sonunda bölünmüş numara getirir, bunlar önce normalleştirilir.
_BOSLUK = re.compile("[  -​  　]")
_TIRE = re.compile("[‐-―−]")
_AYRAC = r"[ \t.\-]{0,2}(?:\n[ \t]*)?"


def _normallestir(metin: str) -> str:
    metin = metin.replace("\r\n", "\n").replace("\r", "\n")
    return _TIRE.sub("-", _BOSLUK.sub(" ", metin))


def _rakam(s: str) -> str:
    return re.sub(r"\D", "", s)


def _tckn_gecerli(s: str) -> bool:
    """NVİ kontrol basamakları. Doğrulamadan maskelemek yevmiye numarasını da yutar."""
    d = [int(c) for c in s]
    if len(d) != 11 or d[0] == 0:
        return False
    t1 = sum(d[0:9:2]) * 7 - sum(d[1:8:2])
    return t1 % 10 == d[9] and sum(d[:10]) % 10 == d[10]


def _iban_gecerli(s: str) -> bool:
    """mod-97. TR IBAN 26 karakterdir."""
    s = s.upper()
    if not re.fullmatch(r"TR\d{24}", s):
        return False
    tasi = s[4:] + s[:4]
    sayi = "".join(str(ord(c) - 55) if c.isalpha() else c for c in tasi)
    return int(sayi) % 97 == 1


def _satir_sayisini_koru(yeni: str, eski: str) -> str:
    """Satır sonunu yutan eşleşme satır sayısını değiştirmesin: korunan satırlar
    (taşınmaz alanları) sonradan sıra numarasıyla geri konur."""
    return yeni + "\n" * eski.count("\n")


_TEL_ONEK = r"(?:(?:\+|00)90[ \t.\-]*\(?0?|\(0?|0[ \t.\-]*\(?)"
_TEL_GOVDE = r"[ \t]*[2-58]\d{2}[ \t]*\)?[ \t.\-]*\d{3}[ \t.\-]*\d{2}[ \t.\-]*\d{2}(?!\d)"
_DESENLER = (
    ("IBAN", r"(?i)\bTR(?:%s\d{2}){12}\b" % _AYRAC,
     lambda h: _iban_gecerli("TR" + _rakam(h)), lambda h: "TR**...**" + _rakam(h)[-4:]),
    ("TCKN", r"(?<!\d)\d{3}%s\d{3}%s\d{3}%s\d{2}(?!\d)" % (_AYRAC, _AYRAC, _AYRAC),
     lambda h: _tckn_gecerli(_rakam(h)), lambda h: "*" * 9 + _rakam(h)[-2:]),
    # Önekli ya da parantezli numara: cep (5xx) ve sabit hat (2xx-4xx, 850).
    ("TELEFON", r"(?<![\d+])" + _TEL_ONEK + _TEL_GOVDE, lambda h: True, lambda h: "*" * 7 + _rakam(h)[-4:]),
    ("EPOSTA", r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", lambda h: True, lambda h: "***@***"),
)
# Öneksiz on hane yalnız telefon bağlamlı satırda maskelenir: "Yevmiye: 4521987650" telefon değildir.
_TEL_CIPLAK = re.compile(r"(?<![\d+*])[2-58]\d{2}[ \t.\-]*\d{3}[ \t.\-]*\d{2}[ \t.\-]*\d{2}(?!\d)")
_TEL_BAGLAM = re.compile(r"\b(tel|telefon|gsm|cep|faks|fax|iletisim)\b")

# VKN'de sağlama basamağı kullanılmıyor: GİB algoritması burada doğrulanamadı ve
# doğrulanmamış bir sağlama iki yönden de yanlış yapar. Onun yerine SATIR BAĞLAMI
# aranır — etiketin kendi satırında ve altındaki satırda.
_VKN_BAGLAM = re.compile(r"vergi\s*(kimlik|dairesi|no|numaras)|(?<![a-z])v\.?\s*k\.?\s*n(?![a-z])")
_VKN_SAYI = re.compile(r"(?<!\d)\d{10}(?!\d)")
# T.C./kimlik etiketli satırda sağlaması tutmayan 11 hane de maskelenir (OCR/yazım hatası);
# etiketsiz satırda tutmayan 11 hane (yevmiye) bilerek bırakılır.
_TC_BAGLAM = re.compile(r"(?<![a-z])t\.?\s?c\.?(?![a-z])|kimlik")
_ON_BIR_HANE = re.compile(r"(?<!\d)\d(?:[ .\-]?\d){10}(?!\d)")


def _maskele(metin: str) -> str:
    """Kimlik numaralarını, IBAN'ı, telefonu ve e-postayı yıldızlar. Adlar `_adlari_maskele`de."""
    metin = _normallestir(metin)
    for _, desen, gecerli, degistir in _DESENLER:
        metin = re.sub(desen, lambda m: _satir_sayisini_koru(degistir(m.group(0)), m.group(0))
                       if gecerli(m.group(0)) else m.group(0), metin)
    satirlar = metin.split("\n")
    vkn_onceki = False
    for i, satir in enumerate(satirlar):
        katli = _katla(satir)
        vkn = bool(_VKN_BAGLAM.search(katli))
        if vkn or vkn_onceki:
            satir = _VKN_SAYI.sub(lambda m: "*" * 8 + m.group(0)[-2:], satir)
        if _TC_BAGLAM.search(katli):
            satir = _ON_BIR_HANE.sub(lambda m: "*" * 9 + _rakam(m.group(0))[-2:], satir)
        if _TEL_BAGLAM.search(katli):
            satir = _TEL_CIPLAK.sub(lambda m: "*" * 7 + _rakam(m.group(0))[-4:], satir)
        satirlar[i] = satir
        vkn_onceki = vkn
    return "\n".join(satirlar)


# ------------------------------------------------------------------ adlar
# Harf sınıfları: aynı ad belgede BÜYÜK, Başlık ve ASCII'ye indirilmiş ("INCE") yazılır;
# str.title()/upper() Türkçe İ/ı'yı bozar, bu yüzden eşleşme harf sınıfıyla kurulur.
_SINIF: Dict[str, str] = {}
for _grup in ("iıİI", "şŞsS", "ğĞgG", "üÜuU", "öÖoO", "çÇcC"):
    for _c in _grup:
        _SINIF[_c] = "[%s]" % _grup
_TUZEL = (" hazine", "belediye", "bakanlig", "mudurlugu", "idaresi", " vakfi", "kooperatif", " a.s",
          " a. s", " as ", " ltd", " sti", "bankasi", " toki ", "universitesi", "dernegi", "sirketi",
          "baskanligi", "valiligi", "kurumu", "odasi")


def _harf(c: str) -> str:
    if c in _SINIF:
        return _SINIF[c]
    if c.isalpha():
        return "[%s%s]" % (re.escape(c.lower()), re.escape(c.upper()))
    return re.escape(c)


def _ad_deseni(sozcukler: List[str]) -> "re.Pattern[str]":
    """Sözcükler arasında boşluk, çift boşluk ya da SATIR SONU; harfle bitişik eşleşme yok."""
    govde = r"[\s.]{1,3}".join("".join(_harf(c) for c in w) for w in sozcukler)
    return re.compile(r"(?<![^\W\d_])" + govde + r"(?![^\W\d_])")


def _ad_cekirdegi(deger: str) -> List[str]:
    """Malik değerinden ad-soyad sözcükleri. TAKBİS "(SN:48213377) MEHMET YILMAZ : HASAN",
    "HASAN oğlu MEHMET YILMAZ", "ZEYNEP KAYA, HASAN Kızı", "<tckn> - AD SOYAD" yazar."""
    s = re.sub(r"[(\[][^)\]]*[)\]]?", " ", deger)
    s = re.split(r"(?i)\bhisse\b|\bpay\s*/", s)[0]
    s = re.sub(r"\*{3,}\d*|\d+", " ", s)
    s = re.sub(r"(?i)\bT\.\s*C\.|\bTC\b|\bkimlik\s*no\b", " ", s)
    m = re.match(r"(?i)^\W*[^\W\d_]+\s+(?:o[gğ]lu|k[ıi]z[ıi])\s+(.+)$", s)
    if m:
        s = m.group(1)
    for parca in re.split(r"\s*[:,;/]\s*|\s+-\s+|^\s*-\s*", s):
        sozcukler = [w for w in re.findall(r"[^\W\d_]+", parca or "")
                     if _katla(w) not in ("oglu", "kizi")]
        if sum(len(w) for w in sozcukler) >= 3:
            return sozcukler
    return []


def _tuzel_mi(sozcukler: List[str]) -> bool:
    k = " " + _katla(" ".join(sozcukler)) + " "
    return any(t in k for t in _TUZEL)


def _adlari_maskele(metin: str, degerler: List[str], korunan: set) -> Tuple[str, Dict[str, Any]]:
    """Malik adlarını METNİN TAMAMINDA {{MALİK-nn}}'e çevirir; korunan satırlar (taşınmaz
    alanları: "Mahalle: Yunus Emre") geri konur. Tüzel kişi/kamu malikleri kişisel veri
    değildir ve hukuken anlamlıdır (Hazine, belediye); maskelenmez."""
    kisiler: List[Dict[str, Any]] = []
    tuzel = cozulemeyen = 0
    for deger in degerler:
        s = _ad_cekirdegi(deger)
        if not s:
            cozulemeyen += 1
            continue
        if _tuzel_mi(s):
            tuzel += 1
            continue
        anahtar = _katla(" ".join(s))
        if all(k["anahtar"] != anahtar for k in kisiler):
            kisiler.append({"anahtar": anahtar, "sozcukler": s, "etiket": "{{MALİK-%02d}}" % (len(kisiler) + 1)})

    eski_satirlar = metin.split("\n")
    for k in sorted(kisiler, key=lambda k: (-len(k["sozcukler"]), -len(k["anahtar"]))):
        metin = _ad_deseni(k["sozcukler"]).sub(lambda m, e=k["etiket"]: _satir_sayisini_koru(e, m.group(0)), metin)
    # Yalnız soyadıyla anma ("YILMAZ mirasçıları"): BÜYÜK ya da Başlık yazımı maskelenir,
    # küçük harf ("demir", "kaya") sözcük olabilir. Soyadı paylaşılıyorsa etiket belirsizdir.
    soyadlari = [_katla(k["sozcukler"][-1]) for k in kisiler if len(k["sozcukler"]) >= 2]
    for k in kisiler:
        soyad = k["sozcukler"][-1]
        if len(k["sozcukler"]) >= 2 and len(soyad) >= 3 and soyadlari.count(_katla(soyad)) == 1:
            metin = _ad_deseni([soyad]).sub(
                lambda m, e=k["etiket"]: m.group(0) if not any(ch.isupper() for ch in m.group(0)) else e, metin)
    satirlar = metin.split("\n")
    for i in korunan:
        if i < len(satirlar) and i < len(eski_satirlar):
            satirlar[i] = eski_satirlar[i]
    metin = "\n".join(satirlar)

    # Sonradan denetim: maskelenmemiş ad sözcüğü kaldı mı? Sözcüğün kendisi yazılmaz.
    denetim = _katla("\n".join(s for i, s in enumerate(satirlar) if i not in korunan))
    kalan = sorted({k["etiket"] for k in kisiler for w in k["sozcukler"]
                    if len(w) >= 3 and re.search(r"(?<![a-z])%s(?![a-z])" % re.escape(_katla(w)), denetim)})
    return metin, {"kisi": len(kisiler), "tuzel": tuzel, "cozulemeyen": cozulemeyen, "kalan": kalan}


# ArthurMask kancası: kuruluysa şerh/beyan/irtifak/rehin satırlarındaki ÜÇÜNCÜ kişi ve kurum
# adlarını da maskeler (alacaklı banka, kiracı, mahkeme). Bu sunucu standart kütüphaneyle
# çalışmaya devam eder: içe aktarma tembeldir, yoksa kvkk bunu açıkça yazar. Kasa kullanılmaz
# (diske eşleştirme yazılmaz); yalnız motorun bulguları okunur. TKGM_ARTHUR_MASK=0 kapatır.
_MOTOR: Any = None
_MOTOR_HATA = ""
_KISI_TURLERI = ("PERSON", "KISI", "KİŞİ", "AD")
_KURUM_TURLERI = ("ORGANIZATION", "ORG", "KURUM", "SIRKET", "ŞİRKET")


def _arthur_mask() -> Any:
    global _MOTOR, _MOTOR_HATA
    if os.environ.get("TKGM_ARTHUR_MASK", "").strip() == "0":
        _MOTOR_HATA = "TKGM_ARTHUR_MASK=0 ile kapalı"
        return None
    if _MOTOR is None:
        try:
            from arthur_mask.motor import MaskeMotoru
            _MOTOR = MaskeMotoru()
        except Exception as exc:  # noqa: BLE001 - kurulu değil ya da bağımlılığı eksik
            _MOTOR, _MOTOR_HATA = False, "%s: %s" % (type(exc).__name__, exc)
    return _MOTOR or None


def _ucuncu_kisileri_maskele(bolumler: Dict[str, List[str]], motor: Any) -> int:
    etiketler: Dict[str, str] = {}
    sayac = {"KİŞİ": 0, "KURUM": 0}
    for ad, satirlar in bolumler.items():
        for i, satir in enumerate(satirlar):
            bulgular = motor.analiz(satir)[0]
            for b in sorted(bulgular, key=lambda b: -b.bas):
                parca = satir[b.bas:b.son]
                if "{{" in parca or "}}" in parca:
                    continue
                varlik = str(getattr(b, "varlik", "")).upper()
                tur = "KURUM" if any(t in varlik for t in _KURUM_TURLERI) else \
                      "KİŞİ" if any(t in varlik for t in _KISI_TURLERI) else None
                if tur is None:
                    continue
                anahtar = tur + ":" + _katla(getattr(b, "kanonik", "") or parca)
                if anahtar not in etiketler:
                    sayac[tur] += 1
                    etiketler[anahtar] = "{{%s-%02d}}" % (tur, sayac[tur])
                satir = satir[:b.bas] + etiketler[anahtar] + satir[b.son:]
            satirlar[i] = satir
    return len(etiketler)


class TapuHatasi(ValueError):
    """Tapu metni güvenle işlenemedi (ör. malik satırı tanınmadı ve adlar maskelenemez)."""


_MALIK_ETIKETLERI = {"malik", "maliki", "malik adi", "malik adi soyadi", "malik ad soyad", "malik ad soyadi",
                     "ad soyad", "adi soyadi", "ad soyadi", "hissedar", "hissedar adi soyadi",
                     "mulkiyet sahibi", "mal sahibi", "malik/hissedar"}


def _malik_etiketi_mi(etiket_k: str, aktif: Optional[str]) -> bool:
    if etiket_k.startswith("hak sahibi"):
        return aktif is None             # irtifak bölümündeki "Hak Sahibi: TEİAŞ" malik değildir
    if etiket_k in _MALIK_ETIKETLERI:
        return True
    return etiket_k.startswith("malik ") and not etiket_k.startswith(("malik tip", "malik say", "malik tur"))


def _ayristir_ham(metin: str) -> Dict[str, Any]:
    tasinmaz: Dict[str, Any] = {}
    malikler: List[Dict[str, str]] = []
    bolumler: Dict[str, List[str]] = {ad: [] for _, ad in _BOLUMLER}
    aktif: Optional[str] = None
    taninmayan = 0
    korunan: set = set()
    bekleyen_malik = False

    for sira, ham in enumerate(metin.split("\n")):
        satir = ham.strip()
        if not satir:
            continue
        katli = _katla(satir)
        if bekleyen_malik:                 # "Malik:" etiketi tek başına, değer alt satırda
            malikler.append({"ad": satir})
            bekleyen_malik = False
            continue

        # Bölüm başlığı: kısa, değer taşımayan, anahtar sözcükle başlayan satır.
        baslik = next((ad for anahtar, ad in _BOLUMLER
                       if katli.startswith(anahtar) and len(katli) < 40 and ":" not in satir), None)
        if baslik:
            aktif = baslik
            continue

        if re.match(r"^[^:]{2,40}?\s*[:：]\s*$", satir) and \
                _malik_etiketi_mi(_katla(satir.rstrip(":： ")), aktif):
            bekleyen_malik = True
            continue
        m = re.match(r"^([^:]{2,40}?)\s*[:：]\s*(.+)$", satir)
        if m:
            etiket_k, deger = _katla(m.group(1)), m.group(2).strip()
            if _malik_etiketi_mi(etiket_k, aktif):
                malikler.append({"ad": deger})
                aktif = None
                continue
            if etiket_k.startswith("hak sahibi") and aktif:
                bolumler[aktif].append(satir)
                continue
            if etiket_k in ("hisse", "hisse pay/payda", "pay/payda", "hisse orani") and malikler:
                malikler[-1]["hisse"] = deger
                continue
            if etiket_k in ("edinme sebebi", "edinme sebep", "iktisap sebebi") and malikler:
                malikler[-1]["edinme_sebebi"] = deger
                continue
            if etiket_k in ("tarih-yevmiye", "tarih/yevmiye", "tarih yevmiye", "yevmiye") and malikler:
                malikler[-1]["tarih_yevmiye"] = deger
                continue
            alan = next((a for e, a in _ETIKETLER if etiket_k == e), None)
            if alan:
                tasinmaz.setdefault(alan, tr_sayi(deger) if alan == "yuzolcumu_m2" else deger)
                korunan.add(sira)
                aktif = None
                continue
        if aktif:
            bolumler[aktif].append(satir)
        else:
            taninmayan += 1
    return {"tasinmaz": tasinmaz, "malikler": malikler, "bolumler": bolumler,
            "taninmayan": taninmayan, "korunan": korunan}


def ayristir(metin: str, maskele: bool = True, ad_maskele: bool = True,
             ucuncu_kisi: bool = True) -> Dict[str, Any]:
    metin = _maskele(metin) if maskele else _normallestir(metin)
    ham = _ayristir_ham(metin)
    ad = None
    if ad_maskele:
        if not ham["malikler"] and re.search(r"malik|hissedar|mulkiyet|mal sahibi", _katla(metin)):
            # Kapalı başarısızlık: adların nerede olduğu bilinmiyorsa metin modele gitmez.
            raise TapuHatasi("Malik satırı tanınmadı; adlar maskelenemeyeceği için metin işlenmedi. "
                             "Malik bilgisini 'Malik: AD SOYAD' biçiminde bir satıra yazın ya da adların "
                             "modele gideceğini kabul ederek ad_maskele=false ile çalıştırın.")
        metin, ad = _adlari_maskele(metin, [m["ad"] for m in ham["malikler"]], ham["korunan"])
        ham = _ayristir_ham(metin)
        for m in ham["malikler"]:
            e = re.search(r"\{\{MALİK-\d\d\}\}", m["ad"])
            if e:
                m["ad"] = e.group(0)       # (SN:…), baba adı ve maskeli TCKN kuyruğu da gider
            elif not _tuzel_mi(_ad_cekirdegi(m["ad"]) or [m["ad"]]):
                m["ad"] = "{{MALİK-??}}"

    bolumler = ham["bolumler"]
    katli_kisit = _katla(" ".join(s for liste in bolumler.values() for s in liste))
    motor = _arthur_mask() if ucuncu_kisi else None
    ucuncu = _ucuncu_kisileri_maskele(bolumler, motor) if motor else None
    kayit: Dict[str, Any] = {
        "tasinmaz": ham["tasinmaz"], "malikler": ham["malikler"], **bolumler,
        "isaretler": [not_ for anahtar, not_ in _ISARETLER if anahtar in katli_kisit],
        "taninmayan_satir": ham["taninmayan"],
        "guven": "düşük — belge düzeni gerçek bir örnekle sabitlenmedi; her alanı belgeyle karşılaştırın",
    }
    # Ne maskelendiği kadar ne MASKELENMEDİĞİ de yazılır: "maskelendi" diye okunan
    # bir alan, altında ne olduğu söylenmezse olmayan bir güvence verir.
    maskelenen = (["TCKN (NVİ sağlamalı; T.C. etiketli satırda sağlamasız da)", "IBAN", "VKN (etiketli)",
                   "telefon (cep ve sabit hat)", "e-posta"] if maskele else [])
    if ucuncu is not None:
        maskelenen.append("şerh/beyan/rehin satırlarındaki üçüncü kişi ve kurum adları → {{KİŞİ-nn}}/"
                          "{{KURUM-nn}} (ArthurMask, %d ad)" % ucuncu)
    maskelenmeyen = [
        None if maskele else "kimlik numaraları (maskele=false)",
        None if ad_maskele else "malik adları (ad_maskele=false)",
        ("ArthurMask bir NLP tahminidir: tanımadığı üçüncü kişi adı açık kalabilir; dosya no ve "
         "tarih maskelenmez") if ucuncu is not None else
        ("şerh/beyan/rehin satırlarındaki ÜÇÜNCÜ kişi ve şirket adları (alacaklı banka, kiracı, "
         "mahkeme ve dosya no) — bunlar ayrıştırıcının ad olarak bilmediği dizgelerdir; ArthurMask "
         "%s" % ("kapalı (ucuncu_kisi_maskele=false)" if not ucuncu_kisi else
                 "devrede değil (%s)" % (_MOTOR_HATA or "içe aktarılamadı"))),
        "adres, doğum tarihi",
    ]
    if ad:
        if ad["kisi"]:
            maskelenen.append("malik adı → {{MALİK-nn}} (%d kişi)" % ad["kisi"])
        if ad["tuzel"]:
            maskelenmeyen.append("tüzel kişi/kamu malik adları (%d) — kişisel veri değildir" % ad["tuzel"])
        if ad["cozulemeyen"]:
            maskelenmeyen.append("%d malik değeri ad olarak çözümlenemedi; metnin geri kalanında o ad "
                                 "AÇIK olabilir" % ad["cozulemeyen"])
        if ad["kalan"]:
            maskelenmeyen.append("OLASI AD SIZINTISI: %s adının bir sözcüğü metinde tek başına geçiyor "
                                 "(küçük harf ya da yalnız ad) — belgeyle karşılaştırın" % ", ".join(ad["kalan"]))
        if not ham["malikler"]:
            maskelenmeyen.append("metinde malik satırı yok: ad maskesi uygulanacak ad bulunamadı")
    kayit["kvkk"] = {
        "maskelenen": maskelenen,
        "maskelenmeyen": [x for x in maskelenmeyen if x],
        "saklama": "Eşleştirme döndürülmez ve diske yazılmaz; hiçbir şey bellekte tutulmaz.",
        "daha_fazlasi": "ArthurMask kuruluysa üçüncü kişi adları da maskelenir (isteğe bağlı kanca; "
                        "bu sunucu onsuz da standart kütüphaneyle çalışır). Adres ve belge bütünü için "
                        "tam takma adlandırma ArthurMask arayüzünün işidir.",
    }
    return kayit


def capraz_kontrol(kayit: Dict[str, Any], parsel: Dict[str, Any], hesap_alani: float) -> List[str]:
    """Tapu kaydı ile okunmuş parsel dosyası aynı taşınmazı mı anlatıyor?"""
    t, oz = kayit["tasinmaz"], parsel["oznitelik"]
    out: List[str] = []
    # Tutan alanlar da sayılır: yalnız uyuşmazlığı yazmak, hiç karşılaştırılmamış bir
    # alanla eşleşmiş bir alanı aynı sessizlikte bırakır — ikisi çok farklı şeylerdir.
    tutan: List[str] = []
    for alan in ("ada", "parsel"):
        if t.get(alan) and oz.get(alan):
            if str(t[alan]).strip() != str(oz[alan]).strip():
                out.append("%s UYUŞMUYOR: tapu kaydı %s, parsel dosyası %s." % (alan, t[alan], oz[alan]))
            else:
                tutan.append("%s %s" % (alan, t[alan]))
    for alan in ("il", "ilce", "mahalle"):
        if t.get(alan) and oz.get(alan):
            if _katla(str(t[alan])) not in _katla(str(oz[alan])) \
                    and _katla(str(oz[alan])) not in _katla(str(t[alan])):
                out.append("%s UYUŞMUYOR: tapu kaydı %s, parsel dosyası %s." % (alan, t[alan], oz[alan]))
            else:
                tutan.append("%s %s" % (alan, t[alan]))
    if tutan:
        out.append("Tutan alanlar: %s." % ", ".join(tutan))
    yuz = t.get("yuzolcumu_m2")
    if yuz:
        fark = hesap_alani - yuz
        out.append("Tapu yüzölçümü %.2f m², geometriden hesap alanı %.2f m² (fark %+.2f m², %%%.2f). "
                   "Fark bir işarettir; düzeltme 3402 s. K. m. 41 yoluna ve kadastro kayıtlarına tabidir."
                   % (yuz, hesap_alani, fark, abs(fark) / yuz * 100.0))
    if not out:
        out.append("Karşılaştırılabilir alan bulunamadı: ada, parsel, il/ilçe/mahalle ve yüzölçümünün "
                   "hiçbiri tapu metninde tanınmadı. Belge düzeni beklenenden farklı olabilir.")
    elif not yuz:
        out.append("Yüzölçümü tapu metninde tanınmadı; alan karşılaştırması YAPILMADI.")
    return out
