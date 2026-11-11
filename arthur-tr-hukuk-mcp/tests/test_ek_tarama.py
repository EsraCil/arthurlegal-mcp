"""Zamanlanmış kurum taraması (ek_tarama.py): kayıttan iskelet, yalnız-yeni tarama, Rekabet metin tamamlama,
ek ve kayıt yazımı, kurum hatası ötekileri durdurmaz. Sahte kaynaklarla; ağ yok.

    python -m unittest discover -s tests -p "test_ek_tarama.py"
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import ek_tarama  # noqa: E402
import retrieval  # noqa: E402
import sources  # noqa: E402

E = ek_tarama.indeks_ek
AD = ek_tarama.AD
UZUN = "Rekabet Kurulu kararının gerekçeli metni. " * 20


def _belge(ref, kurum, baslik, govde, tarih="2026-10-01"):
    return {"ref": ref, "subject": kurum, "title": baslik, "body": govde, "url": "https://ornek/" + ref,
            "lang": "tr", "date": tarih, "status": "", "court": kurum.upper(), "citation": "%s atfı" % ref,
            "meta": {"kurum": kurum}}


class _Kaynak:
    crawlable = True

    def __init__(self, liste=(), hata=None, metinler=None, gunluk=()):
        self.liste, self.hata, self.metinler, self.gunluk = list(liste), hata, metinler or {}, list(gunluk)
        self.secenek = {}

    def crawl(self, index, log=print, max_pages=0, fetch_text=False, years=None, issues=None):
        self.secenek = {"max_pages": max_pages, "fetch_text": fetch_text, "years": years, "issues": issues}
        if self.hata:
            raise self.hata
        for mesaj in self.gunluk:
            log(mesaj)
        n = 0
        for belge in self.liste:
            onek = belge.get("_onek")
            if onek and index.exists_prefix(onek):
                continue
            if not onek and index.exists(belge["ref"]):
                continue
            index.upsert({k: v for k, v in belge.items() if k != "_onek"})
            n += 1
        return {"indexed": n}

    def get(self, args):
        return {"text": self.metinler.get(args["id"], "")}


class TaramaTest(unittest.TestCase):
    def setUp(self):
        self.dizin = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dizin, True)
        self.indeks = os.path.join(self.dizin, "taban.db")
        idx = retrieval.Index(self.indeks)
        for belge in (_belge("rekabet:1", "rekabet", "Birleşme izni", UZUN, "2026-09-01"),
                      _belge("rekabet:2", "rekabet", "Kartel soruşturması", "Kartel soruşturması", "2026-09-20"),
                      _belge("bddk:1", "bddk", "Faaliyet izni", "Faaliyet izni metni"),
                      _belge("spk:2026/1:0", "spk", "SPK Bülteni 2026/1", "bülten"),
                      _belge("stk:66:K-1", "sigorta_tahkim", "K-1", "hakem kararı")):
            idx.upsert(belge)
        idx.reindex_fts()
        idx.db.close()
        db = E.salt_okunur(self.indeks)
        self.kayit = ek_tarama.kayit_uret(db)
        db.close()
        self.veri = os.path.join(self.dizin, "veri")
        ek = os.path.join(self.veri, AD, "ek")
        os.makedirs(ek)
        E.kayit_yaz(os.path.join(ek, E.KAYIT_ADI), self.kayit)
        # aynı gün daha önce yazılmış bir ek: yeni dosya -2 almalı
        E.dosya_yaz(os.path.join(ek, "2026-11-13.jsonl.gz"), {"ek": 1, "indeks": AD, "tarih": "2026-11-13"}, [])
        self.kaynaklar = {
            "kvkk": _Kaynak([_belge("kvkk:9", "kişisel veriler", "KVKK kararı", "özet")]),
            "btk": _Kaynak(hata=RuntimeError("HTTP 503 btk.gov.tr")),
            "bddk": _Kaynak([_belge("bddk:1", "bddk", "Faaliyet izni", "kısa"),
                             _belge("bddk:2", "bddk", "Kuruluş izni", "yeni", "2026-11-05")]),
            "spk": _Kaynak([dict(_belge("spk:2026/1:0", "spk", "x", "x"), _onek="spk:2026/1:"),
                            dict(_belge("spk:2026/2:0", "spk", "SPK Bülteni 2026/2", "yeni bülten", "2026-11-10"),
                                 _onek="spk:2026/2:")]),
            "sigorta_tahkim": _Kaynak([dict(_belge("stk:66:K-1", "sigorta_tahkim", "x", "x"), _onek="stk:66:")],
                                      gunluk=["sigorta_tahkim: issue 67 failed: HTTP 404 Not Found"]),
            "rekabet": _Kaynak([_belge("rekabet:1", "rekabet", "x", "x"),
                                _belge("rekabet:3", "rekabet", "Yeni birleşme", "Yeni birleşme", "2026-11-06")],
                               metinler={"2": UZUN + "kartel", "3": ""}),
            "epdk": _Kaynak([]),
        }
        self._eski = sources.load_all
        sources.load_all = lambda: self.kaynaklar           # crawl._backfill kaynağı buradan alır
        self.addCleanup(setattr, sources, "load_all", self._eski)

    def test_kayit_metinsizi_ve_kurumlari_tasir(self):
        self.assertEqual(self.kayit["belge"], 5)
        self.assertEqual([b["ref"] for b in self.kayit["metinsiz"]], ["rekabet:2"])
        self.assertEqual(self.kayit["kurumlar"]["rekabet"], {"refler": ["rekabet:1", "rekabet:2"], "en_yeni": "2026-09-20"})

    def test_plan_haftada_iki_kosuya_gore(self):
        p = dict(ek_tarama.plan(self.kayit, time.strptime("2026-11-13", "%Y-%m-%d")))
        self.assertEqual(p["rekabet"], {"pages": 10})
        self.assertEqual(p["kvkk"], {"pages": 3})
        self.assertEqual(p["btk"], {"pages": 2})
        self.assertEqual(p["sigorta_tahkim"], {"issues": [66, 67]})       # kayıttaki son sayı ve bir sonraki
        self.assertEqual(p["epdk"], {"fetch_text": True})
        self.assertEqual(p["spk"], {"years": [2026]})
        self.assertEqual(dict(ek_tarama.plan(self.kayit, time.strptime("2027-01-03", "%Y-%m-%d")))["spk"],
                         {"years": [2026, 2027]})

    def test_tarama_ek_ve_kayit_yazar_hata_otekileri_durdurmaz(self):
        cikti = os.path.join(self.dizin, "cikti")
        ozet = ek_tarama.tara(self.veri, cikti, tarih="2026-11-13", aralik=0, kaynaklar=self.kaynaklar)
        self.assertEqual(ozet["dosya"], "2026-11-13-2.jsonl.gz")
        baslik, belgeler = E.dosya_oku(os.path.join(cikti, AD, "ek", ozet["dosya"]), AD)
        refler = {b["ref"]: b for b in belgeler}
        self.assertEqual(set(refler), {"kvkk:9", "bddk:2", "spk:2026/2:0", "rekabet:3", "rekabet:2"})
        self.assertEqual((baslik["yeni"], baslik["degisen"]), (4, 1))
        self.assertEqual(refler["rekabet:2"]["body"], UZUN + "kartel")      # metni gelen eski karar
        self.assertEqual(refler["rekabet:2"]["citation"], "rekabet:2 atfı")  # öteki alanlar kayıttan
        self.assertEqual(refler["rekabet:3"]["body"], "Yeni birleşme")       # metni henüz yok
        self.assertEqual(refler["kvkk:9"]["subject"], "kvkk")                 # konu kurum anahtarı
        self.assertIn("last_crawl", baslik["durum"])

        k = ozet["kurumlar"]
        self.assertEqual((k["rekabet"]["yeni"], k["rekabet"]["metin_eklenen"]), (1, 1))
        self.assertEqual(k["rekabet"]["en_yeni_tarih"], "2026-11-06")
        self.assertEqual(k["kvkk"]["yeni"], 1)
        self.assertTrue(k["btk"]["hatalar"] and "503" in k["btk"]["hatalar"][0])
        self.assertTrue(k["epdk"]["hatalar"] and "hiç kayıt" in k["epdk"]["hatalar"][0])
        self.assertEqual(k["sigorta_tahkim"]["hatalar"], [], "henüz çıkmamış sayının 404'ü hata değil")
        self.assertEqual(k["spk"]["hatalar"], [])
        self.assertEqual(k["bddk"]["bilinen_atlandi"], 1)
        self.assertEqual(ozet["hata_sayisi"], 2)
        self.assertEqual(self.kaynaklar["rekabet"].secenek["max_pages"], 10)
        self.assertTrue(self.kaynaklar["epdk"].secenek["fetch_text"])

        yeni_kayit = E.kayit_oku(os.path.join(cikti, AD, "ek", E.KAYIT_ADI), AD)
        self.assertEqual(yeni_kayit["belge"], 9)
        self.assertEqual([b["ref"] for b in yeni_kayit["metinsiz"]], ["rekabet:3"])
        self.assertIn("kvkk:9", yeni_kayit["kurumlar"]["kvkk"]["refler"])

        # Ek tabana uygulanınca yeni belgeler aranabilir, metni gelen kararın gövdesi değişir.
        ozet_u = E.uygula(self.indeks, os.path.join(cikti, AD, "ek"), AD, log=lambda m: None)
        self.assertEqual((ozet_u["yeni"], ozet_u["degisen"]), (4, 1))
        idx = retrieval.Index(self.indeks)
        self.assertEqual(idx.get("rekabet:2")["body"], UZUN + "kartel")
        self.assertEqual(idx._lexical_ladder("Kuruluş izni", {}, 5)[0], "all words")
        idx.db.close()

    def test_kuru_kosu_hicbir_sey_yazmaz(self):
        cikti = os.path.join(self.dizin, "kuru")
        ozet = ek_tarama.tara(self.veri, cikti, kuru=True, tarih="2026-11-13", aralik=0, kaynaklar=self.kaynaklar)
        self.assertIsNone(ozet["dosya"])
        self.assertEqual(ozet["belge"], 5)
        self.assertFalse(os.path.exists(cikti))

    def test_kayit_yoksa_acik_hata(self):
        os.remove(os.path.join(self.veri, AD, "ek", E.KAYIT_ADI))
        with self.assertRaises(E.EkHatasi):
            ek_tarama.tara(self.veri, os.path.join(self.dizin, "c"), aralik=0, kaynaklar=self.kaynaklar)


class DurumTest(unittest.TestCase):
    def test_status_ek_sayisini_gosterir(self):
        import server
        idx = retrieval.Index(os.path.join(tempfile.mkdtemp(), "t.db"))
        idx.upsert({"ref": "kvkk:1", "subject": "kvkk", "title": "t", "body": "b"})
        idx.db.commit()
        eski, eski_durum = server.index, server.retrieval.embeddings_status
        server.index = lambda: idx
        server.retrieval.embeddings_status = lambda *a, **k: {}
        try:
            self.assertNotIn("index_updates", server.t_status({})["local_index"])
            idx.set_state("ek", '{"documents": 103, "files": 1, "latest": "2026-11-10"}')
            self.assertEqual(server.t_status({})["local_index"]["index_updates"],
                             {"documents": 103, "files": 1, "latest": "2026-11-10"})
        finally:
            server.index, server.retrieval.embeddings_status = eski, eski_durum
            idx.db.close()


if __name__ == "__main__":
    unittest.main()
