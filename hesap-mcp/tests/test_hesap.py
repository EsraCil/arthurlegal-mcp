"""Hesap kartları testleri (ağsız).

    python -m unittest discover -s hesap-mcp/tests      # depo kökünden

Tutarlar burada SABİTTİR: Resmî Gazete'den aktarılan bir rakam değişirse test düşer, değişiklik
fark edilmeden geçmez. Yeni yıl tarifesi gelince veri ve bu testler birlikte güncellenir.
"""

import json
import os
import re
import sys
import unittest

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)

import hesap  # noqa: E402
import server  # noqa: E402
from mcpcore import McpError  # noqa: E402

with open(os.path.join(KOK, "veri", "tarife_2026.json"), encoding="utf-8") as fh:
    VERI = json.load(fh)


def tl(metin):
    """'62.000,00' -> 62000.0"""
    return float(metin.replace(".", "").replace(",", "."))


class Veri(unittest.TestCase):
    def test_yuklendi(self):
        d = hesap.durum()
        self.assertIsNone(d["hata"])
        self.assertEqual((d["aaut_kademe"], d["aaut_maktu_satiri"]), (10, 58))

    def test_kaynak_kunyeleri(self):
        k = {x["kod"]: x for x in VERI["kaynaklar"]}
        self.assertEqual((k["AAUT2026"]["rg_tarih"], k["AAUT2026"]["rg_sayi"]), ("2026-11-03", "33419"))
        self.assertEqual(k["AAUT2026"]["url"], "https://www.resmigazete.gov.tr/eskiler/2026/11/20261103-3.htm")
        self.assertEqual((k["HKGT98"]["rg_tarih"], k["HKGT98"]["rg_sayi"], k["HKGT98"]["mukerrer"]), ("2025-12-31", "33124", "5"))
        self.assertEqual((k["HMKGAT"]["rg_tarih"], k["HMKGAT"]["rg_sayi"]), ("2025-10-28", "33061"))
        for x in VERI["kaynaklar"]:
            self.assertTrue(x["url"].startswith("https://www.resmigazete.gov.tr/") or x["url"].startswith("https://www.mevzuat.gov.tr/"), x["kod"])

    def test_aaut_tutarlari_sabit(self):
        m = {r["kod"]: r for r in VERI["aaut"]["maktu"]}
        beklenen = {
            "1.1.1": 5500, "1.1.2": 9000, "1.1.3": 9000, "1.1.4": 7500, "1.1.5a": 10000, "1.1.5b": 40000,
            "1.1.5c": 27000, "1.1.6": 10000, "1.2.1": 7500, "1.2.2a": 15000, "1.2.2b": 4000, "1.2.3": 78000,
            "1.2.4": 30000, "1.2.5a": 140000, "1.2.5b": 250000, "1.3.1": 35000, "1.3.2": 58000, "1.4.1": 43000,
            "1.4.2": 43000, "2.1.1a": 13000, "2.1.1b": 16000, "2.1.2": 25000, "2.1.3": 52000, "2.1.4a": 40000,
            "2.1.4b": 52000, "2.1.5": 26000, "2.2.1": 12500, "2.2.2": 15000, "2.2.3": 23000, "2.2.4": 26000,
            "2.2.5": 20000, "2.2.6": 21000, "2.2.7": 15000, "2.2.8": 40000, "2.2.9": 23000, "2.2.10": 62000,
            "2.2.11": 30000, "2.2.12": 70000, "2.2.13": 86000, "2.2.14": 62000, "2.2.15": 86000, "2.2.16": 34000,
            "2.2.17a": 40000, "2.2.17b": 52000, "2.2.18a": 45000, "2.2.18b": 28000, "2.2.18c": 55000,
            "2.2.19a": 44000, "2.2.19b": 84000, "2.2.20": 85000, "2.2.21a": 50000, "2.2.21b": 80000,
            "2.2.22": 50000, "2.2.23": 50000, "2.2.24a": 150000, "2.2.24b1": 55000, "2.2.24b2": 100000,
            "2.2.24c": 115000,
        }
        self.assertEqual({k: r["tutar_tl"] for k, r in m.items()}, beklenen)
        self.assertEqual({k: r["ek_saat_tl"] for k, r in m.items() if r.get("ek_saat_tl")},
                         {"1.1.1": 2400, "1.1.2": 4500, "1.1.3": 4500})

    def test_alinti_tutari_tasir(self):
        """Her satırın tutarı kendi alıntısında Türkçe yazımla geçer: aktarım hatası yakalanır."""
        for r in VERI["aaut"]["maktu"]:
            tutarlar = [tl(x) for x in re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", r["alinti"])]
            self.assertIn(r["tutar_tl"], tutarlar, r["kod"])
        for r in VERI["harc"]["kalemler"]:
            if r.get("tutar_tl") is not None:
                tutarlar = [tl(x) for x in re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}|\d+(?= TL)", r["alinti"])]
                self.assertIn(r["tutar_tl"], tutarlar, r["kod"])
            for alan in ("oran_binde", "oran_yuzde"):
                if r.get(alan) is not None:
                    self.assertIn(("%.2f" % r[alan]).replace(".", ","), r["alinti"], r["kod"])

    def test_kademeler(self):
        k = VERI["aaut"]["kademeler"]
        self.assertEqual([x["yuzde"] for x in k], [16, 15, 14, 13, 11, 8, 5, 3, 2, 1])
        self.assertEqual(sum(x["dilim_tl"] for x in k[:-1]), k[-1]["esik_tl"])
        self.assertEqual(k[-1]["esik_tl"], 24800000)

    def test_harc_tutarlari_sabit(self):
        h = {r["kod"]: r for r in VERI["harc"]["kalemler"]}
        self.assertEqual({k: r["tutar_tl"] for k, r in h.items() if r.get("tutar_tl") is not None}, {
            "A-I-1": 335.2, "A-I-2": 732.0, "A-I-3": 1124.5, "A-I-4": 6024.1, "A-II-1-b": 411.6,
            "A-III-1-asgari": 732.0, "A-III-2-a": 732.0, "A-III-2-b": 1526.2, "A-III-2-c": 1206.0,
            "A-III-2-d": 1206.0, "A-III-2-e": 732.0, "A-IV-a": 3608.5, "A-IV-c": 3608.5, "A-IV-d": 2002.0,
            "A-IV-e": 2002.0, "A-V": 5188.0, "B-I-1": 732.0, "B-I-2": 732.0})
        self.assertEqual(h["A-III-1-a"]["oran_binde"], 68.31)
        self.assertEqual(VERI["gider_avansi"]["diger_islemler_tl"], 530.0)
        self.assertEqual(VERI["gider_avansi"]["tebligat_kati"], 5)

    def test_kodda_tarife_rakami_yok(self):
        """Tutarlar veride durur; hesap.py'de bir tarife tutarı ya da oranı görünmemeli."""
        with open(os.path.join(KOK, "hesap.py"), encoding="utf-8") as fh:
            kod = fh.read()
        rakamlar = {r["tutar_tl"] for r in VERI["aaut"]["maktu"]} | {r["tutar_tl"] for r in VERI["harc"]["kalemler"]
                                                                     if r.get("tutar_tl")}
        rakamlar |= {78125.0, 62500.0, 9000.0, 530.0, 68.31}
        for x in rakamlar:
            for yazim in ("%d" % x if x == int(x) else "%s" % x, "%.2f" % x):
                if len(yazim) >= 3:
                    self.assertNotRegex(kod, r"(?<![\w.])%s(?![\w])" % re.escape(yazim), yazim)


class Vekalet(unittest.TestCase):
    def test_nispi_kademeli(self):
        o = hesap.vekalet("nispi_dava", tutar=1_000_000, mahkeme="2.2.10")
        self.assertEqual(o["asgari_tl"], 158000.0)            # 800.000 × %16 + 200.000 × %15
        self.assertEqual(o["ust_sinir_tl"], 474000.0)         # m. 3/1: üç katı
        self.assertEqual([d["ucret_tl"] for d in o["kalemler"][0]["dokum"]], [128000.0, 30000.0])

    def test_nispi_buyuk_tutar_son_kademe(self):
        o = hesap.vekalet("nispi_dava", tutar=30_000_000, mahkeme="2.2.10")
        self.assertEqual(o["asgari_tl"], 1708000.0)           # 24,8 M'ye kadar 1.656.000 + 5,2 M × %1

    def test_nispi_mahkeme_tabani(self):
        o = hesap.vekalet("nispi_dava", tutar=100_000, mahkeme="2.2.10")
        self.assertEqual(o["asgari_tl"], 62000.0)             # m. 13/1
        o = hesap.vekalet("nispi_dava", tutar=100_000, mahkeme="2.2.8")
        self.assertEqual(o["asgari_tl"], 40000.0)

    def test_nispi_miktari_gecemez(self):
        o = hesap.vekalet("nispi_dava", tutar=30_000, mahkeme="2.2.10")
        self.assertEqual(o["asgari_tl"], 30000.0)             # m. 13/2
        self.assertEqual(o["ust_sinir_tl"], 30000.0)

    def test_nispi_mahkeme_kalemi_ister(self):
        with self.assertRaises(hesap.HesapHatasi):
            hesap.vekalet("nispi_dava", tutar=100_000, mahkeme="1.1.4")
        with self.assertRaises(hesap.HesapHatasi):
            hesap.vekalet("nispi_dava", tutar=100_000)

    def test_maktu_ve_saat(self):
        self.assertEqual(hesap.vekalet("maktu", kalem="2.2.13")["asgari_tl"], 86000.0)
        self.assertEqual(hesap.vekalet("maktu", kalem="1.1.1", saat=3)["asgari_tl"], 10300.0)
        self.assertEqual(hesap.vekalet("maktu", kalem="1.1.1")["asgari_tl"], 5500.0)
        self.assertIsNone(hesap.vekalet("maktu", kalem="1.1.4").get("ust_sinir_tl"))   # dava dışı: m. 3/1 yok
        with self.assertRaises(hesap.HesapHatasi):
            hesap.vekalet("maktu", kalem="1.1.1", saat=1.5)
        with self.assertRaises(hesap.HesapHatasi):
            hesap.vekalet("maktu", kalem="2.2.10", saat=3)

    def test_icra(self):
        self.assertEqual(hesap.vekalet("icra", takip_turu="para", tutar=50_000)["asgari_tl"], 12500.0)
        self.assertEqual(hesap.vekalet("icra", takip_turu="para", tutar=78_125)["asgari_tl"], 12500.0)
        self.assertEqual(hesap.vekalet("icra", takip_turu="para", tutar=10_000)["asgari_tl"], 10000.0)
        self.assertEqual(hesap.vekalet("icra", takip_turu="para", tutar=200_000)["asgari_tl"], 32000.0)
        self.assertEqual(hesap.vekalet("icra", takip_turu="para", tutar=200_000, odeme_suresinde=True)["asgari_tl"], 24000.0)
        self.assertEqual(hesap.vekalet("icra", takip_turu="tahliye")["asgari_tl"], 26000.0)
        self.assertEqual(hesap.vekalet("icra", takip_turu="tahliye", odeme_suresinde=True)["asgari_tl"], 19500.0)

    def test_arabuluculuk(self):
        a = lambda **k: hesap.vekalet("arabuluculuk", **k)["asgari_tl"]  # noqa: E731
        self.assertEqual(a(sonuc="anlasma", konu="para", tutar=50_000), 12500.0)
        self.assertEqual(a(sonuc="anlasma", konu="para", tutar=10_000), 10000.0)       # asıl alacağı geçemez
        self.assertEqual(a(sonuc="anlasma", konu="para", tutar=500_000), 100000.0)     # 80.000 × 1,25
        self.assertEqual(a(sonuc="anlasmazlik", konu="para", tutar=500_000), 10000.0)
        self.assertEqual(a(sonuc="anlasmazlik", konu="para", tutar=5_000), 5000.0)
        self.assertEqual(a(sonuc="anlasma", konu="para_disi", mahkeme="2.2.10"), 77500.0)
        self.assertEqual(a(sonuc="anlasmazlik", konu="para_disi"), 10000.0)

    def test_hakem_heyeti(self):
        self.assertEqual(hesap.vekalet("hakem_heyeti", tutar=20_000)["asgari_tl"], 9000.0)
        self.assertEqual(hesap.vekalet("hakem_heyeti", tutar=5_000)["asgari_tl"], 5000.0)
        self.assertEqual(hesap.vekalet("hakem_heyeti", tutar=100_000)["asgari_tl"], 16000.0)

    def test_cikti_asgari_der_onermez(self):
        o = hesap.vekalet("maktu", kalem="2.2.10")
        metin = json.dumps(o, ensure_ascii=False)
        self.assertIn("ASGARİ", o["asgari_notu"]["metin"])
        self.assertNotIn("önerilen ücret", metin.lower().replace("önerilen ya da uygun ücret değildir", ""))
        self.assertEqual(o["asgari_notu"]["dayanak"][0], "AVUKATLIK KANUNU (Kanun No. 1136, RG sayı 13168) m. 168")
        self.assertEqual(o["kaynak"]["rg"], "RG 03.11.2026, S. 33419")


class Harc(unittest.TestCase):
    def test_dava_acilis_nispi(self):
        o = hesap.harc("dava_acilis", mahkeme="asliye", deger=1_000_000, taraf_sayisi=2, tebligat_ucreti=150)
        self.assertEqual([k["tutar_tl"] for k in o["kalemler"]], [732.0, 17077.5, 2030.0])
        self.assertEqual(o["toplam_tl"], 19839.5)
        self.assertNotIn("ayrica", o)

    def test_dava_acilis_olum_cismani(self):
        o = hesap.harc("dava_acilis", mahkeme="asliye", deger=1_000_000, olum_cismani=True)
        self.assertEqual(o["kalemler"][1]["tutar_tl"], 3415.5)          # 68.310 / 20

    def test_dava_acilis_alt_sinir(self):
        o = hesap.harc("dava_acilis", mahkeme="sulh", deger=5_000)
        self.assertEqual(o["kalemler"][0]["tutar_tl"], 335.2)
        self.assertEqual(o["kalemler"][1]["tutar_tl"], 183.0)           # 732 / 4
        self.assertTrue(any("alt sınırı" in t for t in o["teyit_edin"]))

    def test_dava_acilis_degersiz_ve_tebligatsiz(self):
        o = hesap.harc("dava_acilis", mahkeme="asliye", taraf_sayisi=3)
        self.assertEqual(o["toplam_tl"], 732.0)
        self.assertEqual([a["tutar_tl"] for a in o["ayrica"]], [732.0])   # maktu karar harcı toplama girmez
        self.assertEqual(o["gider_avansi"]["eksik"], "tebligat_ucreti")

    def test_karar_harci(self):
        o = hesap.harc("karar_harci", deger=1_000_000, pesin_odenen=17077.5)
        self.assertEqual((o["toplam_tl"], o["kalan_tl"]), (68310.0, 51232.5))
        self.assertEqual(hesap.harc("karar_harci", deger=100_000, karar_turu="taksim")["toplam_tl"], 732.0)
        self.assertEqual(hesap.harc("karar_harci", deger=1_000_000, karar_turu="izale_satis")["toplam_tl"], 11380.0)

    def test_kanun_yollari(self):
        self.assertEqual(hesap.harc("istinaf", yargi="adli")["toplam_tl"], 2002.0)
        self.assertEqual(hesap.harc("istinaf", yargi="idari")["toplam_tl"], 2002.0)
        self.assertEqual(hesap.harc("temyiz", yargi="adli")["toplam_tl"], 3608.5)
        o = hesap.harc("temyiz", yargi="idari")
        self.assertEqual(o["toplam_tl"], 3608.5)
        self.assertEqual(o["ayrica"][0]["kod"], "A-I-3")

    def test_digerleri(self):
        self.assertEqual(hesap.harc("ihtiyati", mahkeme="sulh")["toplam_tl"], 335.2)
        self.assertEqual(hesap.harc("kesif")["toplam_tl"], 5188.0)
        self.assertEqual(hesap.harc("aym")["toplam_tl"], 6024.1)

    def test_gider_avansi_dayanak(self):
        o = json.loads(server._t_harc({"islem": "dava_acilis", "mahkeme": "asliye", "taraf_sayisi": "4",
                                       "tebligat_ucreti": "200,00"}))
        self.assertEqual(o["kalemler"][-1]["tutar_tl"], 4530.0)          # 4 × 5 × 200 + 530
        self.assertIn("RG 28.10.2025, S. 33061", o["kalemler"][-1]["dayanak"])


class Arac(unittest.TestCase):
    def test_turkce_tutar(self):
        o = json.loads(server._t_vekalet({"islem": "nispi_dava", "tutar": "1.000.000,00", "mahkeme": "2.2.10"}))
        self.assertEqual(o["asgari_tl"], 158000.0)

    def test_hatalar_mcp_hatasi(self):
        with self.assertRaises(McpError):
            server._t_vekalet({"islem": "uydurma"})
        with self.assertRaises(McpError):
            server._t_harc({"islem": "dava_acilis", "mahkeme": "asliye", "deger": "-5"})
        with self.assertRaises(McpError):
            server._t_kalemler({"kart": "faiz"})

    def test_menu_veriden(self):
        m = json.loads(server._t_kalemler({}))
        v = {i["kod"]: i for i in m["vekalet"]["islemler"]}
        mahkemeler = next(g for g in v["nispi_dava"]["girdiler"] if g["ad"] == "mahkeme")["secenekler"]
        self.assertIn({"kod": "2.2.10", "ad": "Asliye mahkemesi", "tutar_tl": 62000.0}, mahkemeler)
        gruplar = next(g for g in v["maktu"]["girdiler"] if g["ad"] == "kalem")["gruplar"]
        self.assertEqual(sum(len(g["secenekler"]) for g in gruplar), 58)
        self.assertEqual({i["kod"] for i in m["harc"]["islemler"]},
                         {"dava_acilis", "karar_harci", "istinaf", "temyiz", "ihtiyati", "kesif", "aym"})


if __name__ == "__main__":
    unittest.main()
