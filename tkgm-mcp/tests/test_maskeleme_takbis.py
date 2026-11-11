"""Tapu maskelemesi — v0.3.0 incelemesinde sızdığı gösterilen biçimler (ağsız).

Her test bir sızıntıyı kapatır. Sızıntı alan alan değil, yanıtın TAMAMINDA aranır.
Adlar ve numaralar kurgusaldır; TCKN'ler NVİ sağlamasından geçer.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkgm_tapu as tapu  # noqa: E402

TCKN = "10000000146"


def govde(kayit):
    return json.dumps(kayit, ensure_ascii=False)


class TakbisAdBicimleri(unittest.TestCase):
    def sizmaz(self, metin, *gizliler):
        g = govde(tapu.ayristir(metin))
        for gizli in gizliler:
            self.assertNotIn(gizli, g, "sızdı: %s" % gizli)
        return g

    def test_sn_ve_baba_adi(self):
        g = self.sizmaz("Malik: (SN:48213377) MEHMET YILMAZ : HASAN\nHisse: 1/1\nŞERHLER\n"
                        "Haciz: MEHMET YILMAZ aleyhine Ankara 3. İcra Dairesi 2024/55\n",
                        "MEHMET YILMAZ", "48213377", "HASAN")
        self.assertIn("{{MALİK-01}} aleyhine", g)

    def test_oglu_kizi_bicimleri(self):
        self.sizmaz("Malik: HASAN oğlu MEHMET YILMAZ\nMalik: ZEYNEP KAYA, HASAN Kızı\nŞERHLER\n"
                    "İhtiyati tedbir: MEHMET YILMAZ ve ZEYNEP KAYA aleyhine\n", "MEHMET YILMAZ", "ZEYNEP KAYA")

    def test_tckn_once_gelen_ad(self):
        self.sizmaz("Malik: %s - AYŞE DEMİR\nREHİNLER\nAYŞE DEMİR borçlu, 1. derece ipotek\n" % TCKN,
                    "AYŞE DEMİR", TCKN)

    def test_yazim_farklari(self):
        self.sizmaz("Malik: İBRAHİM İNCE\nŞERHLER\nİbrahim İnce lehine kira şerhi\n"
                    "Mehmet YILMAZ değil; Ibrahim Ince aleyhine haciz\nborçlu İBRAHİM\nİNCE\n"
                    "İBRAHİM  İNCE mirasçıları\n", "İbrahim İnce", "Ibrahim Ince", "İBRAHİM\\nİNCE", "İBRAHİM  İNCE")

    def test_yalniz_soyadi(self):
        g = self.sizmaz("Malik: MEHMET YILMAZ\nŞERHLER\nYILMAZ mirasçıları lehine şerh\n", "YILMAZ")
        self.assertIn("{{MALİK-01}} mirasçıları", g)

    def test_etiket_alt_satirda(self):
        self.sizmaz("Malik:\nMEHMET YILMAZ\nŞERHLER\nMEHMET YILMAZ aleyhine tedbir\n", "MEHMET YILMAZ")

    def test_malik_tanınmazsa_metin_donmez(self):
        with self.assertRaises(tapu.TapuHatasi):
            tapu.ayristir("MÜLKİYET BİLGİLERİ\nMEHMET YILMAZ 1/2 Satış\nŞERHLER\nMEHMET YILMAZ aleyhine haciz\n")
        # açık rıza yolu var: ad_maskele=false ile işlenir ve bunu kvkk'da söyler
        k = tapu.ayristir("MÜLKİYET BİLGİLERİ\nMEHMET YILMAZ 1/2\nŞERHLER\nMEHMET YILMAZ aleyhine\n", ad_maskele=False)
        self.assertIn("MEHMET YILMAZ", govde(k))
        self.assertTrue(any("ad_maskele=false" in x for x in k["kvkk"]["maskelenmeyen"]))

    def test_ad_kvkk_sayimi_dogru(self):
        k = tapu.ayristir("Malik: MEHMET YILMAZ\nMalik Tipi: GERÇEK KİŞİ\nMalik Sayısı: 1\n")
        self.assertTrue(any("(1 kişi)" in x for x in k["kvkk"]["maskelenen"]))
        self.assertEqual(len(k["malikler"]), 1)


class YanlisMaskeYok(unittest.TestCase):
    def test_mahalle_adi_malik_adiyla_ayni(self):
        k = tapu.ayristir("Mahalle: Yunus Emre\nAda: 1\nParsel: 2\nMalik: YUNUS EMRE\n")
        self.assertEqual(k["tasinmaz"]["mahalle"], "Yunus Emre")
        self.assertEqual(k["malikler"][0]["ad"], "{{MALİK-01}}")

    def test_irtifaktaki_hak_sahibi_malik_degil(self):
        k = tapu.ayristir("Malik: MEHMET YILMAZ\nİRTİFAKLAR\nHak Sahibi: TEİAŞ\n"
                          "Geçit hakkı 60 m2 AYŞE DEMİR lehine\n")
        self.assertEqual(len(k["malikler"]), 1)
        self.assertEqual(len(k["irtifaklar"]), 2)

    def test_tuzel_malik_maskelenmez(self):
        k = tapu.ayristir("Malik: MALİYE HAZİNESİ\n")
        self.assertIn("HAZİNESİ", k["malikler"][0]["ad"])
        self.assertTrue(any("tüzel" in x for x in k["kvkk"]["maskelenmeyen"]))

    def test_yevmiye_telefon_sanilmaz(self):
        self.assertIn("4521987650", tapu._maskele("Yevmiye: 4521987650"))


class KimlikKaliplari(unittest.TestCase):
    def test_nbsp_uzun_tire_satir_sonu(self):
        a, b, c, d = TCKN[:3], TCKN[3:6], TCKN[6:9], TCKN[9:]
        for yazim in (" ".join((a, b, c, d)), "–".join((a, b, c, d)), a + b + c + "\n" + d,
                      "  ".join((a, b, c, d))):
            self.assertNotIn(TCKN[:9], tapu._rakam(tapu._maskele("Kimlik " + yazim)), repr(yazim))

    def test_tc_etiketli_saglamasiz(self):
        self.assertNotIn("12345678901", tapu._maskele("T.C. Kimlik No: 12345678901"))
        self.assertIn("12345678901", tapu._maskele("Yevmiye: 12345678901"))

    def test_iban_bosluklu_ve_nbsp(self):
        iban = "TR33 0006 1005 1978 6457 8413 26"
        for yazim in (iban, iban.replace(" ", " "), iban.lower()):
            self.assertNotIn("1978", tapu._maskele("Hesap " + yazim), repr(yazim))

    def test_telefon_bicimleri(self):
        for yazim in ("(0532) 111 22 33", "+90 (532) 111 22 33", "0 (212) 555 12 34", "0212 555 12 34",
                      "+90 532 111 22 33", "Tel: 532 111 22 33"):
            m = tapu._maskele(yazim)
            self.assertNotIn("111", m, yazim) if "111" in yazim else self.assertNotIn("555", m, yazim)

    def test_vkn_bicimleri(self):
        self.assertNotIn("1234567801", tapu._maskele("V.K.N.: 1234567801"))
        self.assertNotIn("1234567801", tapu._maskele("Vergi Dairesi / No\nKızılbey 1234567801"))

    def test_eposta_tamami(self):
        self.assertNotIn("mehmetyilmaz", tapu._maskele("e-posta: m@mehmetyilmaz.av.tr"))




class ArthurMaskKancasi(unittest.TestCase):
    """ArthurMask kuruluysa üçüncü kişi adları da maskelenir; değilse kvkk bunu söyler.
    Gerçek paket (presidio) ağırdır; motorun sözleşmesi (analiz → bas/son/varlik/kanonik) sahteyle sınanır."""

    METIN = ("Malik: AHMET ÖRNEK\nREHİNLER\nGARANTİ BANKASI A.Ş. lehine 1. derece ipotek\n"
             "ŞERHLER\nVeli Kaya lehine kira şerhi\n")

    def setUp(self):
        import types

        class Bulgu:
            def __init__(self, bas, son, varlik, kanonik):
                self.bas, self.son, self.varlik, self.kanonik = bas, son, varlik, kanonik

        class Motor:
            def analiz(self, metin):
                out = []
                for ad, tur in (("GARANTİ BANKASI A.Ş.", "ORGANIZATION"), ("Veli Kaya", "PERSON")):
                    i = metin.find(ad)
                    if i >= 0:
                        out.append(Bulgu(i, i + len(ad), tur, ad))
                return out, []

        paket, motor = types.ModuleType("arthur_mask"), types.ModuleType("arthur_mask.motor")
        motor.MaskeMotoru = Motor
        self._eski = {k: sys.modules.get(k) for k in ("arthur_mask", "arthur_mask.motor")}
        sys.modules.update({"arthur_mask": paket, "arthur_mask.motor": motor})
        tapu._MOTOR = None

    def tearDown(self):
        for k, v in self._eski.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        tapu._MOTOR = None
        os.environ.pop("TKGM_ARTHUR_MASK", None)

    def test_kuruluysa_ucuncu_kisi_ve_kurum_maskelenir(self):
        k = tapu.ayristir(self.METIN)
        g = govde(k)
        for gizli in ("GARANTİ", "Veli Kaya", "AHMET ÖRNEK"):
            self.assertNotIn(gizli, g)
        self.assertIn("{{KURUM-01}}", g)
        self.assertIn("{{KİŞİ-01}}", g)
        self.assertTrue(any("ArthurMask" in x for x in k["kvkk"]["maskelenen"]))

    def test_kapatilabilir_ve_bunu_soyler(self):
        os.environ["TKGM_ARTHUR_MASK"] = "0"
        k = tapu.ayristir(self.METIN)
        self.assertIn("Veli Kaya", govde(k))
        self.assertTrue(any("devrede değil" in x for x in k["kvkk"]["maskelenmeyen"]))
        k = tapu.ayristir(self.METIN, ucuncu_kisi=False)
        self.assertTrue(any("ucuncu_kisi_maskele=false" in x for x in k["kvkk"]["maskelenmeyen"]))


if __name__ == "__main__":
    unittest.main()
