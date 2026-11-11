"""Tapu kaydı maskeleme testleri (ağsız).

Bu dosyanın varlık sebebi bir kusur: v0.2.0'a kadar TEK maske ayraçsız 11 haneli
TCKN'ydi. Malik adı, ayraçlı TCKN, IBAN, telefon ve e-posta olduğu gibi geçiyordu —
ve eski test bunu yakalamak yerine adın içindeki maskeli TCKN'yi doğrulayarak
açığın üstünü örtüyordu. Testler artık "ne maskelendi"yi değil, "kayıtta ne KALDI"yı
sorar: sızıntı testi, tek tek alanları değil bütün JSON gövdesini tarar.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server  # noqa: E402
import tkgm_tapu as tapu  # noqa: E402

# Kurgusal. TCKN'ler NVİ sağlamasından geçer (maskelenmeleri gerekir), IBAN mod-97'den.
TCKN_A = "10000000146"
TCKN_B = "19191919190"
IBAN = "TR330006100519786457841326"
BELGE = """TAPU KAYIT BİLGİLERİ
İl: Ankara
İlçe: Çankaya
Ada: 123
Parsel: 4
Yüzölçümü: 1.195,00 m2
Malik: AHMET ÖRNEK (T.C. %s)
Hisse Pay/Payda: 1/2
Malik: Ayşe Örnek
Hisse Pay/Payda: 1/2
İletişim: ayse.ornek@example.com - 0532 111 22 33
ŞERHLER
Aile konutu şerhi (Ayşe Örnek lehine) - 12.03.2019 / 4521
İhtiyati tedbir: AHMET ÖRNEK aleyhine, T.C. %s
BEYANLAR
2/B alanında kalmaktadır
REHİNLER
X Bankası A.Ş. (Vergi Kimlik No: 1234567801) lehine ipotek, IBAN %s
Yevmiye: 4521987650
""" % (TCKN_A, TCKN_B, IBAN)


class Sizinti(unittest.TestCase):
    """Kayıdın TAMAMINDA arama yapar; alan alan bakmak açığı gizleyen şeydi."""

    def govde(self, **kw):
        return json.dumps(tapu.ayristir(BELGE, **kw), ensure_ascii=False)

    def test_varsayilan_hicbir_kimlik_ve_malik_adi_sizmaz(self):
        g = self.govde()
        for gizli in (TCKN_A, TCKN_B, IBAN, "ayse.ornek@example.com", "0532 111 22 33",
                      "AHMET ÖRNEK", "Ayşe Örnek", "1234567801"):
            self.assertNotIn(gizli, g, "sızdı: %s" % gizli)

    def test_ad_serh_satirinda_da_etiketlenir(self):
        k = tapu.ayristir(BELGE)
        self.assertIn("{{MALİK-02}}", k["serhler"][0])          # "Ayşe Örnek lehine"
        self.assertIn("{{MALİK-01}}", k["serhler"][1])          # büyük harfli yazım
        self.assertEqual(k["malikler"][0]["ad"].split(" (")[0], "{{MALİK-01}}")

    def test_ayni_kisi_her_yerde_ayni_etiket(self):
        k = tapu.ayristir(BELGE)
        self.assertEqual(json.dumps(k, ensure_ascii=False).count("{{MALİK-01}}"), 2)
        self.assertEqual(len({e for e in ("{{MALİK-01}}", "{{MALİK-02}}")
                              if e in json.dumps(k, ensure_ascii=False)}), 2)

    def test_ad_maskele_kapatilabilir_ve_bunu_soyler(self):
        k = tapu.ayristir(BELGE, ad_maskele=False)
        self.assertIn("AHMET ÖRNEK", json.dumps(k, ensure_ascii=False))
        self.assertIn(TCKN_A, "".join(k["kvkk"]["maskelenen"]) + TCKN_A)   # TCKN yine maskeli
        self.assertNotIn(TCKN_A, json.dumps(k, ensure_ascii=False))
        self.assertTrue(any("ad_maskele=false" in x for x in k["kvkk"]["maskelenmeyen"]))

    def test_kvkk_alani_maskelenmeyeni_de_sayar(self):
        kv = tapu.ayristir(BELGE)["kvkk"]
        self.assertTrue(any("TCKN" in x for x in kv["maskelenen"]))
        self.assertTrue(any("MALİK" in x for x in kv["maskelenen"]))
        self.assertTrue(any("ÜÇÜNCÜ kişi" in x for x in kv["maskelenmeyen"]))
        self.assertIn("ArthurMask", kv["daha_fazlasi"])


class Kaliplar(unittest.TestCase):
    def test_tckn_sagalamasi(self):
        self.assertTrue(tapu._tckn_gecerli(TCKN_A))
        self.assertFalse(tapu._tckn_gecerli("12345678901"))     # sağlama tutmaz
        self.assertFalse(tapu._tckn_gecerli("01234567890"))     # 0 ile başlayamaz

    def test_ayracli_tckn_de_maskelenir(self):
        a, b, c, d = TCKN_A[:3], TCKN_A[3:6], TCKN_A[6:9], TCKN_A[9:]
        for ayrac in (" ", ".", "-", ""):
            m = tapu._maskele(ayrac.join((a, b, c, d)))
            self.assertNotIn(a + ayrac + b, m, "ayraç %r ile sızdı" % ayrac)

    def test_sagalamasiz_11_hane_maskelenmez(self):
        """Yevmiye numarası kimlik değildir; körü körüne maskelemek kaydı bozar."""
        self.assertIn("12345678901", tapu._maskele("Yevmiye: 12345678901"))

    def test_iban_mod97(self):
        self.assertTrue(tapu._iban_gecerli(IBAN))
        self.assertFalse(tapu._iban_gecerli(IBAN[:-1] + "7"))
        self.assertNotIn(IBAN, tapu._maskele("Hesap " + IBAN))

    def test_vkn_yalniz_baglamda_maskelenir(self):
        self.assertNotIn("1234567801", tapu._maskele("Vergi Kimlik No: 1234567801"))
        self.assertNotIn("1234567801", tapu._maskele("VKN 1234567801"))
        # Etiketsiz on haneli sayı: dokunulmaz (yevmiye, alan, sicil olabilir).
        self.assertIn("4521987650", tapu._maskele("Yevmiye: 4521987650"))

    def test_turkce_buyuk_kucuk(self):
        d = tapu._ad_deseni(["AHMET", "İNCE"])
        for yazim in ("Ahmet İnce", "AHMET İNCE", "Ahmet Ince", "ahmet ince", "Ahmet  İNCE", "AHMET\nİNCE"):
            self.assertTrue(d.search(yazim), yazim)
        self.assertFalse(d.search("AHMET İNCEOĞLU"))


class Arac(unittest.TestCase):
    def setUp(self):
        os.environ["TKGM_DOSYA_ERISIMI"] = "1"

    def tearDown(self):
        os.environ.pop("TKGM_DOSYA_ERISIMI", None)

    def test_arac_varsayilani_maskeler(self):
        out = json.loads(server._t_tapu({"metin": BELGE}))
        g = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("AHMET ÖRNEK", g)
        self.assertNotIn(TCKN_A, g)
        self.assertIn("{{MALİK-01}}", g)

    def test_arac_ad_maskele_bayragi(self):
        out = json.loads(server._t_tapu({"metin": BELGE, "ad_maskele": False}))
        self.assertIn("AHMET ÖRNEK", json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
