"""Resmî Gazete ekleri: ek bağlantıları, taranmış sayfalar ve ek_sayfa — ağsız, unittest.

    python -m unittest discover -s tests -p test_rg_ek.py -v      # arthur-tr-hukuk-mcp'den

PDF'ler tests/rg_ek_pdf.py'de üretilir. Gerçek iki ek (20261103-3-1.pdf JPEG taraması,
20251231M5-28.pdf JBIG2 taraması) testlerin dışında bir kez denendi; sayfa türleri buradakilerle aynıdır.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import sys
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import rg_ek_pdf  # noqa: E402
from sources import resmi_gazete as rg  # noqa: E402
from textx import HAS_PYPDF, pdf_to_text  # noqa: E402

B = "https://www.resmigazete.gov.tr/eskiler/"
EK_URL = B + "2026/11/20261103-3-1.pdf"
SAYFA_URL = B + "2026/11/20261103-3.htm"

SAYFA_HTML = """<html><body><p>MADDE 1 – Tarife ekte gösterilmiştir.</p>
<link rel=File-List href="20261103-3_dosyalar/filelist.xml">
<p><b><span><a href="20261103-3-1.pdf"><span style='text-decoration:none'>Ekleri için
tıklayınız.</span></a></span></b></p>
<p><a href='20261103-3-2.pdf'>Ek-2</a> <a href="20261103-3-1.pdf">tekrar</a>
<a href="https://baska.example/20261103-3-3.pdf">dış site</a> <a href="20261103-2.htm">önceki</a>
<a href="/eskiler/2026/11/20261103-9-1.pdf">Eki için tıklayınız</a> <a href="#ust">yukarı</a></p>
</body></html>""".encode("windows-1254")


class _Sahte:
    """rg._http.get yerine: adres -> (gövde, başlık). İstekleri sayar."""

    def __init__(self, yanitlar):
        self.yanitlar, self.istekler = yanitlar, []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.istekler.append(url)
        govde, tur = self.yanitlar[url]
        return govde, {"Content-Type": tur}, url


class _Temel(unittest.TestCase):
    def setUp(self):
        self._eski = (rg._http.get, rg._pdfium)
        rg._ONBELLEK.clear()
        self.sahte = _Sahte({SAYFA_URL: (SAYFA_HTML, "text/html; charset=windows-1254"),
                             EK_URL: (rg_ek_pdf.ek_pdf(), "application/pdf")})
        rg._http.get = self.sahte

    def tearDown(self):
        rg._http.get, rg._pdfium = self._eski
        rg._ONBELLEK.clear()


class EkBaglantilariTest(_Temel):
    def test_ekler_sirasiyla_ve_yalniz_rg_adresleri(self):
        out = rg.get({"url": SAYFA_URL})
        self.assertEqual(out["ekler"], [{"sira": 1, "url": B + "2026/11/20261103-3-1.pdf"},
                                        {"sira": 2, "url": B + "2026/11/20261103-3-2.pdf"},
                                        {"sira": 3, "url": B + "2026/11/20261103-9-1.pdf"}])
        self.assertIn("tr_resmi_gazete_ek_sayfa", out["ek_okuma"])
        self.assertIn("Tarife ekte", out["text"])
        self.assertEqual(out["source_url"], SAYFA_URL)

    def test_eki_olmayan_sayfanin_yaniti_degismez(self):
        html = "<html><body><p>MADDE 1 – Yürürlük.</p></body></html>".encode("windows-1254")
        self.sahte.yanitlar[SAYFA_URL] = (html, "text/html; charset=windows-1254")
        out = rg.get({"url": SAYFA_URL})
        self.assertEqual(sorted(out), ["chars", "has_more", "page", "source_url", "text", "total_pages"])


@unittest.skipUnless(HAS_PYPDF, "pypdf yok")
class PdfOzetiTest(_Temel):
    def test_metin_bugunku_gibi_taranmis_sayfalar_ayrica(self):
        govde = rg_ek_pdf.ek_pdf()
        out = rg.get({"url": EK_URL})
        self.assertEqual(out["text"], pdf_to_text(govde)[0][:8000])       # metin yolu değişmedi
        self.assertEqual(out["sayfa_sayisi"], 3)
        self.assertEqual(out["taranmis_sayfalar"], [2, 3])
        self.assertIn("YOKTUR", out["taranmis_not"])
        self.assertIn("tr_resmi_gazete_ek_sayfa", out["ek_okuma"])

    def test_tamami_metin_olan_pdf_taranmis_notu_tasimaz(self):
        self.sahte.yanitlar[EK_URL] = (rg_ek_pdf.pdf([{"metin": rg_ek_pdf.METIN}]), "application/pdf")
        out = rg.get({"url": EK_URL})
        self.assertEqual((out["sayfa_sayisi"], out["taranmis_sayfalar"]), (1, []))
        self.assertNotIn("taranmis_not", out)
        self.assertNotIn("ek_okuma", out)

    def test_getir_indirdigini_ek_sayfa_yeniden_indirmez(self):
        rg.get({"url": EK_URL})
        rg.ek_sayfa({"url": EK_URL, "sayfa": 1})
        rg.ek_sayfa({"url": EK_URL, "sayfa": 2})
        self.assertEqual(self.sahte.istekler, [EK_URL])


@unittest.skipUnless(HAS_PYPDF, "pypdf yok")
class EkSayfaTest(_Temel):
    def test_metinli_sayfa_metin_doner(self):
        out = rg.ek_sayfa({"url": EK_URL, "sayfa": 1})
        self.assertIsInstance(out, dict)
        self.assertEqual((out["tur"], out["sayfa"], out["toplam_sayfa"]), ("metin", 1, 3))
        self.assertIn("MADDE 1", out["text"])
        self.assertEqual(out["rg_tarihi"], "03.11.2026")
        self.assertEqual(out["source_url"], EK_URL)

    def test_jpeg_taramasi_cozulmeden_goruntu_blogu(self):
        rg._pdfium = None                      # kod dağıtımı: yalnız pypdf
        out = rg.ek_sayfa({"url": EK_URL, "sayfa": 2})
        metin, goruntu = out.mcp_content
        self.assertEqual(goruntu["type"], "image")
        self.assertEqual(goruntu["mimeType"], "image/jpeg")
        self.assertEqual(base64.b64decode(goruntu["data"]), rg_ek_pdf.JPEG)   # PDF'teki baytlar
        self.assertEqual(metin["type"], "text")
        ozet = json.loads(metin["text"])
        self.assertEqual(ozet, out.data)
        self.assertEqual((ozet["sayfa"], ozet["toplam_sayfa"], ozet["source_url"]), (2, 3, EK_URL))
        self.assertEqual(ozet["goruntu"], {"mimeType": "image/jpeg", "genislik": 16, "yukseklik": 24,
                                           "bayt": len(rg_ek_pdf.JPEG), "yol": "pdf-jpeg"})
        self.assertIn("Tutarları görüntüden okuyun, kaynağı RG tarih/sayı ile verin; okunamayan rakam "
                      "için UYARI satırı.", ozet["kural"])
        self.assertIn("UYARI: veri çekilemedi, teyidiniz gerekli: " + EK_URL, ozet["kural"])
        self.assertEqual(ozet["sayfadaki_metin"], "Teblig (N) Ekleri 2")
        self.assertNotIn("data", json.dumps(out.data))                       # özet görüntü taşımaz

    def test_pdfium_yoksa_goruntu_uydurulmaz(self):
        rg._pdfium = None
        out = rg.ek_sayfa({"url": EK_URL, "sayfa": 3})
        self.assertIsInstance(out, dict)
        self.assertTrue(out["unavailable"])
        self.assertEqual(out["uyari"], "UYARI: veri çekilemedi, teyidiniz gerekli: " + EK_URL)
        self.assertEqual((out["sayfa"], out["toplam_sayfa"]), (3, 3))

    def test_butceyi_asan_jpeg_pdfiumsuz_uyari(self):
        rg._pdfium = None
        genis = rg_ek_pdf.pdf([{"jpeg": rg_ek_pdf.JPEG, "genislik": 3000, "yukseklik": 4000}])
        self.sahte.yanitlar[EK_URL] = (genis, "application/pdf")
        self.assertTrue(rg.ek_sayfa({"url": EK_URL, "sayfa": 1})["unavailable"])

    def test_dondurulmus_sayfada_ham_jpeg_verilmez(self):
        rg._pdfium = None
        doner = rg_ek_pdf.pdf([{"jpeg": rg_ek_pdf.JPEG, "dondur": 90}])
        self.sahte.yanitlar[EK_URL] = (doner, "application/pdf")
        self.assertTrue(rg.ek_sayfa({"url": EK_URL, "sayfa": 1})["unavailable"])

    @unittest.skipUnless(rg._pdfium is not None, "pypdfium2 yok (tam imajda var)")
    def test_pdfium_ile_goruntusuz_sayfa_png(self):
        out = rg.ek_sayfa({"url": EK_URL, "sayfa": 3})
        goruntu = out.mcp_content[1]
        self.assertEqual(goruntu["mimeType"], "image/png")
        png = base64.b64decode(goruntu["data"])
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        genislik, yukseklik = struct.unpack(">II", png[16:24])
        self.assertEqual((genislik, out.data["goruntu"]["genislik"]), (1600, 1600))
        self.assertEqual(yukseklik, out.data["goruntu"]["yukseklik"])
        self.assertLessEqual(len(png), rg._AZAMI_BAYT)
        self.assertEqual(out.data["goruntu"]["yol"], "pdfium")

    def test_yalniz_www_resmigazete_https(self):
        for url in ("http://www.resmigazete.gov.tr/eskiler/a.pdf",
                    "https://resmigazete.gov.tr/eskiler/a.pdf",
                    "https://cdn.resmigazete.gov.tr/eskiler/a.pdf",
                    "https://www.resmigazete.gov.tr.example.com/a.pdf",
                    "https://x@www.resmigazete.gov.tr/a.pdf",
                    "https://www.resmigazete.gov.tr:8443/a.pdf",
                    "file:///etc/passwd", "", None):
            self.assertIn("error", rg.ek_sayfa({"url": url}), url)
        self.assertEqual(self.sahte.istekler, [])                            # hiç istek gitmedi

    def test_sayfa_araligi_ve_pdf_olmayan_adres(self):
        out = rg.ek_sayfa({"url": EK_URL, "sayfa": 4})
        self.assertEqual((out["toplam_sayfa"], "1-3" in out["error"]), (3, True))
        self.assertIn("error", rg.ek_sayfa({"url": EK_URL, "sayfa": "iki"}))
        self.assertIn("HTML", rg.ek_sayfa({"url": SAYFA_URL})["error"])

    def test_indirme_hatasi_uyari_olur(self):
        def hata(*a, **k):
            raise rg.HttpError(503, EK_URL, b"")
        rg._http.get = hata
        out = rg.ek_sayfa({"url": EK_URL, "sayfa": 1})
        self.assertTrue(out["unavailable"])
        self.assertEqual(out["uyari"], "UYARI: veri çekilemedi, teyidiniz gerekli: " + EK_URL)


class YardimciTest(unittest.TestCase):
    def test_rg_kunyesi_baglantidan(self):
        self.assertEqual(rg._rg_kunyesi(B + "2025/12/20251231M5-28.pdf"),
                         {"rg_tarihi": "31.12.2025", "mukerrer": 5,
                          "rg_sayisi_icin": 'tr_resmi_gazete_fihrist(date="2025-12-31", mukerrer=5)'})
        self.assertEqual(rg._rg_kunyesi(EK_URL)["rg_tarihi"], "03.11.2026")
        self.assertEqual(rg._rg_kunyesi(B + "2026/11/20261231-1.pdf"), {})   # klasör ile ad uyuşmuyor
        self.assertEqual(rg._rg_kunyesi(B + "2026/02/20260231-1.pdf"), {})   # olmayan gün
        self.assertEqual(rg._rg_kunyesi("https://www.resmigazete.gov.tr/x.pdf"), {})

    def test_png_gecerli_ve_satirlar_korunur(self):
        satirlar = bytes([0, 255, 10, 20, 30, 40])                     # 3x2 gri
        png = rg._png(3, 2, 1, satirlar)
        self.assertEqual(struct.unpack(">IIBB", png[16:26]), (3, 2, 8, 0))
        idat = png.index(b"IDAT")
        uzunluk = struct.unpack(">I", png[idat - 4:idat])[0]
        ham = zlib.decompress(png[idat + 4:idat + 4 + uzunluk])
        self.assertEqual(ham, b"\x00" + satirlar[:3] + b"\x00" + satirlar[3:])

    def test_arac_tablosunda(self):
        import server
        araclar = {t.name: t for t in server.TOOLS}
        self.assertIn("resmi_gazete_ek_sayfa", araclar)
        self.assertEqual(araclar["resmi_gazete_ek_sayfa"].input_schema["required"], ["url"])


if __name__ == "__main__":
    unittest.main()
