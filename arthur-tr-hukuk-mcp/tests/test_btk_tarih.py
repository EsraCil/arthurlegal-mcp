"""BTK karar tarihi: API'nin UTC damgası Türkiye'deki güne çevrilir (ağa çıkmaz)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import btk  # noqa: E402


class BtkTarih(unittest.TestCase):
    def test_gece_yarisi_damgasi_ertesi_gundur(self):
        # 15.09.2026 Salı tarihli karar; btk.tr sayfası da 15.09.2026 gösterir.
        self.assertEqual(btk._gun("2026-09-14T21:00:00.000Z"), "2026-09-15")

    def test_kis_saati_donemindeki_damga(self):
        # 2016 öncesi kış: Türkiye UTC+2, gece yarısı 22:00Z.
        self.assertEqual(btk._gun("2012-01-09T22:00:00.000Z"), "2012-01-10")

    def test_gun_ici_damga_ayni_gundur(self):
        self.assertEqual(btk._gun("2026-10-01T08:21:47.115Z"), "2026-10-01")

    def test_saatsiz_ve_bos_deger(self):
        self.assertEqual(btk._gun("2026-09-15"), "2026-09-15")
        self.assertEqual(btk._gun(None), "")
        self.assertEqual(btk._gun("bozuk"), "bozuk")

    def test_atif_dogru_gunu_tasir(self):
        kayit = btk._item({"id": 1, "title": "t", "publishedAt": "2026-10-01T08:21:47.115Z",
                           "data": {"decision_no": "2026/DK-ETD/217",
                                    "decision_date": "2026-09-14T21:00:00.000Z"}})
        self.assertEqual(kayit["decision_date"], "2026-09-15")
        self.assertIn("15.09.2026", kayit["citation"])


if __name__ == "__main__":
    unittest.main()
