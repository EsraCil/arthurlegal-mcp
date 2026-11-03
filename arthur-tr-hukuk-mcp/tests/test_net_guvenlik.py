"""net.Http güvenlik sınırları: yalnız kaynağın kendi alan adı, yönlendirmede de; yanıt ve açılmış boy sınırı.

Ağa çıkmaz: dış adresler istek gönderilmeden reddedilir, yönlendirme ve boy denemeleri 127.0.0.1'deki geçici
bir sunucuyla yapılır. Çalıştırma: python -m unittest discover -s tests -p test_net_guvenlik.py -v
"""

from __future__ import annotations

import gzip
import http.server
import os
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import net  # noqa: E402
from net import Http, HttpError  # noqa: E402
from sources import epdk, kvkk, resmi_gazete  # noqa: E402


class _Sunucu(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/baska-alana":
            self.send_response(302)
            self.send_header("Location", "http://localhost:%d/hedef" % self.server.server_port)
            self.end_headers()
        elif self.path == "/ayni-alana":
            self.send_response(302)
            self.send_header("Location", "/hedef")
            self.end_headers()
        elif self.path == "/buyuk":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x" * 5000)
        elif self.path == "/bomba":
            govde = gzip.compress(b"\0" * 50000)
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(govde)))
            self.end_headers()
            self.wfile.write(govde)
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"tamam")

    def log_message(self, *a):
        pass


class HttpAlanSiniri(unittest.TestCase):
    def test_kendi_alani_ve_alt_alanlari_gecer(self):
        h = Http("https://www.resmigazete.gov.tr")
        self.assertTrue(h.allowed("https://www.resmigazete.gov.tr/eskiler/2026/11/20261102.htm"))
        self.assertTrue(h.allowed("https://resmigazete.gov.tr/x"))

    def test_dis_adresler_reddedilir(self):
        h = Http("https://www.resmigazete.gov.tr")
        for url in ("https://www.resmigazete.gov.tr.saldirgan.example/r",
                    "https://saldirgan.example/?u=www.resmigazete.gov.tr",
                    "http://www.resmigazete.gov.tr/x",              # https gerekir
                    "http://127.0.0.1:8790/mcp",
                    "http://169.254.169.254/latest/meta-data/",
                    "http://[fdaa::3]:8080/",
                    "file:///etc/passwd",
                    "https://evilresmigazete.gov.tr/x"):
            self.assertFalse(h.allowed(url), url)
            with self.assertRaises(HttpError):
                h.get(url)

    def test_ek_alan_adi(self):
        h = Http("", allowed_domains=["anayasa.gov.tr"])
        self.assertTrue(h.allowed("https://kararlarbilgibankasi.anayasa.gov.tr/api"))
        self.assertFalse(h.allowed("https://anayasa.gov.tr.saldirgan.example/api"))

    def test_kaynak_getiricileri_dis_adresi_cekmez(self):
        self.assertIn("error", kvkk.get({"id": "http://127.0.0.1:9/x"}))
        self.assertIn("error", resmi_gazete.get({"url": "https://www.resmigazete.gov.tr.saldirgan.example/r"}))
        self.assertIn("error", epdk.get({"id": "https://www.epdk.gov.tr.saldirgan.example/x"}))


class HttpYonlendirmeVeBoy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sunucu = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Sunucu)
        threading.Thread(target=cls.sunucu.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.sunucu.server_port

    @classmethod
    def tearDownClass(cls):
        cls.sunucu.shutdown()

    def setUp(self):
        self._eski = net.MAX_BYTES

    def tearDown(self):
        net.MAX_BYTES = self._eski

    def test_ayni_alana_yonlendirme_izlenir(self):
        govde, _, son = Http(self.base).get("/ayni-alana")
        self.assertEqual(govde, b"tamam")
        self.assertTrue(son.endswith("/hedef"))

    def test_baska_alana_yonlendirme_reddedilir(self):
        with self.assertRaises(HttpError):
            Http(self.base).get("/baska-alana")

    def test_buyuk_yanit_reddedilir(self):
        net.MAX_BYTES = 1000
        with self.assertRaises(HttpError):
            Http(self.base).get("/buyuk")

    def test_sikistirma_bombasi_reddedilir(self):
        net.MAX_BYTES = 10000
        with self.assertRaises(HttpError):
            Http(self.base).get("/bomba")

    def test_kucuk_sikistirilmis_yanit_acilir(self):
        govde = net._inflate(gzip.compress(b"merhaba"), {"Content-Encoding": "gzip"})
        self.assertEqual(govde, b"merhaba")


class _DogrulanamayanAcici:
    """Her açılışta sertifika doğrulama hatası veren sahte açıcı (ağa çıkmaz)."""

    def __init__(self):
        self.acilis = 0

    def open(self, req, timeout=None):
        import ssl
        import urllib.error
        self.acilis += 1
        raise urllib.error.URLError(ssl.SSLCertVerificationError(
            1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"))


class TlsDogrulama(unittest.TestCase):
    def setUp(self):
        self._eski = net.TLS_FALLBACK

    def tearDown(self):
        net.TLS_FALLBACK = self._eski

    def test_dogrulanamayan_zincir_hata_olur_dogrulama_kapanmaz(self):
        net.TLS_FALLBACK = False
        h = Http("https://www.bddk.org.tr")
        acici = _DogrulanamayanAcici()
        h.opener = acici
        with self.assertRaises(HttpError):
            h.get("/x")
        self.assertTrue(h.verify)
        self.assertIs(h.opener, acici)
        self.assertEqual(acici.acilis, 1)

    def test_isletmeci_acarsa_eski_davranis(self):
        net.TLS_FALLBACK = True
        h = Http("https://www.bddk.org.tr")
        h.opener = _DogrulanamayanAcici()
        h._degrade_tls = lambda url: (setattr(h, "verify", False), setattr(h, "opener", _DogrulanamayanAcici()))
        with self.assertRaises(HttpError):
            h.get("/x")
        self.assertFalse(h.verify)

    def test_hicbir_kaynak_dogrulamayi_kapatmaz(self):
        import importlib
        import sources
        for ad in sources.MODULES:
            mod = importlib.import_module("sources." + ad)
            istemci = getattr(mod, "_http", None)
            if isinstance(istemci, Http):
                self.assertTrue(istemci.verify, ad)


class EpdkZipSiniri(unittest.TestCase):
    def test_buyuk_zip_uyesi_okunmaz(self):
        import io
        import zipfile
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("karar.doc", b"\0" * 5000)
        eski = epdk._ZIP_UYE_SINIRI
        epdk._ZIP_UYE_SINIRI = 1000
        try:
            self.assertEqual(epdk.metin_cikar(tampon.getvalue()), ("", 0, ""))
        finally:
            epdk._ZIP_UYE_SINIRI = eski


if __name__ == "__main__":
    unittest.main()
