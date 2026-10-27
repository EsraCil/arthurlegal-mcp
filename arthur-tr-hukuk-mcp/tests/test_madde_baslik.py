"""Kademeli kenar başlıkları (TBK, TMK): başlık, başlık yolu ve madde sınırı (ağsız).

Olay (2026-10-27): TBK m.344 tek başına istenince başlıksız, listeyle istenince "II. BELİRLENMESİ2"
döndü. Üç kusurdu: (1) başlık, maddenin nasıl istendiğine bağlıydı (ağaçta TBK'nın hiçbir maddesinin
başlığı yok, tam metne yalnız liste yolu bakıyordu); (2) yapışık dipnot rakamı ("…MESİ2") silinmiyordu;
(3) kat kat başlıklardan ("E. KİRA BEDELİ" / "II. BELİRLENMESİ") yalnız son satır alınıyor, üst satır
önceki maddenin metnine kalıyordu (TMK m.23'ün metni "II. Saldırıya karşı / 1. İlke" ile bitiyordu).
Satırlar canlı Bedesten metninden alındı.

    python -m pytest tests/test_madde_baslik.py
"""

from __future__ import annotations

import base64
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sources import bedesten_mevzuat as mv  # noqa: E402


def _coz(metin: str) -> dict:
    return {b["no"]: b for b in mv._kaynak_coz(metin)["basliklar"]}


def _govde(metin: str, b: dict) -> str:
    return metin[b["bas"]:b["son"]]


KIRA = (
    "MADDE 342- Kiracı güvence vermekle yükümlü ise bu güvence üç aylık kira bedelini aşamaz.\n\n"
    "Kiraya veren, kira sözleşmesinin sona ermesini izleyen üç ay içinde güvenceyi geri vermekle yükümlüdür.\n\n"
    "E. KİRA BEDELİ\n\n"
    "I. GENEL OLARAK2\n\n"
    "MADDE 343- Kira sözleşmelerinde kira bedelinin belirlenmesi dışında, kiracı aleyhine değişiklik yapılamaz.\n\n"
    "II. BELİRLENMESİ2[3][4]\n\n"
    "MADDE 344 - Tarafların yenilenen kira dönemlerinde uygulanacak kira bedeline ilişkin anlaşmaları geçerlidir.\n\n"
    "III. DAVA AÇMA SÜRESİ VE KARARIN ETKİSİ\n\n"
    "MADDE 345- Kira bedelinin belirlenmesine ilişkin dava, her zaman açılabilir.\n"
)


def test_yapisik_ve_koseli_dipnot_basliktan_ayrilir() -> None:
    assert mv._baslik_temiz("II. BELİRLENMESİ2[3][4]") == ("II. BELİRLENMESİ", ["2", "3", "4"])
    assert mv._baslik_temiz("I. GENEL OLARAK2") == ("I. GENEL OLARAK", ["2"])
    assert mv._baslik_temiz("Geçici Madde 2") == ("Geçici Madde 2", [])   # boşluklu rakam başlığın parçası


def test_kat_kat_baslik_tek_birim_ve_yol() -> None:
    k = _coz(KIRA)
    assert _govde(KIRA, k[342]).rstrip().endswith("geri vermekle yükümlüdür.")   # "E. KİRA BEDELİ" sızmaz
    assert k[343]["on_blok"] == ["E. KİRA BEDELİ", "I. GENEL OLARAK2"]
    assert k[343]["baslik_yolu"] == ["E. KİRA BEDELİ", "I. GENEL OLARAK"]
    assert k[344]["baslik_yolu"] == ["E. KİRA BEDELİ", "II. BELİRLENMESİ"]
    assert k[345]["baslik_yolu"] == ["E. KİRA BEDELİ", "III. DAVA AÇMA SÜRESİ VE KARARIN ETKİSİ"]


def test_dugum_sonundaki_sonraki_madde_blogu_ve_nbsp_atilir() -> None:
    dugum = ("MADDE 146- Kanunda aksine bir hüküm bulunmadıkça, her alacak on yıllık zamanaşımına tabidir.\n\n"
             "&NBSP;\n\nII. BEŞ YILLIK ZAMANAŞIMI\n")
    b = _coz(dugum)[146]
    assert _govde(dugum, b).rstrip().endswith("zamanaşımına tabidir.")
    tmk = ("MADDE 23- Kimse hak ve fiil ehliyetlerinden kısmen de olsa vazgeçemez.\n\n"
           "II. Saldırıya karşı\n\n1. İlke\n")
    assert _govde(tmk, _coz(tmk)[23]).rstrip().endswith("vazgeçemez.")


def test_numarali_liste_basliga_donusmez() -> None:
    metin = ("MADDE 10- Şirket sözleşmesinde şunlar yazılır:\n\n1. Unvanı\n\n2. Merkezi\n\n"
             "MADDE 11- Sözleşme yazılı yapılır.\n")
    k = _coz(metin)
    assert _govde(metin, k[10]).rstrip().endswith("2. Merkezi")
    assert k[11]["on_baslik"] is None


def test_liste_ile_biten_maddeden_sonra_ust_kademe_baslik_tanir() -> None:
    metin = ("MADDE 144- Şu alacaklar takas edilemez:\n\n1. Tevdi edilen eşyanın geri verilmesi.\n\n"
             "2. Nafaka gibi doğrudan alacaklıya verilmesi gereken alacaklar.\n\n"
             "IV. TAKASTAN FERAGAT\n\nMADDE 145- Borçlu, takas hakkından önceden de feragat edebilir.\n")
    k = _coz(metin)
    assert k[145]["on_baslik"] == "IV. TAKASTAN FERAGAT"
    assert _govde(metin, k[144]).rstrip().endswith("gereken alacaklar.")


def test_buyuk_harfli_tek_harf_etiketi_kademesini_baglamdan_alir() -> None:
    metin = ("II. ÖNERİ VE KABUL\n\n1. SÜRELİ ÖNERİ\n\nMADDE 3- Öneren, süre içinde kabul beklemekle yükümlüdür.\n\n"
             "&NBSP;\n\n2. SÜRESİZ ÖNERİ\n\nA. HAZIR OLANLAR ARASINDA\n\n"
             "MADDE 4- Hemen kabul edilmeyen öneri, önereni bağlamaz.\n\n"
             "B. HAZIR OLMAYANLAR ARASINDA\n\nMADDE 5- Öneren, olağan sürede kabul beklemekle yükümlüdür.\n\n"
             "3. ÖRTÜLÜ KABUL\n\nMADDE 6- Açık kabul gerekmiyorsa sözleşme kurulmuş sayılır.\n")
    k = _coz(metin)
    assert k[4]["on_baslik"] == "A. HAZIR OLANLAR ARASINDA"
    assert k[4]["baslik_yolu"] == ["II. ÖNERİ VE KABUL", "2. SÜRESİZ ÖNERİ", "A. HAZIR OLANLAR ARASINDA"]
    assert k[5]["baslik_yolu"] == ["II. ÖNERİ VE KABUL", "2. SÜRESİZ ÖNERİ", "B. HAZIR OLMAYANLAR ARASINDA"]
    assert k[6]["baslik_yolu"] == ["II. ÖNERİ VE KABUL", "3. ÖRTÜLÜ KABUL"]
    assert _govde(metin, k[3]).rstrip().endswith("beklemekle yükümlüdür.")


def test_yapi_basligi_yolu_sifirlar() -> None:
    metin = ("F. Takas\n\nI. Koşulları\n\nMADDE 139- İki kişi karşılıklı alacaklıysa takas edebilir.\n\n"
             "İKİNCİ AYIRIM\n\nZamanaşımı\n\nA. SÜRELER\n\nI. ON YILLIK ZAMANAŞIMI\n\n"
             "MADDE 146- Her alacak on yıllık zamanaşımına tabidir.\n")
    k = _coz(metin)
    assert k[146]["baslik_yolu"] == ["A. SÜRELER", "I. ON YILLIK ZAMANAŞIMI"]
    assert _govde(metin, k[139]).rstrip().endswith("takas edebilir.")


def test_etiketsiz_kenar_basligi_degismez() -> None:
    metin = ("MADDE 119- Bedel, tarafların talebi üzerine mahkeme tarafından tespit edilir.\n\n"
             "Çalışanın önalım hakkı\n\nMADDE 120- (1) İşverenin iflas etmesi hâlinde çalışanın önalım hakkı vardır.\n")
    k = _coz(metin)
    assert k[120]["on_baslik"] == "Çalışanın önalım hakkı"
    assert k[120]["baslik_yolu"] == []
    assert _govde(metin, k[119]).rstrip().endswith("tespit edilir.")


# --------------------------------------------------------------------------- tek madde = liste
def _html(*paragraflar: str) -> dict:
    govde = "".join("<p>%s</p>" % p for p in paragraflar)
    return {"content": base64.b64encode(govde.encode("utf-8")).decode("ascii"), "mimeType": "text/html"}


def _sahte_tbk():
    arama = {"mevzuatList": [{
        "mevzuatId": "103273", "mevzuatNo": 6098, "mevzuatAdi": "TÜRK BORÇLAR KANUNU",
        "mevzuatTur": {"id": 1, "name": "KANUN", "description": "Kanunlar"}, "mevzuatTertip": 5,
        "resmiGazeteTarihi": "2011-02-04T00:00:00", "resmiGazeteSayisi": "27836",
        "url": "https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=6098&MevzuatTur=1&MevzuatTertip=5",
        "mukerrer": "HAYIR", "gerekceId": None}], "total": 1, "start": 0}
    agac = {"children": [{"children": [
        {"children": [], "maddeId": "9%d" % n, "maddeNo": n, "title": "", "description": None,
         "maddeBaslik": "Madde No: %d" % n, "gerekceId": None, "mevzuatId": "103273"} for n in (342, 343, 344, 345)],
        "maddeId": "9000", "maddeNo": None, "title": "İKİNCİ AYIRIM", "mevzuatId": "103273"}]}
    tam = _html(*[s for s in KIRA.split("\n\n") if s.strip()])
    dugum_344 = _html("MADDE 344 - Tarafların yenilenen kira dönemlerinde uygulanacak kira bedeline ilişkin "
                      "anlaşmaları geçerlidir.", "III. DAVA AÇMA SÜRESİ VE KARARIN ETKİSİ")
    istekler = []

    def call(path, data, paging=False):
        istekler.append(path)
        if path == "/searchDocuments":
            return arama
        if path == "/mevzuatMaddeTree":
            return agac
        if path == "/getDocumentContent":
            if data.get("documentType") == "MEVZUAT":
                return tam
            if str(data.get("id")) == "9344":
                return dugum_344
        raise mv.HttpError(404, "sahte" + path, b"Record not found")

    mv._CACHE.clear()
    mv._call = call
    return istekler


def test_tek_madde_ile_liste_ayni_basligi_ve_yolu_verir() -> None:
    _sahte_tbk()
    tek = mv.article({"number": "6098", "madde_no": "344"})["items"][0]
    _sahte_tbk()
    liste = {i["madde_no"]: i for i in mv.article({"number": "6098", "madde_no": ["343", "344"]})["items"]}
    for it in (tek, liste["344"]):
        assert it["ok"] is True
        assert it["heading"] == "II. BELİRLENMESİ"
        assert it["heading_path"] == ["E. KİRA BEDELİ", "II. BELİRLENMESİ"]
        assert it["heading_source"] == "text"
        assert set(it["heading_footnote_refs"]) == {"2", "3", "4"}
        assert "DAVA AÇMA SÜRESİ" not in it["text"]
    assert liste["343"]["heading"] == "I. GENEL OLARAK"
