"""Tazelik kilidi ve Resmî Gazete nöbetçisi testleri (ağsız: fihrist getiricisi sahte HTML döndürür).

    python -m unittest discover -s hesap-mcp/tests      # depo kökünden
"""

import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)

import nobetci  # noqa: E402
import server  # noqa: E402
import tazelik  # noqa: E402

VERI = tazelik.veri_yukle()
AAUT_DEGISIKLIK = "Avukatlık Asgari Ücret Tarifesinde Değişiklik Yapılmasına Dair Tarife"


def sayfa(gun, kalemler=(), mukerrerler=(), mukerrer=0, sayi="33423"):
    """Resmî Gazete günlük fihristi biçiminde sayfa (windows-1254). kalemler: (bölüm, başlık, bağlantı)."""
    ek = " - %d. Mükerrer" % mukerrer if mukerrer else ""
    p = ["<html><head><title>T.C. Resmî Gazete</title></head><body>",
         "<p align=center><b><span> %d Kasım %d Tarihli ve %s Sayılı Resmî\n Gazete%s</span></b></p>"
         % (gun.day, gun.year, sayi, ek),
         '<p class="MsoNormal"><b><u><font>YÜRÜTME VE İDARE BÖLÜMÜ</font></u></b></p>']
    bolum = None
    for b, baslik, href in kalemler:
        if b != bolum:
            p.append('<p class="MsoNormal"><span class="Normal1"><b><u><font>%s</font></u></b></span></p>' % b)
            bolum = b
        p.append('<p class="MsoNormal"><span class="Normal1"><font size="1"><a href="%s" style="text-decoration: '
                 'none">––&nbsp; %s</a></font></span></p>' % (href, baslik))
    for n in mukerrerler:
        p.append('<p><a href="http://www.resmigazete.gov.tr/eskiler/%04d/%02d/%s%sM%d.htm"><marquee>%s tarihli %d. '
                 'mükerrer sayılı Resmî Gazete’de Tebliğler yayımlanmıştır.</marquee></a></p>'
                 % (gun.year, gun.month, gun.strftime("%Y%m%d"), "", n, gun.strftime("%d/%m/%Y"), n))
    p.append("</body></html>")
    return "\n".join(p).encode("cp1254")


SIRADAN = [("YÖNETMELİKLER", "Orman Köylülerine Yapılacak Dağıtımın Uygulamasına Dair Yönetmelik", "X-1.htm"),
           ("TEBLİĞLER", "Yurt Dışına Çıkış Harcı Uygulama Genel Tebliğinde Değişiklik Yapılmasına Dair Tebliğ",
            "X-2.pdf"),
           ("CUMHURBAŞKANI KARARI", "Harcama Yetkilileri Hakkında Karar (Karar Sayısı: 1)", "X-3.pdf")]


class Saat:
    def __init__(self, an):
        self.an = an

    def __call__(self):
        return self.an

    def ilerle(self, **kw):
        self.an = self.an + timedelta(**kw)


class SahteRG:
    """URL → (durum, gövde). Tanımsız gün sıradan bir fihristtir; `hata` doluysa her istek ağ hatasıdır."""

    def __init__(self, ozel=None, hata=None, gecikme=0.0):
        self.ozel = dict(ozel or {})
        self.hata = hata
        self.gecikme = gecikme
        self.istenen = []
        self._kilit = threading.Lock()

    def __call__(self, url, zaman_asimi):
        with self._kilit:
            self.istenen.append(url)
        if self.gecikme:
            time.sleep(self.gecikme)
        if self.hata:
            raise tazelik.ErisimHatasi(self.hata)
        if url in self.ozel:
            return self.ozel[url]
        ad = url.rsplit("/", 1)[-1]
        g = datetime.strptime(ad[:8], "%Y%m%d").date()
        kalemler = [(b, t, ad[:8] + h[1:]) for b, t, h in SIRADAN]
        return 200, sayfa(g, kalemler)


def kur(rg, an=datetime(2026, 11, 7, 10, 0, tzinfo=tazelik.TR), veri=None, **kw):
    saat = Saat(an)
    uykular = []
    n = tazelik.Nobetci(veri or VERI, getir=rg, saat=saat, uyu=uykular.append, **kw)
    n.uykular = uykular
    return n, saat


class Fihrist(unittest.TestCase):
    def test_katlama(self):
        self.assertEqual(tazelik.katla("  HARÇLAR Kanunu  İçin ÜCRET  "), "harclar kanunu icin ucret")
        self.assertEqual(tazelik.katla("AVUKATLIK Asgarî"), "avukatlik asgari")

    def test_ayristirma(self):
        g = date(2026, 11, 8)
        url = tazelik.gun_url(g)
        a = tazelik.fihrist_ayristir(tazelik._coz(sayfa(g, [("TEBLİĞLER", AAUT_DEGISIKLIK, "20261108-4.htm")],
                                                        mukerrerler=[1, 2])), url, g)
        self.assertEqual(a["sayi"], "33423")
        self.assertEqual(a["mukerrerler"], [1, 2])
        self.assertEqual(a["kalemler"], [{"bolum": "TEBLİĞLER", "baslik": AAUT_DEGISIKLIK,
                                          "url": "https://www.resmigazete.gov.tr/eskiler/2026/11/20261108-4.htm"}])

    def test_tanınmayan_sayfa_fihrist_sayilmaz(self):
        g = date(2026, 11, 8)
        with self.assertRaises(tazelik.ErisimHatasi):
            tazelik.fihrist_ayristir("<html><body><p>Erişim engellendi</p></body></html>", tazelik.gun_url(g), g)

    def test_yalniz_resmi_gazete_adresi(self):
        self.assertTrue(tazelik.rg_adresi("https://www.resmigazete.gov.tr/eskiler/2026/11/20261107.htm"))
        for url in ("http://www.resmigazete.gov.tr/x", "https://resmigazete.gov.tr.ornek.com/x",
                    "https://www.resmigazete.gov.tr:8443/x", "https://kullanici@www.resmigazete.gov.tr/x"):
            self.assertFalse(tazelik.rg_adresi(url), url)
        with self.assertRaises(tazelik.ErisimHatasi):
            tazelik.http_getir("https://ornek.com/")

    def test_desenler(self):
        n, _ = kur(SahteRG())
        d = {x.kod: x for x in n._desenler}
        uy = lambda kod, baslik, bolum="": d[kod].uyar(tazelik.katla(bolum), tazelik.katla(baslik))  # noqa: E731
        self.assertTrue(uy("aaut", AAUT_DEGISIKLIK))
        self.assertTrue(uy("harc_teblig", "Harçlar Kanunu Genel Tebliği (Seri No: 100)"))
        self.assertTrue(uy("harc_cbk", "Maktu Harç Tutarlarının Yeniden Belirlenmesi Hakkında Karar (Karar Sayısı: 9)"))
        self.assertTrue(uy("harc_cbk", "492 Sayılı Harçlar Kanununa Bağlı Tarifeler Hakkında Karar", "CUMHURBAŞKANI KARARI"))
        self.assertFalse(uy("harc_cbk", "Yurt Dışına Çıkış Harcı Tutarı Hakkında Karar (Karar Sayısı: 2)"))
        self.assertFalse(uy("harc_cbk", "Harcama Yetkilileri Hakkında Karar (Karar Sayısı: 1)"))
        self.assertFalse(uy("harc_cbk", "Harç Tutarları Hakkında Tebliğ", "TEBLİĞLER"))
        self.assertTrue(uy("gider_avansi", "Hukuk Muhakemeleri Kanunu Gider Avansı Tarifesi"))
        self.assertTrue(uy("k492", "Harçlar Kanunu ile Bazı Kanunlarda Değişiklik Yapılmasına Dair Kanun"))
        self.assertTrue(uy("k1136", "Avukatlık Kanunu ile Bazı Kanunlarda Değişiklik Yapılmasına Dair Kanun"))
        self.assertFalse(uy("k1136", "Avukatlık Kanunu Hakkında Bilgilendirme"))


class Durumlar(unittest.TestCase):
    def test_guncel(self):
        rg = SahteRG()
        n, _ = kur(rg)
        t = n.tazelik("vekalet")
        self.assertEqual(t["durum"], "guncel")
        self.assertEqual(t["toplam"], "kesin")
        self.assertIsNone(t["uyari"])
        self.assertEqual(t["yeni"], [])
        self.assertEqual(t["veri_kaynaklari"], ["Avukatlık Asgari Ücret Tarifesi, RG 03.11.2026, S. 33419"])
        self.assertEqual(t["son_kontrol"], "2026-11-07T10:00+03:00")
        self.assertEqual(t["satir"], "Kaynak: RG 03.11.2026, S. 33419 · son kontrol: 07.11.2026 10:00")
        self.assertEqual(rg.istenen, ["https://www.resmigazete.gov.tr/eskiler/2026/11/20261107.htm"])
        h = n.tazelik("harc")
        self.assertEqual(h["satir"], "Kaynak: RG 31.12.2025, S. 33124 (5. Mükerrer); RG 28.10.2025, S. 33061 · "
                                     "son kontrol: 07.11.2026 10:00")

    def test_yeni_duzenleme_mukerrerde(self):
        g = date(2026, 11, 7)
        rg = SahteRG({
            tazelik.gun_url(g): (200, sayfa(g, [SIRADAN[0]], mukerrerler=[1])),
            tazelik.gun_url(g, 1): (200, sayfa(g, [("TEBLİĞLER", AAUT_DEGISIKLIK, "20261107M1-3.pdf")], mukerrer=1)),
        })
        n, _ = kur(rg)
        t = n.tazelik("vekalet")
        self.assertEqual(t["durum"], "yeni_duzenleme")
        self.assertEqual(t["toplam"], "verilmedi")
        bag = "https://www.resmigazete.gov.tr/eskiler/2026/11/20261107M1-3.pdf"
        self.assertEqual(t["uyari"], "UYARI: 07.11.2026 tarihli Resmî Gazete'de bu hesabı etkileyebilecek yeni "
                                     "düzenleme var, teyidiniz gerekli: " + bag)
        self.assertEqual(t["yeni"][0]["rg"], "RG 07.11.2026, S. 33423 (1. Mükerrer)")
        self.assertEqual(t["yeni"][0]["url"], bag)
        self.assertEqual(n.tazelik("harc")["durum"], "guncel")        # AAÜT harç kartını kilitlemez
        self.assertEqual(len(rg.istenen), 2)

    def test_bilinen_yayim_kilitlemez(self):
        g = date(2026, 11, 3)
        rg = SahteRG({tazelik.gun_url(g): (200, sayfa(g, [("TEBLİĞLER", "Avukatlık Asgari Ücret Tarifesi",
                                                                "20261103-3.htm")], sayi="33419"))})
        veri = copy.deepcopy(VERI)
        veri["derlenme_tarihi"] = "2026-11-01"
        n, _ = kur(rg, veri=veri)
        self.assertEqual(n.tazelik("vekalet")["durum"], "guncel")
        self.assertEqual(n._gunler[g]["kalemler"][0]["desenler"], ["aaut"])   # desene uydu, bilinen olduğu için elendi

    def test_kontrol_edilemedi_ve_yeniden_deneme(self):
        rg = SahteRG(hata="TimeoutError: timed out")
        n, saat = kur(rg)
        t = n.tazelik("vekalet")
        self.assertEqual(t["durum"], "kontrol_edilemedi")
        self.assertEqual(t["toplam"], "teyit edilmedi")
        self.assertEqual(t["uyari"], "UYARI: Resmî Gazete'ye bugün ulaşılamadı; tutarlar 03.11.2026 tarihli resmî "
                                     "metne göredir, teyidiniz gerekli: https://www.resmigazete.gov.tr/")
        self.assertIn("TimeoutError", t["neden"])
        self.assertIn("teyit edilmedi", t["satir"])
        self.assertIn("31.12.2025 ve 28.10.2025 tarihli resmî metinlere", n.tazelik("harc")["uyari"])
        self.assertEqual(len(rg.istenen), 1)       # ilk hatada tarama durur
        saat.ilerle(minutes=10)
        n.tazelik("vekalet")
        self.assertEqual(len(rg.istenen), 1)       # 30 dk dolmadan yeniden denemez
        saat.ilerle(minutes=25)
        rg.hata = None
        t = n.tazelik("vekalet")
        self.assertEqual(len(rg.istenen), 2)
        self.assertEqual(t["durum"], "guncel")

    def test_fihrist_olmayan_sayfa_ve_404(self):
        g = date(2026, 11, 7)
        n, _ = kur(SahteRG({tazelik.gun_url(g): (200, b"<html><body>Bakim</body></html>")}))
        self.assertEqual(n.tazelik("harc")["durum"], "kontrol_edilemedi")
        n, _ = kur(SahteRG({tazelik.gun_url(g): (404, b"")}))
        self.assertEqual(n.tazelik("harc")["durum"], "kontrol_edilemedi")
        n, _ = kur(SahteRG({tazelik.gun_url(g): (503, b"")}))
        self.assertIn("HTTP 503", n.tazelik("harc")["neden"])

    def test_kapali(self):
        n = tazelik.Nobetci(VERI, getir=None)
        t = n.tazelik("vekalet")
        self.assertEqual(t["durum"], "kontrol_edilemedi")
        self.assertTrue(t["uyari"].startswith("UYARI: Resmî Gazete denetimi bu kurulumda kapalı;"))


class Onbellek(unittest.TestCase):
    def test_24_saat_ve_gun_donumu(self):
        rg = SahteRG()
        n, saat = kur(rg)
        n.tazelik("vekalet")
        self.assertEqual(len(rg.istenen), 1)
        for _ in range(5):                         # aynı gün, 24 saat içinde: yeni istek yok
            saat.ilerle(hours=2)
            self.assertEqual(n.tazelik("vekalet")["durum"], "guncel")
        self.assertEqual(len(rg.istenen), 1)
        saat.ilerle(hours=4)                       # 08.11 00:00: bugünkü sayı taranmadı
        n.tazelik("vekalet")
        self.assertEqual(rg.istenen[1:], [tazelik.gun_url(date(2026, 11, 7)), tazelik.gun_url(date(2026, 11, 8))])
        saat.ilerle(hours=23, minutes=59)          # 24 saat dolmadı, aynı gün
        n.tazelik("vekalet")
        self.assertEqual(len(rg.istenen), 3)
        saat.ilerle(minutes=2)                     # 09.11: dünkü sayı yeniden, 07.11 bitmiş sayılır
        n.tazelik("harc")
        self.assertEqual(rg.istenen[3:], [tazelik.gun_url(date(2026, 11, 8)), tazelik.gun_url(date(2026, 11, 9))])

    def test_24_saatten_eski_basari(self):
        rg = SahteRG()
        n, saat = kur(rg)
        n.tazelik("vekalet")
        n._gunler[date(2026, 11, 8)] = {"durum": "tamam", "kesin": False, "kalemler": [], "sayi": "1"}
        saat.ilerle(hours=24)                      # bugünkü sayı önbellekte, ama son başarı 24 saat önce
        n.tazelik("vekalet")
        self.assertEqual(len(rg.istenen), 3)

    def test_bulgu_kalici(self):
        g = date(2026, 11, 8)
        rg = SahteRG({tazelik.gun_url(g): (200, sayfa(g, [("TEBLİĞLER", AAUT_DEGISIKLIK, "20261108-4.htm")]))})
        n, saat = kur(rg, an=datetime(2026, 11, 8, 9, 0, tzinfo=tazelik.TR))
        self.assertEqual(n.tazelik("vekalet")["durum"], "yeni_duzenleme")
        saat.ilerle(days=3)                        # aralık kaydı: bulgu süreç boyunca kalır
        t = n.tazelik("vekalet")
        self.assertEqual(t["durum"], "yeni_duzenleme")
        self.assertEqual(t["yeni"][0]["tarih"], "2026-11-08")

    def test_yeniden_baslama_en_cok_8_gun(self):
        veri = copy.deepcopy(VERI)
        veri["derlenme_tarihi"] = "2026-10-01"
        rg = SahteRG()
        n, _ = kur(rg, an=datetime(2026, 11, 20, 12, 0, tzinfo=tazelik.TR), veri=veri)
        n.tazelik("harc")
        self.assertEqual(len(rg.istenen), 8)
        self.assertEqual(rg.istenen[0], tazelik.gun_url(date(2026, 11, 13)))
        self.assertGreaterEqual(min(n.uykular), 0.0)
        self.assertEqual(len(n.uykular), 7)        # her istek arasında bekleme

    def test_gunluk_istek_siniri(self):
        veri = copy.deepcopy(VERI)
        veri["derlenme_tarihi"] = "2026-10-01"
        rg = SahteRG()
        n, saat = kur(rg, an=datetime(2026, 11, 20, 12, 0, tzinfo=tazelik.TR), veri=veri, gunluk_istek=5)
        t = n.tazelik("harc")
        self.assertEqual(t["durum"], "kontrol_edilemedi")
        self.assertIn("Günlük istek sınırı", t["neden"])
        self.assertEqual(len(rg.istenen), 5)
        saat.ilerle(hours=12)                      # ertesi gün kalan günler taranır, bitenler istenmez
        self.assertEqual(n.tazelik("harc")["durum"], "guncel")
        self.assertEqual(len(rg.istenen), 5 + 4)   # 13.11 aralıktan çıktı

    def test_ayni_anda_tek_tarama(self):
        rg = SahteRG(gecikme=0.05)
        n, _ = kur(rg)
        sonuc = []
        isler = [threading.Thread(target=lambda: sonuc.append(n.tazelik("vekalet")["durum"])) for _ in range(6)]
        for i in isler:
            i.start()
        for i in isler:
            i.join()
        self.assertEqual(sonuc, ["guncel"] * 6)
        self.assertEqual(len(rg.istenen), 1)


class Kilit(unittest.TestCase):
    def tearDown(self):
        tazelik.kur(None)

    def _vekalet(self):
        return json.loads(server._t_vekalet({"islem": "nispi_dava", "tutar": "1.000.000,00", "mahkeme": "2.2.10"}))

    def test_guncel_toplam_verilir(self):
        tazelik.kur(kur(SahteRG())[0])
        o = self._vekalet()
        self.assertEqual(list(o)[0], "tazelik")
        self.assertEqual(o["tazelik"]["durum"], "guncel")
        self.assertIsInstance(o["asgari_tl"], float)
        h = json.loads(server._t_harc({"islem": "karar_harci", "deger": "100000"}))
        self.assertIsInstance(h["toplam_tl"], float)

    def test_yeni_duzenlemede_toplam_null(self):
        g = date(2026, 11, 7)
        tazelik.kur(kur(SahteRG({tazelik.gun_url(g): (200, sayfa(g, [
            ("TEBLİĞLER", "Harçlar Kanunu Genel Tebliği (Seri No: 100)", "20261107-9.pdf")]))}))[0])
        h = json.loads(server._t_harc({"islem": "karar_harci", "deger": "100000"}))
        self.assertEqual(list(h)[:2], ["UYARI", "tazelik"])
        self.assertTrue(h["UYARI"].startswith("UYARI: 07.11.2026 tarihli Resmî Gazete'de"))
        self.assertIsNone(h["toplam_tl"])
        self.assertNotIn("kalemler", h)
        b = h["teyit_bekleyen_tutarlar"]
        self.assertIn("kesin değildir", b["not"])
        self.assertIsInstance(b["toplam_tl"], float)
        self.assertTrue(b["kalemler"])
        o = self._vekalet()                        # harç tebliği vekâlet kartını kilitlemez
        self.assertEqual(o["tazelik"]["durum"], "guncel")
        self.assertIsInstance(o["asgari_tl"], float)
        m = json.loads(server._t_kalemler({}))     # menü kilitlenmez, uyarıyı taşır
        self.assertEqual(m["tazelik"]["durum"], "yeni_duzenleme")
        self.assertIn("UYARI", m)
        self.assertIn("vekalet", m)

    def test_vekalette_asgari_null(self):
        g = date(2026, 11, 7)
        tazelik.kur(kur(SahteRG({tazelik.gun_url(g): (200, sayfa(g, [("TEBLİĞLER", AAUT_DEGISIKLIK, "x.htm")]))}))[0])
        o = self._vekalet()
        self.assertIsNone(o["asgari_tl"])
        self.assertIsNone(o["toplam_tl"])
        self.assertIsInstance(o["teyit_bekleyen_tutarlar"]["asgari_tl"], float)

    def test_kontrol_edilemedide_toplam_isaretli(self):
        tazelik.kur(kur(SahteRG(hata="URLError: timed out"))[0])
        o = self._vekalet()
        self.assertEqual(list(o)[:2], ["UYARI", "tazelik"])
        self.assertEqual(o["tazelik"]["toplam"], "teyit edilmedi")
        self.assertIsInstance(o["asgari_tl"], float)

    def test_aciklamalarda_kural(self):
        for t in server.TOOLS:
            self.assertIn("tazelik satırını", t.description)
        self.assertIn("TAZELİK", server.INSTRUCTIONS)


class KomutVeIssue(unittest.TestCase):
    def test_komut_son_3_gun(self):
        g = date(2026, 11, 6)
        rg = SahteRG({tazelik.gun_url(g): (200, sayfa(g, [("TEBLİĞLER", AAUT_DEGISIKLIK, "20261106-2.htm")]))})
        with tempfile.TemporaryDirectory() as d:
            yol = os.path.join(d, "alt", "sonuc.json")
            s = tazelik.komut(["--gun", "3", "--cikti", yol], getir=rg,
                              saat=lambda: datetime(2026, 11, 7, 9, 0, tzinfo=tazelik.TR), uyu=lambda x: None)
            with open(yol, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["durum"], "yeni_duzenleme")
        self.assertEqual(s["aralik"], ["2026-11-05", "2026-11-07"])
        self.assertEqual(s["istek"], 3)
        self.assertEqual([y["tarih"] for y in s["yeni"]], ["2026-11-06"])

    def test_komut_erisilemedi(self):
        s = tazelik.komut(["--gun", "3"], getir=SahteRG(hata="timed out"),
                          saat=lambda: datetime(2026, 11, 7, 9, 0, tzinfo=tazelik.TR), uyu=lambda x: None)
        self.assertEqual(s["durum"], "kontrol_edilemedi")
        self.assertIn("timed out", s["neden"])

    def test_issue_bir_kez(self):
        y = {"tarih": "2026-11-06", "rg": "RG 06.11.2026, S. 33422", "baslik": AAUT_DEGISIKLIK, "bolum": "TEBLİĞLER",
             "url": "https://www.resmigazete.gov.tr/eskiler/2026/11/20261106-2.htm", "desen": "AAÜT",
             "kartlar": ["vekalet"]}
        sonuc = {"durum": "yeni_duzenleme", "aralik": ["2026-11-05", "2026-11-07"], "yeni": [y, dict(y)]}
        ac, durum = nobetci.planla(sonuc, {}, [])
        self.assertEqual([i["title"] for i in ac], ["Tarife nöbetçisi: 06.11.2026 " + AAUT_DEGISIKLIK])
        self.assertIn(y["url"], ac[0]["body"])
        self.assertIn("ikinci okuma", ac[0]["body"])
        self.assertEqual(durum["erisilemeyen_gunler"], [])
        for state in ("OPEN", "CLOSED"):           # aynı başlık varsa (kapalı da olsa) yenisi açılmaz
            self.assertEqual(nobetci.planla(sonuc, {}, [{"title": ac[0]["title"], "state": state}])[0], [])

    def test_uc_gun_erisilemezse_issue(self):
        durum = {}
        acilan = []
        for gun in ("2026-11-05", "2026-11-06", "2026-11-06", "2026-11-07"):
            sonuc = {"durum": "kontrol_edilemedi", "aralik": ["x", gun], "neden": "timed out", "yeni": []}
            ac, durum = nobetci.planla(sonuc, durum, [])
            acilan.append(len(ac))
        self.assertEqual(acilan, [0, 0, 0, 1])
        self.assertEqual(durum["erisilemeyen_gunler"], ["2026-11-05", "2026-11-06", "2026-11-07"])
        self.assertEqual(nobetci.planla(sonuc, durum, [{"title": nobetci.ERISIM_BASLIGI, "state": "OPEN"}])[0], [])
        ac, durum = nobetci.planla({"durum": "guncel", "aralik": ["x", "2026-11-08"], "yeni": []}, durum, [])
        self.assertEqual((ac, durum["erisilemeyen_gunler"]), ([], []))


if __name__ == "__main__":
    unittest.main()
