"""F-196 regresyonu: tiresiz madde başlığı ve "8." biçimli fıkra (ağsız).

Olay: tr_mevzuat_madde_getir TBK m.428'i getiremiyordu; Bedesten metninde başlık
"MADDE 428 İşyerinin…" diye TİRESİZ geliyor, katı desen tire istiyordu. Tam metin
modunda m.427 istenince 428'in metni de 427'ye ekleniyordu. Aynı gün İYUK m.45/8
"(8) numaralı fıkra yok" dedi: İYUK fıkraları "8." diye numaralar. Metinler canlı
Bedesten yanıtlarından kısaltıldı (26.10.2026).
"""

from __future__ import annotations

import base64
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sources import bedesten_mevzuat as mv  # noqa: E402


def _html(*paragraflar: str) -> dict:
    h = "".join("<p>%s</p>" % p for p in paragraflar)
    return {"content": base64.b64encode(h.encode("utf-8")).decode("ascii"), "mimeType": "text/html"}


def _kayit(mid: str, no: int, ad: str, rg_sayi: str) -> dict:
    return {"mevzuatList": [{
        "mevzuatId": mid, "mevzuatNo": no, "mevzuatAdi": ad,
        "mevzuatTur": {"id": 1, "name": "KANUN", "description": "Kanunlar"},
        "mevzuatTertip": 5, "resmiGazeteTarihi": "2011-02-04T00:00:00", "resmiGazeteSayisi": rg_sayi,
        "url": "https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=%d&MevzuatTur=1&MevzuatTertip=5" % no,
        "mukerrer": "HAYIR", "ekler": None}], "total": 1, "start": 0}


def _agac(mid: str, no: int, ad: str, maddeler: list) -> dict:
    cocuklar = [{"maddeId": aid, "maddeNo": n, "title": t, "description": None,
                 "maddeBaslik": "Madde No: %d - %s" % (n, t), "gerekceId": None,
                 "parentMaddeId": "1", "mevzuatId": mid} for aid, n, t in maddeler]
    return {"maddeId": "1", "maddeNo": None, "title": str(no), "description": ad,
            "maddeBaslik": "%d - %s" % (no, ad), "gerekceId": None, "parentMaddeId": "-1",
            "mevzuatId": mid, "children": cocuklar}


M426 = ("MADDE 426- İşveren, işçinin isteği üzerine her zaman, işin türünü ve süresini gösteren "
        "bir hizmet belgesi vermekle yükümlüdür.")
M427 = ("MADDE 427- Hizmet buluşları üzerinde işçinin ve işverenin hakları, bunların kazanılması "
        "ile diğer sınaî ve fikrî mülkiyet hakları konusunda özel kanun hükümleri uygulanır.")
M428 = ("MADDE 428 İşyerinin tamamı veya bir bölümü hukuki bir işlemle başkasına devredildiğinde, "
        "devir tarihinde işyerinde veya bir bölümünde mevcut olan hizmet sözleşmeleri, bütün hak ve "
        "borçları ile birlikte devralana geçer.")
M428_2 = ("İşçinin hizmet süresine bağlı hakları bakımından, onun devreden işveren yanında işe "
          "başladığı tarih esas alınır.")
M429 = ("MADDE 429- Hizmet sözleşmesi, ancak işçinin yazılı rızası alınmak suretiyle, sürekli "
        "olarak başka bir işverene devredilebilir.")
M430 = ("MADDE 430- Belirli süreli hizmet sözleşmesi, aksi kararlaştırılmadıkça, fesih bildiriminde "
        "bulunulmasına gerek olmaksızın, sürenin bitiminde kendiliğinden sona erer.")


def _tbk_tam(m428: str = M428) -> dict:
    return _html("VII. HİZMET BELGESİ", M426, "E. SINAÎ VE FİKRÎ MÜLKİYET HAKKI", M427,
                 "F. HİZMET İLİŞKİSİNİN DEVRİ", "I. İŞYERİNİN TAMAMININ VEYA BİR BÖLÜMÜNÜN DEVRİ",
                 m428, M428_2, "II. SÖZLEŞMENİN DEVRİ", M429, "G. SÖZLEŞMENİN SONA ERMESİ",
                 "I. BELİRLİ SÜRELİ SÖZLEŞMEDE", M430)


IYUK45 = _html(
    "İstinaf:[30]", "Madde 45 – (Değişik: 18/6/2014-6545/19 md.)",
    "1. İdare ve vergi mahkemelerinin kararlarına karşı bölge idare mahkemesine, kararın tebliğinden "
    "itibaren otuz gün içinde istinaf yoluna başvurulabilir.",
    "2. İstinaf, temyizin şekil ve usullerine tabidir.",
    "3. Bölge idare mahkemesi, ilk derece mahkemesinin kararına ilişkin yaptığı inceleme sonunda;",
    "a) Kararı hukuka uygun bulursa,", "istinaf başvurusunun reddine karar verir.",
    "7. İstinaf başvurusuna konu edilen kararı veren ya da karara katılan hâkim, aynı davanın istinaf "
    "yoluyla bölge idare mahkemesince incelenmesinde bulunamaz.",
    "8. İvedi yargılama usulüne tabi olan davalarda istinaf yoluna başvurulamaz.")


def _sahte_bedesten(tbk_tam: dict = None, tbk_428_dugum: dict = None):
    fx = {
        ("6098", "search"): _kayit("103273", 6098, "TÜRK BORÇLAR KANUNU", "27836"),
        ("103273", "tree"): _agac("103273", 6098, "TÜRK BORÇLAR KANUNU", [
            ("1661540", 426, "Hizmet belgesi"), ("1661545", 427, "Sınaî ve fikrî mülkiyet hakkı"),
            ("1661548", 428, "İşyerinin devri"), ("1661551", 429, "Sözleşmenin devri"),
            ("1661554", 430, "Belirli süreli sözleşmede")]),
        "madde_1661540": _html(M426, "E. SINAÎ VE FİKRÎ MÜLKİYET HAKKI"),
        "madde_1661545": _html(M427, "F. HİZMET İLİŞKİSİNİN DEVRİ",
                               "I. İŞYERİNİN TAMAMININ VEYA BİR BÖLÜMÜNÜN DEVRİ"),
        "madde_1661548": tbk_428_dugum or _html(M428, M428_2, "II. SÖZLEŞMENİN DEVRİ"),
        "madde_1661551": _html(M429, "G. SÖZLEŞMENİN SONA ERMESİ"),
        "madde_1661554": _html(M430),
        "mevzuat_103273": tbk_tam or _tbk_tam(),
        ("2577", "search"): _kayit("103161", 2577, "İDARİ YARGILAMA USULÜ KANUNU", "17580"),
        ("103161", "tree"): _agac("103161", 2577, "İDARİ YARGILAMA USULÜ KANUNU",
                                  [("1650045", 45, "İstinaf")]),
        "madde_1650045": IYUK45,
    }
    istekler = []

    def call(path, data, paging=False):
        istekler.append((path, dict(data)))
        if path == "/searchDocuments":
            r = fx.get((str(data.get("mevzuatNo")), "search"))
            if r:
                return r
        if path == "/mevzuatMaddeTree":
            r = fx.get((str(data.get("mevzuatId")), "tree"))
            if r:
                return r
        if path == "/getDocumentContent":
            anahtar = "%s_%s" % (data.get("documentType", "").lower(), data.get("id"))
            if anahtar in fx:
                return fx[anahtar]
        raise mv.HttpError(404, "sahte" + path, b"Record not found")

    mv._CACHE.clear()
    mv._call = call
    return istekler


def _kalemler(r):
    return {it["requested"]: it for it in r["items"]}


def test_tiresiz_428_dugumden_gelir_komsulara_karismaz():
    _sahte_bedesten()
    k = _kalemler(mv.article({"number": "6098", "madde_no": ["427", "428", "429"]}))
    assert all(it["ok"] for it in k.values()), k
    assert k["428"]["text"].startswith("MADDE 428 İşyerinin")
    assert "devralana geçer" in k["428"]["text"] and "MADDE 429" not in k["428"]["text"]
    assert k["428"]["citation"].endswith("m. 428")
    assert "İşyerinin tamamı" not in k["427"]["text"]
    assert k["429"]["text"].startswith("MADDE 429-")


def test_tam_metin_modunda_427_428in_metnini_almaz():
    _sahte_bedesten()
    # 4 soğuk düğüm → tam metin modu (TAM_METIN_ESIGI)
    k = _kalemler(mv.article({"number": "6098", "madde_no": ["426", "427", "428", "429"]}))
    assert all(it["ok"] and it["source"] == "full_text" for it in k.values()), k
    assert "İşyerinin tamamı" not in k["427"]["text"]
    assert k["428"]["text"].startswith("MADDE 428 İşyerinin") and "esas alınır" in k["428"]["text"]
    assert "Hizmet sözleşmesi, ancak" not in k["428"]["text"]
    assert "SÖZLEŞMENİN DEVRİ" not in k["428"]["text"]      # 429'un kenar başlığı 429'a taşınır


def test_tam_metinde_bulunamayan_madde_dugumden_gelir():
    # Tam metinde 428 başlığı okunamaz (küçük harfli devam) ama düğüm metninin ilk satırı tiresiz başlık.
    bozuk = _tbk_tam(m428=M428.replace("MADDE 428 İşyerinin", "MADDE 428 işyerinin"))
    _sahte_bedesten(tbk_tam=bozuk)
    k = _kalemler(mv.article({"number": "6098", "madde_no": ["426", "427", "428", "429"]}))
    assert k["428"]["ok"] is True, k["428"]
    assert k["428"]["source"] == "madde_node"
    assert k["428"]["text"].startswith("MADDE 428 İşyerinin")


def test_satir_basindaki_atif_ve_sirasiz_numara_baslik_sayilmaz():
    metin = ("MADDE 11- Kurul kararları kesindir.\n\n"
             "Madde 12 uyarınca verilen kararlar da bu hükme tabidir.\n\n"
             "MADDE 12 uyarınca verilen kararlar ayrıca ilan edilir.\n\n"
             "MADDE 5 Kurul, bu kararları yayımlar.\n\n"
             "MADDE 13- Bu Kanun yayımı tarihinde yürürlüğe girer.")
    k = mv._kaynak_coz(metin)
    assert [b["no"] for b in k["basliklar"]] == [11, 13]
    assert "Madde 12 uyarınca" in metin[k["basliklar"][0]["bas"]:k["basliklar"][0]["son"]]


def test_tek_maddelik_dugumde_tiresiz_baslik_yalniz_ilk_satirda():
    assert [b["no"] for b in mv._kaynak_coz(M428 + "\n\n" + M428_2)["basliklar"]] == [428]
    assert mv._kaynak_coz("Kenar başlığı değil bir cümle.\n\n" + M428)["basliklar"] == []


def test_yumusak_tireli_baslik_tanir():
    k = mv._kaynak_coz("MADDE 463- Bir.\n\nMADDE 464­- İki.\n\nMADDE 465- Üç.")
    assert [b["no"] for b in k["basliklar"]] == [463, 464, 465]
    assert k["basliklar"][1]["etiket"] == "MADDE 464-"


def test_noktali_fikra_45_8():
    _sahte_bedesten()
    it = mv.article({"number": "2577", "madde_no": "45/8"})["items"][0]
    assert it["ok"] is True and not it.get("paragraph_not_found"), it
    assert it["paragraph_text"] == "8. İvedi yargılama usulüne tabi olan davalarda istinaf yoluna başvurulamaz."
    assert it["citation"].endswith("m. 45/8")
    it7 = mv.article({"number": "2577", "madde_no": "45/7"})["items"][0]
    assert it7["paragraph_text"].startswith("7. İstinaf başvurusuna") and "İvedi" not in it7["paragraph_text"]
    it3 = mv.article({"number": "2577", "madde_no": "45/3"})["items"][0]
    assert "a) Kararı hukuka uygun bulursa" in it3["paragraph_text"]
    it9 = mv.article({"number": "2577", "madde_no": "45/9"})["items"][0]
    assert it9["paragraph_not_found"] is True and "'9.'" in it9["paragraph_note"]


def test_noktali_fikra_baslik_satirinda_baslayabilir_bent_listesi_fikra_sayilmaz():
    satirda = "Madde 1 – 1. Danıştay görevlidir.\n\n2. Bölge idare mahkemeleri de görevlidir."
    assert mv._fikra(satirda, 1) == "1. Danıştay görevlidir."
    assert mv._fikra(satirda, 2) == "2. Bölge idare mahkemeleri de görevlidir."
    liste = "Madde 13 – Mücbir sebepler:\n\n1. Ağır kaza;\n\n2. Yangın;\n\n3. Kaçınılmaz olaylar."
    assert mv._fikra(liste, 2) is None
    parantez = "MADDE 5- (1) Birinci fıkra.\n\n(2) İkinci fıkra:\n\n1. bir\n\n2. iki"
    assert mv._fikra(parantez, 2).startswith("(2) İkinci fıkra")


if __name__ == "__main__":
    ok = 0
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
            ok += 1
            print("ok   ", t.__name__)
        except Exception as e:  # noqa: BLE001
            print("FAIL ", t.__name__, "-", repr(e))
    print("%d/%d passed" % (ok, len(tests)))
    sys.exit(0 if ok == len(tests) else 1)
