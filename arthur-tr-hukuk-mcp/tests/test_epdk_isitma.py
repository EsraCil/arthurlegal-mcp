"""EPDK önbelleğini arka planda ısıtma (F-067). Ağsız, unittest.

Soğuk önbellekte ilk kurum_karari_ara(kurum="epdk") beş piyasa sayfasını ve ~126 kategori listesini art arda
çekiyordu (canlı: 39,6 sn). Burada EPDK sahte: her istek sayılır, hiçbir istek ağa çıkmaz.

    python -m unittest discover -s tests -p test_epdk_isitma.py -v      # arthur-tr-hukuk-mcp'den
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from sources import epdk  # noqa: E402


class _SahteEpdk:
    """Piyasa sayfası (iki kategori) ve kategori listesi (bir karar); her isteği sayar."""

    def __init__(self, hata: bool = False):
        self.istekler, self.hata = [], hata

    def get_text(self, path, **kw):
        self.istekler.append(path)
        sira = list(epdk.PAGES.values()).index(path) + 1
        return ('<a class="kesmebedel_1" data-type="False">Lisans</a>'
                '<a class="kesmebedel_2" data-id="%d1" onclick="ShowDetailList(this)">Tadil</a>'
                '<a class="kesmebedel_3" data-id="%d2" onclick="ShowDetailList(this)">Tarife</a>' % (sira, sira))

    def post_json(self, path, payload, **kw):
        self.istekler.append(payload["fId"])
        if self.hata:
            raise epdk.HttpError(503, path)
        return {"Type": 1, "model": [{"Title": "Lisans tadili %s" % payload["fId"], "Number": "13134",
                                      "Date": "26.12.2024", "FastAccessDetail": [{"ContentId": payload["fId"]}]}]}


class _Temel(unittest.TestCase):
    hata = False

    def setUp(self):
        self.sahte = _SahteEpdk(self.hata)
        eski = (epdk._http.get_text, epdk._http.post_json, epdk._isci)

        def geri_al():
            epdk._http.get_text, epdk._http.post_json, epdk._isci = eski
            epdk._tree.clear()
            epdk._lists.clear()

        self.addCleanup(geri_al)
        epdk._http.get_text, epdk._http.post_json = self.sahte.get_text, self.sahte.post_json
        epdk._tree.clear()
        epdk._lists.clear()
        self.isci = epdk._isci = epdk._Tazeleyici(0, lambda m: None)    # iş parçacığı yok: bosalt() ile

    def isit(self):
        for m in epdk.PAGES:
            self.isci.ekle(("piyasa", m))
        self.isci.ekle(("piyasa", "elektrik"))                          # aynı iş ikinci kez sıraya girmez
        self.isci.bosalt()

    def eskit(self, store, saniye):
        for k, (ts, v) in list(store.items()):
            store[k] = (ts - saniye, v)


class IsitmaTest(_Temel):
    def test_isinmis_onbellek_ilk_aramada_aga_cikmaz(self):
        self.isit()
        self.assertEqual(len(self.sahte.istekler), len(epdk.PAGES) * 3)   # 5 sayfa + 10 liste
        once = len(self.sahte.istekler)
        out = epdk.search({"query": "tadili"})
        self.assertEqual(out["total"], 10)
        self.assertEqual(len(self.sahte.istekler), once)

    def test_suresi_gecen_liste_beklemeden_verilir_isci_tazeler(self):
        self.isit()
        once = len(self.sahte.istekler)
        self.eskit(epdk._tree, epdk._TTL + 1)
        self.eskit(epdk._lists, epdk._TTL + 1)
        self.assertEqual(epdk.search({"query": "tadili"})["total"], 10)
        self.assertEqual(len(self.sahte.istekler), once, "arama bayat listeyi beklemeden verir")
        self.isci.bosalt()
        self.assertEqual(len(self.sahte.istekler), once * 2, "işçi her listeyi bir kez tazeler")

    def test_cok_eski_liste_ya_da_iscisiz_sunucu_eski_davranis(self):
        self.isit()
        once = len(self.sahte.istekler)
        self.eskit(epdk._lists, epdk._BAYAT_SINIR + 1)
        epdk.search({"query": "tadili", "market": "lpg"})
        self.assertEqual(len(self.sahte.istekler), once + 2, "24 saatten eski liste verilmez, arama çeker")
        epdk._isci = None
        self.eskit(epdk._lists, epdk._TTL + 1)
        epdk.search({"query": "tadili", "market": "lpg"})
        self.assertEqual(len(self.sahte.istekler), once + 4, "işçi yokken bayat liste aramada çekilir")


class UstUsteHataTest(_Temel):
    hata = True

    def test_ucuncu_hatadan_sonra_siradakiler_birakilir(self):
        self.isit()
        sayfalar = [i for i in self.sahte.istekler if i in epdk.PAGES.values()]
        self.assertEqual(len(sayfalar), len(epdk.PAGES))
        # On liste isteğinin yalnız üçü gider; kalan yedisi bırakılır.
        self.assertEqual(len(self.sahte.istekler) - len(sayfalar), epdk._HATA_SINIRI)
        self.assertTrue(self.isci.sira.empty())
        self.assertEqual(self.isci.bekleyen, set())


class AcilisAnahtariTest(unittest.TestCase):
    def test_yalniz_barindirilan_sunucuda_ya_da_acikca_istenince(self):
        cagrilar = []
        eski = epdk.isit
        epdk.isit = lambda *a, **k: cagrilar.append(1)
        self.addCleanup(setattr, epdk, "isit", eski)
        for ortam, acik in (({}, False), ({"MCP_TRANSPORT": "stdio"}, False), ({"MCP_TRANSPORT": "http"}, True),
                            ({"MCP_TRANSPORT": "http", "EPDK_ISITMA": "0"}, False), ({"EPDK_ISITMA": "1"}, True)):
            with self.subTest(**ortam):
                del cagrilar[:]
                epdk.acilista_isit(ortam)
                self.assertEqual(cagrilar, [1] if acik else [])


if __name__ == "__main__":
    unittest.main()
