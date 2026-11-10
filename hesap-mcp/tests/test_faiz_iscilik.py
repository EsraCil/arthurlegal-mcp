"""Faiz ve işçilik alacağı kartlarının testleri (ağsız).

    python -m unittest discover -s hesap-mcp/tests      # depo kökünden

Oranlar ve tavanlar burada SABİTTİR: Resmî Gazete'den (TCMB tebliğleri, kanuni faiz kararları) ve ÇSGB tablosundan
aktarılan bir değer değişirse test düşer. Yeni dönem verisi gelince veri ve bu testler birlikte güncellenir.
"""

import json
import os
import re
import sys
import unittest
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from unittest import mock

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import faiz  # noqa: E402
import iscilik  # noqa: E402
import nobetci  # noqa: E402
import server  # noqa: E402
import tazelik  # noqa: E402
from mcpcore import McpError  # noqa: E402
from test_tazelik import SahteRG, kur, sayfa  # noqa: E402

with open(os.path.join(KOK, "veri", "tarife_2026.json"), encoding="utf-8") as fh:
    VERI = json.load(fh)
KAYNAK = {k["kod"]: k for k in VERI["kaynaklar"]}

# (yürürlük, reeskont, avans, RG tarihi, RG sayısı): her satır RG'deki tebliğden okundu, TCMB tablosuyla aynı.
TCMB = [
    ("2005-12-20", 23, 25, "2005-12-20", "26029"), ("2006-12-20", 27, 29, "2006-12-20", "26382"),
    ("2007-12-28", 25, 27, "2007-12-28", "26740"), ("2009-04-09", 19, 20, "2009-04-09", "27195"),
    ("2009-06-12", 18, 19, "2009-06-12", "27256"), ("2009-12-22", 15, 16, "2009-12-22", "27440"),
    ("2010-12-30", 14, 15, "2010-12-30", "27801"), ("2011-12-29", 17, 17.75, "2011-12-29", "28157"),
    ("2012-06-19", 16, 16.5, "2012-06-19", "28328"), ("2012-12-20", 13.5, 13.75, "2012-12-20", "28503"),
    ("2013-06-21", 9.5, 11, "2013-06-21", "28684"), ("2013-12-27", 10.25, 11.75, "2013-12-27", "28864"),
    ("2014-12-14", 9, 10.5, "2014-12-14", "29205"), ("2016-12-31", 8.75, 9.75, "2016-12-31", "29935"),
    ("2018-06-29", 18.5, 19.5, "2018-06-29", "30463"), ("2019-10-11", 17.25, 18.25, "2019-10-11", "30915"),
    ("2019-12-21", 12.75, 13.75, "2019-12-21", "30985"), ("2020-06-13", 9, 10, "2020-06-13", "31154"),
    ("2020-12-19", 15.75, 16.75, "2020-12-19", "31339"), ("2021-12-31", 14.75, 15.75, "2022-01-02", "31707"),
    ("2022-12-31", 9.75, 10.75, "2022-12-31", "32060"), ("2023-06-24", 15.75, 16.75, "2023-06-24", "32231"),
    ("2023-09-01", 25.75, 26.75, "2023-09-01", "32296"), ("2023-09-28", 30.75, 31.75, "2023-09-28", "32323"),
    ("2023-11-01", 35.75, 36.75, "2023-11-01", "32356"), ("2023-12-01", 40.75, 41.75, "2023-12-01", "32386"),
    ("2023-12-23", 43.25, 44.25, "2023-12-23", "32408"), ("2024-04-01", 50.75, 51.75, "2024-04-01", "32507"),
    ("2024-12-28", 48.25, 49.25, "2024-12-28", "32766"), ("2025-03-08", 43.25, 44.25, "2025-03-08", "32835"),
    ("2025-09-17", 41.25, 42.25, "2025-09-17", "33020"), ("2025-12-20", 38.75, 39.75, "2025-12-20", "33113"),
]
TAVAN = {
    "2016-01-01": 4092.53, "2016-07-01": 4297.21, "2017-01-01": 4426.16, "2017-07-01": 4732.48, "2018-01-01": 5001.76,
    "2018-07-01": 5434.42, "2019-01-01": 6017.60, "2019-07-01": 6379.86, "2020-01-01": 6730.15, "2020-07-01": 7117.17,
    "2021-01-01": 7638.96, "2021-07-01": 8284.51, "2022-01-01": 10848.59, "2022-07-01": 15371.40, "2023-01-01": 19982.83,
    "2023-07-01": 23489.83, "2024-01-01": 35058.58, "2024-07-01": 41828.42, "2025-01-01": 46655.43, "2025-07-01": 53919.68,
    "2026-01-01": 64948.77, "2026-07-01": 73729.87,
}


def setUpModule():
    tazelik.kur(tazelik.Nobetci(VERI, getir=None))


def tearDownModule():
    tazelik.kur(None)


class Veri(unittest.TestCase):
    def test_tcmb_tablosu_sabit(self):
        satir = [(r["yururluk"], r["reeskont"], r["avans"], r["rg_tarih"], r["rg_sayi"]) for r in VERI["faiz"]["tcmb"]]
        self.assertEqual(satir, [(a, float(b), float(c), d, e) for a, b, c, d, e in TCMB])

    def test_her_oran_rg_kunyesi_ve_ozetiyle(self):
        for r in VERI["faiz"]["tcmb"]:
            self.assertTrue(r["url"].startswith("https://www.resmigazete.gov.tr/eskiler/"), r["yururluk"])
            self.assertIn(r["rg_tarih"].replace("-", ""), r["url"], r["yururluk"])
            self.assertRegex(r["sha256"], r"^[0-9a-f]{64}$")
            self.assertIn(r["okuma"], ("metin", "goruntu"))
            # Alıntı iki oranı da RG'deki yazımla taşır ("16,50", "yüzde 9"): aktarım hatası yakalanır.
            sayilar = [float(x.replace(",", ".")) for x in re.findall(r"(?:%|yüzde)\s*(\d+(?:,\d+)?)", r["alinti"])]
            self.assertEqual(sayilar, [r["reeskont"], r["avans"]], r["yururluk"])
        self.assertEqual(len({r["sha256"] for r in VERI["faiz"]["tcmb"]}), len(TCMB))

    def test_2021_duzeltmesi(self):
        r = next(x for x in VERI["faiz"]["tcmb"] if x["yururluk"] == "2021-12-31")
        self.assertEqual((r["rg_tarih"], r["duzeltme"]["rg_tarih"], r["duzeltme"]["rg_sayi"]), ("2022-01-02", "2022-01-03", "31708"))
        self.assertIn("31/12/2021 tarihinden geçerli", r["duzeltme"]["alinti"])

    def test_kanuni_faiz_zinciri(self):
        k = VERI["faiz"]["kanuni"]
        self.assertEqual([(d["bas"], d.get("oran"), d["kaynak"]) for d in k],
                         [("2006-01-01", 9.0, "BKK2005_9831"), ("2024-06-01", 24.0, "CBK8485"), ("2026-07-31", None, "K7589")])
        self.assertEqual(k[2]["carpan"], 0.8)
        self.assertIn("yüzde sekseni", k[2]["alinti"])
        self.assertIn("% 9", k[0]["alinti"])
        self.assertIn("% 24", k[1]["alinti"])
        for kod, rg in (("BKK2005_9831", ("2005-12-30", "26039")), ("CBK8485", ("2024-05-21", "32552")),
                        ("K7589", ("2026-07-31", "33326"))):
            self.assertEqual((KAYNAK[kod]["rg_tarih"], KAYNAK[kod]["rg_sayi"]), rg)
            self.assertRegex(KAYNAK[kod]["sha256"], r"^[0-9a-f]{64}$")

    def test_kidem_tavani_sabit_ve_kesintisiz(self):
        t = VERI["iscilik"]["kidem_tavani"]
        self.assertEqual({r["bas"]: r["tutar_tl"] for r in t}, TAVAN)
        for onceki, sonraki in zip(t, t[1:]):
            self.assertEqual((date.fromisoformat(onceki["son"]) - date.fromisoformat(sonraki["bas"])).days, -1)
        for r in t:
            yazim = "{:,.2f}".format(r["tutar_tl"]).replace(",", "§").replace(".", ",").replace("§", ".")
            self.assertIn(yazim, r["alinti"], r["bas"])
        self.assertRegex(KAYNAK["CSGBKT"]["sha256"], r"^[0-9a-f]{64}$")

    def test_2026_tavanlari_katsayidan(self):
        """Tavan = (gösterge 1500 + ek gösterge 8000 + kıdem 500) × aylık katsayı + 1000 × taban katsayı
        + (1500 + 8000) × aylık katsayı × 2,15 (ÇSGB hesap sayfaları)."""
        for kat, taban, beklenen in (("1.387871", "22.722793", "64948.77"), ("1.575512", "25.794915", "73729.87")):
            k, tb = Decimal(kat), Decimal(taban)
            t = Decimal(10000) * k + Decimal(1000) * tb + Decimal(9500) * k * Decimal("2.15")
            self.assertEqual(t.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), Decimal(beklenen))

    def test_kodda_oran_ve_tavan_yok(self):
        for ad in ("faiz.py", "iscilik.py"):
            with open(os.path.join(KOK, ad), encoding="utf-8") as fh:
                kod = fh.read()
            for x in [r["avans"] for r in VERI["faiz"]["tcmb"]] + [r["reeskont"] for r in VERI["faiz"]["tcmb"]] \
                    + list(TAVAN.values()) + [24.0]:
                if x >= 10:
                    for yazim in ("%s" % x, "%.2f" % x):
                        self.assertNotRegex(kod, r"(?<![\w.])%s(?![\w])" % re.escape(yazim), (ad, yazim))

    def test_izlenen(self):
        iz = VERI["izlenen"]
        self.assertEqual(iz["kartlar"]["faiz"], ["K7589", "CBK8485", "TCMB20251220"])
        self.assertEqual(iz["kartlar"]["iscilik"], ["CSGBKT"])
        self.assertEqual({k: v["son"] for k, v in iz["gecerlilik"].items()}, {"faiz": "2026-12-31", "iscilik": "2026-12-31"})
        self.assertEqual(VERI["faiz"]["kapsam"]["son"], iz["gecerlilik"]["faiz"]["son"])
        self.assertEqual(VERI["iscilik"]["kidem_tavani"][-1]["son"], iz["gecerlilik"]["iscilik"]["son"])


class Oranlar(unittest.TestCase):
    def yy(self, yil, yari, tur="avans"):
        return float(faiz.yari_yil(yil, yari, tur)[0])

    def test_31_aralik_ve_30_haziran(self):
        self.assertEqual(self.yy(2006, 1), 25)
        self.assertEqual(self.yy(2009, 1), 27)
        self.assertEqual(self.yy(2009, 2), 19)          # 27 → 19: 8 puan, ikinci yarı 30 Haziran oranı
        self.assertEqual(self.yy(2012, 2), 17.75)       # 17,75 → 16,50: 1,25 puan, değişmez
        self.assertEqual(self.yy(2017, 1), 9.75)        # 31.12.2016 günü yürürlüğe giren oran o gün uygulanır
        self.assertEqual(self.yy(2018, 2), 19.5)
        self.assertEqual(self.yy(2020, 2), 13.75)       # 13,75 → 10: 3,75 puan, değişmez
        self.assertEqual(self.yy(2022, 1), 15.75)       # 31.12.2021 (düzeltmeyle)
        self.assertEqual(self.yy(2023, 2), 16.75)       # 10,75 → 16,75: 6 puan
        self.assertEqual(self.yy(2024, 1), 44.25)
        self.assertEqual(self.yy(2024, 2), 51.75)
        self.assertEqual(self.yy(2025, 2), 44.25)       # 49,25 → 44,25: tam 5 puan, "beş puan veya daha çok"
        self.assertEqual((self.yy(2026, 1), self.yy(2026, 2)), (39.75, 39.75))
        self.assertEqual(self.yy(2026, 1, "reeskont"), 38.75)

    def test_belirlenmemis_oran_yok(self):
        # 2027 ilk yarısı 31.12.2026 oranına bağlı; veri 07.11.2026'ya kadar teyitli: eski oran kesinmiş gibi verilmez.
        for tur in ("avans", "reeskont"):
            with self.assertRaises(faiz.HesapHatasi):
                faiz.yari_yil(2027, 1, tur)
        with self.assertRaises(faiz.HesapHatasi):
            faiz.kanuni_orani(date(2027, 1, 1))
        o = faiz.faiz("ticari", 1000, date(2026, 12, 1), date(2027, 1, 1))
        self.assertNotIn("bitis_orani", o)
        self.assertEqual(o["kalemler"][0]["oran"], 39.75)

    def test_kanuni(self):
        k = lambda g: float(faiz.kanuni_orani(g)[0])  # noqa: E731
        self.assertEqual(k(date(2006, 1, 1)), 9)
        self.assertEqual(k(date(2024, 5, 31)), 9)
        self.assertEqual(k(date(2024, 6, 1)), 24)
        self.assertEqual(k(date(2026, 7, 30)), 24)
        self.assertEqual(k(date(2026, 7, 31)), 31)      # 38,75 × %80
        self.assertIn("RG 20.12.2025, S. 33113", faiz.kanuni_orani(date(2026, 8, 1))[1])
        with self.assertRaises(faiz.HesapHatasi):
            faiz.kanuni_orani(date(2005, 12, 31))

    def test_ticari_kanuniden_az_olamaz(self):
        self.assertEqual(float(faiz.ticari_orani(date(2026, 9, 1))[0]), 39.75)
        with mock.patch.object(faiz, "kanuni_orani", lambda g: (Decimal(60), "kanuni dayanak", "k")):
            o, dayanak, _ = faiz.ticari_orani(date(2026, 9, 1))
        self.assertEqual(float(o), 60)
        self.assertIn("kanuni faiz uygulandı", dayanak)


class Faiz(unittest.TestCase):
    def test_ticari_2025(self):
        o = faiz.faiz("ticari", 100000, date(2025, 1, 1), date(2026, 1, 1))
        self.assertEqual([(k["gun"], k["oran"], k["tutar_tl"]) for k in o["kalemler"]],
                         [(181, 49.25, 24422.6), (184, 44.25, 22306.85)])
        self.assertEqual((o["toplam_tl"], o["anapara_ile_tl"], o["gun"]), (46729.45, 146729.45, 365))
        self.assertIn("RG 28.12.2024, S. 32766", o["kalemler"][0]["dayanak"])
        self.assertIn("RG 08.03.2025, S. 32835", o["kalemler"][1]["dayanak"])
        self.assertEqual(o["toplam_etiketi"], "İşlemiş faiz")

    def test_kanuni_uc_donem(self):
        o = faiz.faiz("kanuni", 100000, date(2024, 1, 1), date(2026, 11, 7))
        self.assertEqual([(k["bas"], k["son"], k["gun"], k["oran"], k["tutar_tl"]) for k in o["kalemler"]], [
            ("2024-01-01", "2024-05-31", 152, 9.0, 3747.95),
            ("2024-06-01", "2026-07-30", 790, 24.0, 51945.21),
            ("2026-07-31", "2026-11-06", 99, 31.0, 8408.22)])
        self.assertEqual(o["toplam_tl"], 64101.38)
        self.assertEqual(o["bitis_orani"]["oran"], 31.0)
        self.assertTrue(any("7589" in t for t in o["teyit_edin"]))

    def test_ayni_oran_birlesir(self):
        o = faiz.faiz("kanuni", 1000, date(2006, 1, 1), date(2024, 1, 1))
        self.assertEqual(len(o["kalemler"]), 1)
        self.assertEqual(o["kalemler"][0]["gun"], (date(2024, 1, 1) - date(2006, 1, 1)).days)
        o = faiz.faiz("ticari", 1000, date(2015, 1, 1), date(2017, 1, 1))     # 2015-2016: 14.12.2014 tebliği
        self.assertEqual([(k["oran"], k["gun"]) for k in o["kalemler"]], [(10.5, 731)])

    def test_sabit(self):
        o = faiz.faiz("sabit", 50000, date(2020, 2, 28), date(2020, 3, 1), oran=36)
        self.assertEqual((o["kalemler"][0]["gun"], o["toplam_tl"]), (2, 98.63))   # artık yılda da 365
        self.assertNotIn("bitis_orani", o)
        self.assertTrue(any("TBK" in n or "BORÇLAR" in n for n in o["notlar"]))

    def test_hatalar(self):
        H = faiz.HesapHatasi
        for args in [("ticari", 1000, date(2025, 1, 2), date(2025, 1, 2)), ("ticari", 0, date(2025, 1, 1), date(2025, 2, 1)),
                     ("kanuni", 1000, date(2005, 12, 31), date(2006, 2, 1)),
                     ("kanuni", 1000, date(2026, 1, 1), date(2027, 1, 2)), ("sabit", 1000, date(2026, 1, 1), date(2026, 2, 1)),
                     ("uydurma", 1000, date(2026, 1, 1), date(2026, 2, 1))]:
            with self.assertRaises(H, msg=str(args)):
                faiz.faiz(*args)
        with self.assertRaises(H):
            faiz.faiz("kanuni", 1000, date(2026, 1, 1), date(2026, 2, 1), oran=10)
        self.assertEqual(faiz.faiz("kanuni", 1000, date(2026, 12, 1), date(2027, 1, 1))["gun"], 31)


class Iscilik(unittest.TestCase):
    def test_hizmet_suresi(self):
        s = iscilik.hizmet_suresi(date(2020, 3, 15), date(2026, 9, 30))
        self.assertEqual((s["yil"], s["ay"], s["gun"], s["yildan_artan_gun"]), (6, 6, 16, 200))
        s = iscilik.hizmet_suresi(date(2025, 1, 1), date(2025, 12, 31))
        self.assertEqual((s["yil"], s["ay"], s["gun"]), (1, 0, 0))
        s = iscilik.hizmet_suresi(date(2024, 2, 29), date(2025, 2, 27))
        self.assertEqual((s["yil"], s["ay"]), (1, 0))

    def test_ihbar_sinirlari(self):
        hafta = lambda g, c: iscilik.ihbar_bendi(iscilik.hizmet_suresi(g, c))["hafta"]  # noqa: E731
        self.assertEqual(hafta(date(2025, 1, 1), date(2025, 6, 29)), 2)      # 5 ay 29 gün
        self.assertEqual(hafta(date(2025, 1, 1), date(2025, 6, 30)), 4)      # tam 6 ay: "altı aydan az" değil
        self.assertEqual(hafta(date(2025, 1, 1), date(2026, 6, 30)), 4)      # tam 18 ay: "birbuçuk yıla kadar"
        self.assertEqual(hafta(date(2025, 1, 1), date(2026, 7, 1)), 6)
        self.assertEqual(hafta(date(2023, 1, 1), date(2025, 12, 31)), 6)     # tam 36 ay
        self.assertEqual(hafta(date(2023, 1, 1), date(2026, 1, 1)), 8)

    def test_kidem_tavanli(self):
        o = iscilik.iscilik("kidem_ihbar", date(2020, 3, 15), date(2026, 9, 30), 100000, 5000)
        self.assertEqual([k["tutar_tl"] for k in o["kalemler"]], [482779.15, 196000.0])
        self.assertEqual(o["toplam_tl"], 678779.15)
        self.assertTrue(o["kidem"]["tavan_uygulandi"])
        self.assertEqual(o["kidem"]["tavan_tl"], 73729.87)
        self.assertIn("1475", o["kalemler"][0]["dayanak"])
        self.assertIn("4857", o["kalemler"][1]["dayanak"])
        self.assertEqual(o["toplam_etiketi"], "Toplam (brüt)")

    def test_kidem_tavan_alti_ve_donemi(self):
        o = iscilik.iscilik("kidem", date(2025, 1, 1), date(2026, 6, 30), 30000)
        self.assertEqual(o["kalemler"][0]["tutar_tl"], 44876.71)          # 30.000 × (1 + 181/365)
        self.assertFalse(o["kidem"]["tavan_uygulandi"])
        self.assertEqual(o["kidem"]["tavan_donemi"], ["2026-01-01", "2026-06-30"])
        o = iscilik.iscilik("kidem", date(2019, 1, 1), date(2023, 12, 31), 50000)
        self.assertEqual(o["kalemler"][0]["tutar_tl"], 117449.15)         # 5 yıl × 23.489,83 (tavan)

    def test_bir_yildan_kisa(self):
        o = iscilik.iscilik("kidem_ihbar", date(2025, 3, 1), date(2025, 12, 31), 30000)
        self.assertEqual([k["kalem"] for k in o["kalemler"]], ["İhbar tazminatı (brüt, 4 hafta)"])
        self.assertTrue(any("bir yıldan kısa" in n for n in o["notlar"]))

    def test_hatalar(self):
        H = iscilik.HesapHatasi
        with self.assertRaises(H):
            iscilik.iscilik("kidem", date(2026, 1, 1), date(2025, 1, 1), 30000)
        with self.assertRaises(H):
            iscilik.iscilik("kidem", date(2010, 1, 1), date(2015, 12, 31), 30000)    # tavan verisi 2016'dan
        with self.assertRaises(H):
            iscilik.iscilik("kidem", date(2020, 1, 1), date(2027, 1, 1), 30000)     # tavan verisi 2026 sonuna kadar
        with self.assertRaises(H):
            iscilik.iscilik("kidem", date(2020, 1, 1), date(2025, 1, 1), 0)
        self.assertEqual(iscilik.iscilik("ihbar", date(2010, 1, 1), date(2015, 12, 31), 30000)["toplam_tl"], 56000.0)

    def test_teyit_listesi_isleme_gore(self):
        k = iscilik.iscilik("kidem", date(2020, 1, 1), date(2025, 1, 1), 30000)["teyit_edin"]
        i = iscilik.iscilik("ihbar", date(2020, 1, 1), date(2025, 1, 1), 30000)["teyit_edin"]
        self.assertTrue(any("kıdem tavanı" in x for x in k))
        self.assertFalse(any("kıdem tavanı" in x for x in i))
        self.assertTrue(any("birbuçuk" in x for x in i))
        self.assertTrue(all(x.startswith("İşçilik:") for x in k + i))


class Arac(unittest.TestCase):
    def test_turkce_girdi(self):
        o = json.loads(server._t_faiz({"faiz_turu": "ticari", "anapara": "100.000,00", "baslangic": "01.01.2025",
                                       "bitis": "2026-01-01"}))
        self.assertEqual(o["toplam_tl"], 46729.45)
        o = json.loads(server._t_iscilik({"giris": "15.03.2020", "cikis": "2026-09-30", "brut_ucret": "100.000",
                                          "ek_menfaat": "5.000,00"}))
        self.assertEqual(o["toplam_tl"], 678779.15)

    def test_hatalar(self):
        for args in ({"faiz_turu": "ticari", "anapara": "1000", "baslangic": "31.02.2025", "bitis": "2026-01-01"},
                     {"faiz_turu": "ticari", "anapara": "-5", "baslangic": "2025-01-01", "bitis": "2026-01-01"},
                     {"faiz_turu": "kanuni", "anapara": "1000", "baslangic": "dün", "bitis": "2026-01-01"}):
            with self.assertRaises(McpError):
                server._t_faiz(args)
        with self.assertRaises(McpError):
            server._t_iscilik({"giris": "2026-01-01", "cikis": "2025-01-01", "brut_ucret": 1000})

    def test_menu(self):
        m = json.loads(server._t_kalemler({}))
        self.assertEqual({i["kod"] for i in m["faiz"]["islemler"]}, {"kanuni", "ticari", "sabit"})
        self.assertEqual({i["kod"] for i in m["iscilik"]["islemler"]}, {"kidem_ihbar", "kidem", "ihbar"})
        sabit = next(i for i in m["faiz"]["islemler"] if i["kod"] == "sabit")
        self.assertEqual([g["tur"] for g in sabit["girdiler"]], ["tutar", "tarih", "tarih", "oran"])
        self.assertEqual(list(json.loads(server._t_kalemler({"kart": "iscilik"}))), ["UYARI", "tazelik", "surum", "iscilik", "veri"])

    def test_aciklamalarda_kural(self):
        for t in server.TOOLS:
            self.assertIn("tazelik satırını", t.description)
        self.assertNotIn("Faiz, işçilik alacağı ve süre hesabı bu araçlarda yok", server.INSTRUCTIONS)


class Tazelik(unittest.TestCase):
    def tearDown(self):
        tazelik.kur(tazelik.Nobetci(VERI, getir=None))

    def test_desenler(self):
        n, _ = kur(SahteRG())
        d = {x.kod: x for x in n._desenler}
        uy = lambda kod, baslik, bolum="": d[kod].uyar(tazelik.katla(bolum), tazelik.katla(baslik))  # noqa: E731
        self.assertTrue(uy("tcmb_faiz", "Türkiye Cumhuriyet Merkez Bankasınca, Reeskont ve Avans İşlemlerinde Uygulanacak "
                                        "Yıllık Faiz Oranları Hakkında Tebliğ"))
        self.assertTrue(uy("tcmb_faiz", "Reeskont ve Avans İşlemlerinde Uygulanacak Faiz Oranlarının Tespiti"))
        self.assertTrue(uy("tcmb_faiz", "Türkiye Cumhuriyet Merkez Bankasınca, Reeskoont ve Avans İşlemlerinde Uygulanacak "
                                        "Yıllık Faiz Oranları Hakkında Tebliğ"))
        self.assertTrue(uy("kanuni_faiz", "3095 Sayılı Kanuni Faiz ve Temerrüt Faizine İlişkin Kanunun 1 inci Maddesinde "
                                          "Düzenlenen Kanuni Faiz Oranının ... Hakkında Karar (Karar Sayısı: 8485)"))
        self.assertFalse(uy("kanuni_faiz", "Mal ve Hizmet Tedarikinde Geç Ödemelerde Uygulanacak Temerrüt Faiz Oranı ve "
                                           "Alacağın Tahsili Masrafları İçin Talep Edilebilecek Asgari Giderim Tutarı Hakkında Tebliğ"))
        self.assertTrue(uy("is_kanunu", "İş Kanunu ile Bazı Kanunlarda Değişiklik Yapılmasına Dair Kanun"))
        self.assertFalse(uy("is_kanunu", "Vergi Usul Kanunu ile Bazı Kanunlarda Değişiklik Yapılmasına Dair Kanun"))
        self.assertTrue(uy("kidem", "Kıdem Tazminatı Fonu Kanunu"))
        self.assertTrue(uy("hakem_kurulu", "Kamu Görevlileri Hakem Kurulunun 2027 Yılı Kararı"))
        self.assertTrue(uy("k5434", "Türkiye Cumhuriyeti Emekli Sandığı Kanunu ile Bazı Kanunlarda Değişiklik Yapılmasına Dair Kanun"))

    def test_tcmb_tebligi_yalniz_faizi_kilitler(self):
        g = date(2026, 11, 7)
        tazelik.kur(kur(SahteRG({tazelik.gun_url(g): (200, sayfa(g, [
            ("TEBLİĞLER", "Türkiye Cumhuriyet Merkez Bankasınca, Reeskont ve Avans İşlemlerinde Uygulanacak Yıllık Faiz "
                          "Oranları Hakkında Tebliğ", "20261107-9.htm")]))}))[0])
        f = json.loads(server._t_faiz({"faiz_turu": "ticari", "anapara": 1000, "baslangic": "2025-01-01", "bitis": "2025-02-01"}))
        self.assertEqual(f["tazelik"]["durum"], "yeni_duzenleme")
        self.assertIsNone(f["toplam_tl"])
        self.assertIsInstance(f["teyit_bekleyen_tutarlar"]["toplam_tl"], float)
        i = json.loads(server._t_iscilik({"giris": "2020-01-01", "cikis": "2025-01-01", "brut_ucret": 30000}))
        self.assertEqual(i["tazelik"]["durum"], "guncel")
        v = json.loads(server._t_vekalet({"islem": "maktu", "kalem": "2.2.10"}))
        self.assertEqual(v["tazelik"]["durum"], "guncel")

    def test_gecerlilik_sonrasi_kilit(self):
        n, saat = kur(SahteRG(), an=datetime(2027, 1, 2, 10, 0, tzinfo=tazelik.TR))
        tazelik.kur(n)
        f = json.loads(server._t_faiz({"faiz_turu": "kanuni", "anapara": 1000, "baslangic": "2026-01-01", "bitis": "2026-02-01"}))
        self.assertEqual(f["tazelik"]["durum"], "yeni_duzenleme")
        self.assertTrue(f["UYARI"].startswith("UYARI: Faiz oranı verisi 31.12.2026 tarihine kadar"))
        self.assertIn("tcmb.gov.tr", f["UYARI"])
        self.assertIsNone(f["toplam_tl"])
        i = json.loads(server._t_iscilik({"giris": "2020-01-01", "cikis": "2025-01-01", "brut_ucret": 30000}))
        self.assertIn("Kıdem tazminatı tavanı", i["UYARI"])
        v = json.loads(server._t_vekalet({"islem": "maktu", "kalem": "2.2.10"}))   # vekâlet kartının geçerlilik tarihi yok
        self.assertEqual(v["tazelik"]["durum"], "guncel")

    def test_iscilik_kaynak_satiri(self):
        tazelik.kur(kur(SahteRG())[0])
        i = json.loads(server._t_iscilik({"giris": "2020-01-01", "cikis": "2025-01-01", "brut_ucret": 30000}))
        self.assertTrue(i["tazelik"]["satir"].startswith("Kaynak: ÇSGB kıdem tazminatı tavan tablosu"))
        tazelik.kur(kur(SahteRG(hata="timed out"))[0])
        i = json.loads(server._t_iscilik({"giris": "2020-01-01", "cikis": "2025-01-01", "brut_ucret": 30000}))
        self.assertIn("verideki resmî kaynağa göredir", i["UYARI"])

    def test_nobetci_donem_issue(self):
        s = tazelik.komut(["--gun", "1"], getir=SahteRG(), saat=lambda: datetime(2027, 1, 2, 9, 0, tzinfo=tazelik.TR),
                          uyu=lambda x: None)
        self.assertEqual(s["durum"], "yeni_duzenleme")
        donem = [y for y in s["yeni"] if y.get("tur") == "donem"]
        self.assertEqual(sorted(k for y in donem for k in y["kartlar"]), ["faiz", "iscilik"])
        ac, _ = nobetci.planla(s, {}, [])
        self.assertEqual(len(ac), 2)
        self.assertTrue(all("geçerlilik tarihini geçti" in i["body"] for i in ac))
        self.assertEqual(nobetci.planla(s, {}, [{"title": i["title"], "state": "CLOSED"} for i in ac])[0], [])


if __name__ == "__main__":
    unittest.main()
