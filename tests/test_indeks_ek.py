"""İndeks ekleri (indeks_ek.py): üretim, uygulama, eşdeğerlik, eşgüçlülük, bozuk dosya, start.sh sırası.

Küçük sahte indeksler, paylaşılan retrieval.Index şemasıyla (FTS5 + trigram + vektör); ağ yok.

    python -m unittest discover -s tests -p "test_indeks_ek.py"
"""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import indeks_ek as E  # noqa: E402

_spec = importlib.util.spec_from_file_location("retrieval_ek_testi", os.path.join(ROOT, "arthur-tr-hukuk-mcp",
                                                                                 "retrieval.py"))
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

AD = "pl-sejm-mcp"


def _belge(ref: str, title: str, body: str, **alanlar) -> dict:
    belge = {"ref": ref, "title": title, "body": body, "url": "https://ornek/" + ref, "lang": "tr",
             "date": "2026-01-01", "status": "", "court": "Kurul", "subject": "kurum",
             "citation": "Kurul, %s sayılı karar" % ref, "meta": {"no": ref}}
    belge.update(alanlar)
    return belge


def _vektor(db: sqlite3.Connection, kimlik: int) -> None:
    db.execute("INSERT OR REPLACE INTO vecs(doc_id, dim, vec, model) VALUES(?, 2, ?, 'm')",
               (kimlik, struct.pack("<2f", 0.6, 0.8)))


def _ozet(yol: str) -> str:
    db = sqlite3.connect(yol)
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
    with open(yol, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _ham_ek(yol: str, satirlar: list) -> None:
    """Denetimden geçmeyecek dosyalar için: satırları olduğu gibi yazar."""
    with gzip.open(yol, "wb") as fh:
        fh.write(b"".join((s if isinstance(s, bytes) else json.dumps(s, ensure_ascii=False).encode()) + b"\n"
                          for s in satirlar))


class Ortam(unittest.TestCase):
    """Taban dört belge; yeni indekste B'nin gövdesi değişmiş (vektörü gitmiş), C'nin yalnız tarihi ve atfı
    düzeltilmiş (vektörü kalmış), iki yeni belge (vektörsüz). Tazeleme betiğinin bıraktığı durum budur."""

    def setUp(self) -> None:
        self.dizin = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dizin, True)
        self.taban = os.path.join(self.dizin, "taban.db")
        idx = R.Index(self.taban)
        for belge in (_belge("A", "Kira kanunu", "kira artışı oranı"),
                      _belge("B", "İhale kararı", "kartel ihale"),
                      _belge("C", "Rekabet kararı", "rekabet ihlali", citation="eski atıf 2026"),
                      _belge("D", "Ceza kararı", "idari para cezası")):
            _vektor(idx.db, idx.upsert(belge))
        idx.reindex_fts()
        idx.set_state("last_crawl", "2026-10-10T00:00:00Z")
        idx.db.close()

        self.yeni = os.path.join(self.dizin, "yeni.db")
        shutil.copyfile(self.taban, self.yeni)
        idx = R.Index(self.yeni)
        idx.upsert(_belge("B", "İhale kararı", "kartel ihale tam metin gerekçe"))          # vektör silinir
        idx.db.execute("UPDATE docs SET date = '2026-01-02', citation = 'yeni atıf 2026' WHERE ref = 'C'")
        idx.upsert(_belge("E", "Yeni karar", "numara taşıma", meta={"no": "E", "ek": [1, 2]}))
        idx.upsert(_belge("F", "Başka yeni karar", "doğal gaz dağıtım lisansı"))
        idx.reindex_fts()
        idx.set_state("last_crawl", "2026-11-10T00:00:00Z")
        idx.set_state("coverage", "2015-2026")
        idx.db.commit()
        idx.db.close()
        self.klasor = os.path.join(self.dizin, AD, "ek")
        os.makedirs(self.klasor)

    def _ek(self, ad: str = "2026-11-10.jsonl.gz") -> str:
        baslik, belgeler = E.uret(self.taban, self.yeni, AD, ad[:10])
        yol = os.path.join(self.klasor, ad)
        E.dosya_yaz(yol, baslik, belgeler)
        return yol

    def _kopya(self) -> str:
        yol = os.path.join(self.dizin, "kopya.db")
        shutil.copyfile(self.taban, yol)
        return yol


class UretUygulaTest(Ortam):
    def test_uretim_yalniz_yeni_ve_degiseni_tasir(self) -> None:
        baslik, belgeler = E.uret(self.taban, self.yeni, AD, "2026-11-10")
        self.assertEqual([b["ref"] for b in belgeler], ["B", "C", "E", "F"])   # yeni indeksin sırası
        self.assertEqual((baslik["belge"], baslik["yeni"], baslik["degisen"]), (4, 2, 2))
        self.assertEqual(baslik["durum"], {"last_crawl": "2026-11-10T00:00:00Z", "coverage": "2015-2026"})
        self.assertEqual(belgeler[2]["meta"], {"no": "E", "ek": [1, 2]})

    def test_uygulanan_taban_yeni_indeksle_esdeger(self) -> None:
        self._ek()
        kopya = self._kopya()
        ozet = E.uygula(kopya, self.klasor, AD, log=lambda m: None)
        self.assertEqual({k: ozet[k] for k in ("dosya", "yeni", "degisen", "metin_disi", "vektor_silinen")},
                         {"dosya": 1, "yeni": 2, "degisen": 2, "metin_disi": 1, "vektor_silinen": 1})
        rapor = E.karsilastir(kopya, self.yeni, ["kartel ihale", "gerekçe", "numara taşıma", "yeni atıf",
                                                  "eski atıf", "lisans"])
        self.assertEqual(rapor["hatalar"], [])
        self.assertEqual(rapor["belge"], [6, 6])
        db = sqlite3.connect(kopya)
        for tablo in ("docs_fts", "docs_tri"):        # dizin içerikle tutarlı (FTS5'in kendi denetimi)
            db.execute("INSERT INTO %s(%s, rank) VALUES('integrity-check', 1)" % (tablo, tablo))
        vektorlu = {r[0] for r in db.execute("SELECT d.ref FROM vecs v JOIN docs d ON d.id = v.doc_id")}
        self.assertEqual(vektorlu, {"A", "C", "D"}, "metin dışı düzeltme (C) vektörü korur, gövde değişimi (B) siler")
        self.assertEqual(db.execute("SELECT count(*) FROM docs_fts WHERE docs_fts MATCH '\"eski\"'").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT d.ref FROM docs_fts f JOIN docs d ON d.id = f.rowid "
                                    "WHERE docs_fts MATCH '\"yeni\" \"atıf\"'").fetchall(), [("C",)])
        durum = dict(db.execute("SELECT k, v FROM state"))
        db.close()
        self.assertEqual(durum["last_crawl"], "2026-11-10T00:00:00Z")
        self.assertEqual(E.ek_durumu(durum["ek"]), {"files": 1, "documents": 4, "latest": "2026-11-10"})

    def test_ikinci_uygulama_hicbir_seyi_degistirmez(self) -> None:
        self._ek()
        kopya = self._kopya()
        E.uygula(kopya, self.klasor, AD, log=lambda m: None)
        once = _ozet(kopya)
        ozet = E.uygula(kopya, self.klasor, AD, log=lambda m: None)
        self.assertEqual((ozet["yeni"], ozet["degisen"], ozet["ayni"]), (0, 0, 4))
        self.assertEqual(_ozet(kopya), once, "aynı ek ikinci kez dosyayı değiştirdi")

    def test_ek_yoksa_indekse_dokunulmaz(self) -> None:
        kopya = self._kopya()
        with open(kopya, "rb") as fh:
            once = fh.read()
        for klasor in (os.path.join(self.dizin, "yok"), self.klasor):        # klasör yok / boş
            ozet = E.uygula(kopya, klasor, AD, log=lambda m: None)
            self.assertEqual(ozet["dosya"], 0)
        with open(kopya, "rb") as fh:
            self.assertEqual(fh.read(), once)
        self.assertFalse(os.path.exists(kopya + "-wal"))
        self._ek()
        olmayan = os.path.join(self.dizin, "olmayan.db")       # indeks kopyalanmamışsa boş dosya yaratılmaz
        self.assertTrue(E.uygula(olmayan, self.klasor, AD, log=lambda m: None)["hata"].startswith("indeks yok"))
        self.assertFalse(os.path.exists(olmayan))

    def test_okuyucu_acikken_uygulanir(self) -> None:
        """Sunucu okurken (açık okuma işlemi) ek yazılır; okuyucu kendi anlık görüntüsünü görmeye devam eder."""
        self._ek()
        kopya = self._kopya()
        okuyucu = sqlite3.connect(kopya, isolation_level=None)
        okuyucu.execute("PRAGMA journal_mode=WAL")
        okuyucu.execute("BEGIN")
        self.assertEqual(okuyucu.execute("SELECT count(*) FROM docs").fetchone()[0], 4)
        ozet = E.uygula(kopya, self.klasor, AD, log=lambda m: None)
        self.assertEqual(ozet["yeni"], 2)
        self.assertEqual(okuyucu.execute("SELECT count(*) FROM docs").fetchone()[0], 4)
        okuyucu.execute("COMMIT")
        self.assertEqual(okuyucu.execute("SELECT count(*) FROM docs").fetchone()[0], 6)
        okuyucu.close()

    def test_eski_ek_durumu_geri_almaz(self) -> None:
        """Taban ekten yeniyse (yeniden kuruldu, veri dalı sıfırlanmadı) last_crawl geri gitmez."""
        self._ek()
        kopya = self._kopya()
        db = sqlite3.connect(kopya)
        db.execute("UPDATE state SET v = '2026-12-01T00:00:00Z' WHERE k = 'last_crawl'")
        db.commit()
        db.close()
        E.uygula(kopya, self.klasor, AD, log=lambda m: None)
        db = sqlite3.connect(kopya)
        durum = dict(db.execute("SELECT k, v FROM state"))
        db.close()
        self.assertEqual(durum["last_crawl"], "2026-12-01T00:00:00Z")
        self.assertNotIn("coverage", durum)

    def test_ayni_gunun_ikinci_dosyasi_sonra_uygulanir(self) -> None:
        for ad, govde in (("2026-11-13-2.jsonl.gz", "ikinci"), ("2026-11-13.jsonl.gz", "birinci")):
            E.dosya_yaz(os.path.join(self.klasor, ad), {"ek": 1, "indeks": AD, "tarih": "2026-11-13", "durum": {}},
                        [_belge("B", "İhale kararı", govde)])
        self.assertEqual([os.path.basename(p) for p in E.ek_dosyalari(self.klasor)],
                         ["2026-11-13.jsonl.gz", "2026-11-13-2.jsonl.gz"])
        self.assertEqual(E.sonraki_ad(E.ek_dosyalari(self.klasor), "2026-11-13"), "2026-11-13-3.jsonl.gz")
        self.assertEqual(E.sonraki_ad([], "2026-11-16"), "2026-11-16.jsonl.gz")
        kopya = self._kopya()
        E.uygula(kopya, self.klasor, AD, log=lambda m: None)
        db = sqlite3.connect(kopya)
        self.assertEqual(db.execute("SELECT body FROM docs WHERE ref = 'B'").fetchone()[0], "ikinci")
        db.close()

    def test_silinen_ref_uretimi_durdurur(self) -> None:
        db = sqlite3.connect(self.yeni)
        db.execute("DELETE FROM docs WHERE ref = 'D'")
        db.commit()
        db.close()
        with self.assertRaises(E.EkHatasi):
            E.uret(self.taban, self.yeni, AD, "2026-11-10")
        self.assertEqual(E.uret(self.taban, self.yeni, AD, "2026-11-10", silinene_izin=True)[0]["belge"], 4)


class BozukDosyaTest(Ortam):
    def test_bozuk_dosya_atlanir_saglam_olan_uygulanir(self) -> None:
        saglam = self._ek()
        baslik, belgeler = E.dosya_oku(saglam, AD)
        with open(saglam, "rb") as fh:
            ham = fh.read()
        with open(os.path.join(self.klasor, "2026-11-11.jsonl.gz"), "wb") as fh:
            fh.write(ham[: len(ham) // 2])                                        # yarım gzip
        b = dict(baslik, tarih="2026-11-12", indeks="ie-statutebook-mcp")
        _ham_ek(os.path.join(self.klasor, "2026-11-12.jsonl.gz"), [b] + belgeler)  # başka indeksin
        b = dict(baslik, tarih="2026-11-13")
        _ham_ek(os.path.join(self.klasor, "2026-11-13.jsonl.gz"), [b, dict(belgeler[0], komut="rm -rf /")] + belgeler[1:])
        b = dict(baslik, tarih="2026-11-14")
        _ham_ek(os.path.join(self.klasor, "2026-11-14.jsonl.gz"), [b] + belgeler[:2])        # sayı tutmuyor
        b = dict(baslik, tarih="2026-11-15", belge=1)
        _ham_ek(os.path.join(self.klasor, "2026-11-15.jsonl.gz"), [b, dict(belgeler[0], title="x" * 30000)])
        _ham_ek(os.path.join(self.klasor, "2026-11-16.jsonl.gz"),
                [dict(baslik, tarih="2026-11-16", belge=1), b'{"ref": "Z", "meta": NaN}'])
        _ham_ek(os.path.join(self.klasor, "2026-11-17.jsonl.gz"), [dict(baslik, tarih="2026-11-18")])  # ad ≠ tarih
        for ad in ("ek.py", "2026-11-19.jsonl", "notlar.txt"):                   # ad biçimine uymayan: yok sayılır
            with open(os.path.join(self.klasor, ad), "w") as fh:
                fh.write("print('çalıştırılmamalı')")
        kopya = self._kopya()
        ozet = E.uygula(kopya, self.klasor, AD, log=lambda m: None)
        self.assertEqual(ozet["dosya"], 1)
        atlanan = sorted(a.split(":")[0] for a in ozet["atlanan"])
        self.assertEqual(atlanan, ["2026-11-%d.jsonl.gz" % g for g in range(11, 18)])
        self.assertEqual(E.karsilastir(kopya, self.yeni)["hatalar"], [])
        db = sqlite3.connect(kopya)
        durum = E.ek_durumu(dict(db.execute("SELECT k, v FROM state"))["ek"])
        db.close()
        self.assertEqual(len(durum["skipped_files"]), 7)
        for yol in E.ek_dosyalari(self.klasor):
            if os.path.basename(yol) != "2026-11-10.jsonl.gz":
                with self.assertRaises(E.EkHatasi):
                    E.dosya_oku(yol, AD)

    def test_cok_buyuk_dosya_acilmadan_reddedilir(self) -> None:
        yol = self._ek()
        eski = E.DOSYA_SINIRI
        E.DOSYA_SINIRI = os.path.getsize(yol) - 1
        try:
            with self.assertRaisesRegex(E.EkHatasi, "çok büyük"):
                E.dosya_oku(yol, AD)
        finally:
            E.DOSYA_SINIRI = eski

    def test_gzip_bombasi_sinirda_durur(self) -> None:
        yol = os.path.join(self.klasor, "2026-11-10.jsonl.gz")
        with gzip.open(yol, "wb") as fh:
            fh.write(b"\n" * 2_000_000)
        eski = E.ACIK_SINIRI
        E.ACIK_SINIRI = 1_000_000
        try:
            with self.assertRaisesRegex(E.EkHatasi, "aşıyor"):
                E.dosya_oku(yol, AD)
        finally:
            E.ACIK_SINIRI = eski

    def test_yazarken_de_denetlenir(self) -> None:
        with self.assertRaises(E.EkHatasi):
            E.dosya_yaz(os.path.join(self.klasor, "2026-11-10.jsonl.gz"),
                        {"ek": 1, "indeks": AD, "tarih": "2026-11-10"}, [dict(_belge("X", "t", "b"), fazla=1)])
        self.assertEqual(os.listdir(self.klasor), [])


class KayitTest(unittest.TestCase):
    def _kayit(self) -> dict:
        return {"kayit": 1, "indeks": "arthur-tr-hukuk-mcp", "guncelleme": "2026-11-10T00:00:00Z", "belge": 3,
                "kurumlar": {"rekabet": {"refler": ["rekabet:1", "rekabet:2"], "en_yeni": "2026-11-06"},
                             "bddk": {"refler": ["bddk:1"], "en_yeni": "2026-11-04"}},
                "metinsiz": [_belge("rekabet:2", "Başlık", "Başlık")]}

    def test_gidis_donus_ve_denetim(self) -> None:
        dizin = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, dizin, True)
        yol = os.path.join(dizin, E.KAYIT_ADI)
        E.kayit_yaz(yol, self._kayit())
        self.assertEqual(E.kayit_oku(yol, "arthur-tr-hukuk-mcp"), self._kayit())
        with self.assertRaises(E.EkHatasi):
            E.kayit_oku(yol, "pl-sejm-mcp")
        for bozuk in (dict(self._kayit(), belge=4),
                      dict(self._kayit(), kurumlar={"x": {"refler": ["a", "a"], "en_yeni": ""}}, belge=2),
                      dict(self._kayit(), metinsiz=[_belge("yok:1", "t", "t")]),
                      dict(self._kayit(), fazla=1)):
            with open(yol, "wb") as fh:
                fh.write(gzip.compress(json.dumps(bozuk).encode()))
            with self.assertRaises(E.EkHatasi):
                E.kayit_oku(yol, "arthur-tr-hukuk-mcp")


class AcilisTest(unittest.TestCase):
    """start.sh: ek önce, o indeksin eksik vektörleri sonra; anahtar yokken de ek uygulanır."""

    def test_ek_vektorlerden_once(self) -> None:
        with open(os.path.join(ROOT, "start.sh"), encoding="utf-8") as fh:
            metin = fh.read()
        self.assertIn('python "$APP/indeks_ek.py" uygula', metin)
        tr = metin.index("ek_uygula arthur-tr-hukuk-mcp")
        self.assertLess(tr, metin.index("crawl.py --embed-only"))
        self.assertLess(metin.index("if [ -f \"$APP/arthur-tr-hukuk-mcp/data/index.db\" ]; then"), tr)
        dongu = metin.index('ek_uygula "$j"')
        self.assertLess(dongu, metin.index("embed_missing()"))
        self.assertLess(dongu, metin.index('[ -n "$EMBEDDINGS_API_KEY" ] || continue'))
        self.assertLess(metin.index("ek_uygula() {"), tr)
        self.assertLess(tr, metin.index("exec python server.py"))


if __name__ == "__main__":
    unittest.main()
