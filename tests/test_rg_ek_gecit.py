"""Görüntü içeriği ağ geçidinden geçer; öteki araçların yanıtı değişmez — ağsız, unittest.

    python -m unittest discover -s tests -p test_rg_ek_gecit.py -v      # depo kökünden

mcpcore.ContentResult: araç kendi MCP içerik bloklarını (metin + görüntü) döndürür, dağıtıcı onları
olduğu gibi gönderir. Öteki her dönüş değeri (sözlük, liste, metin ...) master'ın taban mcpcore'uyla
(tests/fixtures/mcpcore_taban.py) bayt bayt aynı yanıtı alır. tr_resmi_gazete_ek_sayfa yalnız /mcp'dedir.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.append(os.path.join(ROOT, "arthur-tr-hukuk-mcp", "tests"))
os.environ["DE_ELI_URL"] = "off"   # de-eli ayrı süreçtir; burada gerekmez

import mcpcore  # noqa: E402
import server  # noqa: E402
import tool_titles  # noqa: E402
import rg_ek_pdf  # noqa: E402

server.build()
DIRECTORY_TOOLS, DIRECTORY_TEXT, DIRECTORY_VIEWS = server.build_directory()
RG = sys.modules["srv_tr"].resmi_gazete          # ağ geçidinin yüklediği arka uç modülü
EK = "tr_resmi_gazete_ek_sayfa"
EK_URL = "https://www.resmigazete.gov.tr/eskiler/2026/11/20261103-3-1.pdf"
TABAN = os.path.join(ROOT, "tests", "fixtures", "mcpcore_taban.py")


def _taban():
    spec = importlib.util.spec_from_file_location("mcpcore_taban_rg", TABAN)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _cagri(modul, deger, meta=None):
    """Tek araçlı dağıtıcıda tools/call yanıtı."""
    def handler(args):
        if isinstance(deger, Exception):
            raise deger
        return deger
    arac = modul.Tool("a", "a", {"type": "object"}, handler, meta=meta)
    return modul.Dispatcher([arac], "t", "1").handle(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "a", "arguments": {}}})


class _Nesne:
    def __str__(self):
        return "nesne"


class IcerikGecisi(unittest.TestCase):
    META = {"ui": {"resourceUri": "ui://arthurlegal/masa.html"}}

    def test_bloklar_oldugu_gibi_gider(self):
        bloklar = [{"type": "text", "text": "{\"sayfa\": 2}"},
                   {"type": "image", "data": "AAEC", "mimeType": "image/jpeg"}]
        r = _cagri(mcpcore, mcpcore.ContentResult(bloklar, {"sayfa": 2}))["result"]
        self.assertEqual(r, {"content": bloklar})                       # görünüm yoksa özet de yok
        r = _cagri(mcpcore, mcpcore.ContentResult(bloklar, {"sayfa": 2}), self.META)["result"]
        self.assertEqual(r, {"content": bloklar, "structuredContent": {"sayfa": 2}})

    def test_oteki_degerler_tabandaki_gibi(self):
        taban = _taban()
        degerler = [{"a": 1, "metin": "Türkçe ığüşöç"}, {"mcp_content": [1]}, [1, {"b": 2}], "düz metin",
                    42, 1.5, None, True, {"tarih": _Nesne()}, {}, [],
                    mcpcore.McpError("RATE_LIMITED: HTTP 429")]
        for meta in (None, self.META):
            for deger in degerler:
                eski_deger = taban.McpError(str(deger)) if isinstance(deger, mcpcore.McpError) else deger
                self.assertEqual(json.dumps(_cagri(mcpcore, deger, meta), ensure_ascii=False, default=str),
                                 json.dumps(_cagri(taban, eski_deger, meta), ensure_ascii=False, default=str),
                                 (meta, deger))

    def test_denetim_kaydi_metni_ozetler_goruntuyu_almaz(self):
        import denetim
        sonuc = mcpcore.ContentResult([{"type": "text", "text": "{\"sayfa\": 2}"},
                                       {"type": "image", "data": "QUJD" * 100, "mimeType": "image/png"}])
        self.assertEqual(denetim._donen(sonuc)["ilk"], "{\"sayfa\": 2}")
        self.assertEqual(denetim._donen({"a": 1}), denetim._donen({"a": 1}))
        self.assertEqual(denetim._durum_sinifla(sonuc), "ok")

    def test_vendored_kopyalar_da_tanir(self):
        """Arka uç kendi mcpcore kopyasının sınıfını döndürse de kök dağıtıcı tanır (öznitelikle)."""
        spec = importlib.util.spec_from_file_location(
            "mcpcore_tr_kopya", os.path.join(ROOT, "arthur-tr-hukuk-mcp", "mcpcore.py"))
        kopya = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kopya)
        bloklar = [{"type": "image", "data": "AA==", "mimeType": "image/png"}]
        self.assertEqual(_cagri(mcpcore, kopya.ContentResult(bloklar))["result"], {"content": bloklar})


class EkSayfaAgGecidinde(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._eski = RG._http.get
        cls.pdf = rg_ek_pdf.ek_pdf()

        def sahte_get(url, params=None, headers=None, timeout=None):
            if url != EK_URL:
                raise RG.HttpError(404, url, b"")
            return cls.pdf, {"Content-Type": "application/pdf"}, url
        RG._http.get = sahte_get
        RG._ONBELLEK.clear()
        main = mcpcore.Dispatcher(server._tools, "arthurlegal-mcp", server.__version__, "",
                                  resources=server._resources)
        directory = mcpcore.Dispatcher(DIRECTORY_TOOLS, "arthurlegal-mcp", server.__version__,
                                       DIRECTORY_TEXT, resources=DIRECTORY_VIEWS)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), mcpcore._make_handler(
            main, {server.DIRECTORY_PATH: directory}))
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        RG._http.get = cls._eski
        RG._ONBELLEK.clear()

    def rpc(self, path, ad, **args):
        govde = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": ad, "arguments": args}}).encode("utf-8")
        istek = urllib.request.Request(self.base + path, data=govde, method="POST",
                                       headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(istek, timeout=30) as yanit:
            return json.loads(yanit.read().decode("utf-8"))

    def test_yalniz_ana_uc_noktasinda(self):
        ana = {t.name: t for t in server._tools}
        self.assertIn(EK, ana)
        self.assertNotIn(EK, [t.name for t in DIRECTORY_TOOLS])
        self.assertEqual(ana[EK].title, tool_titles.TITLES[EK])
        self.assertTrue(ana[EK].annotations["readOnlyHint"])
        self.assertEqual(self.rpc(server.DIRECTORY_PATH, EK, url=EK_URL).get("error", {}).get("code"), -32602)

    @unittest.skipUnless(RG.HAS_PYPDF, "pypdf yok")
    def test_goruntu_blogu_http_yanitinda(self):
        r = self.rpc("/mcp", EK, url=EK_URL, sayfa=2)["result"]
        self.assertNotIn("isError", r)
        metin, goruntu = r["content"]
        self.assertEqual((metin["type"], goruntu["type"], goruntu["mimeType"]), ("text", "image", "image/jpeg"))
        self.assertEqual(base64.b64decode(goruntu["data"]), rg_ek_pdf.JPEG)
        ozet = json.loads(metin["text"])
        self.assertEqual((ozet["sayfa"], ozet["toplam_sayfa"], ozet["source_url"]), (2, 3, EK_URL))
        if server.al_arayuz is not None:                                  # masa görünümü özeti okur
            self.assertEqual(r["structuredContent"], ozet)

    @unittest.skipUnless(RG.HAS_PYPDF, "pypdf yok")
    def test_metinli_sayfa_tek_metin_blogu(self):
        r = self.rpc("/mcp", EK, url=EK_URL, sayfa=1)["result"]
        self.assertEqual([b["type"] for b in r["content"]], ["text"])
        self.assertEqual(json.loads(r["content"][0]["text"])["tur"], "metin")

    @unittest.skipUnless(RG.HAS_PYPDF, "pypdf yok")
    def test_dizindeki_getir_ana_uca_ozgu_isareti_tasimaz(self):
        ana = json.loads(self.rpc("/mcp", "tr_resmi_gazete_getir", url=EK_URL)["result"]["content"][0]["text"])
        dizin = json.loads(self.rpc(server.DIRECTORY_PATH, "tr_resmi_gazete_getir",
                                    url=EK_URL)["result"]["content"][0]["text"])
        self.assertIn(EK, ana["ek_okuma"])
        self.assertNotIn("ek_okuma", dizin)
        self.assertEqual(dizin["taranmis_sayfalar"], [2, 3])
        self.assertEqual({k: v for k, v in ana.items() if k != "ek_okuma"}, dizin)
        self.assertNotIn(EK, json.dumps(dizin, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
