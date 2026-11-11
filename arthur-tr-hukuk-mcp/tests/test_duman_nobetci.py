"""Duman testinin issue adımı (duman_nobetci.py). Ağsız, unittest: `gh` çağrılmaz.

    python -m unittest discover -s tests -p test_duman_nobetci.py -v      # arthur-tr-hukuk-mcp'den
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import duman_nobetci as nobetci  # noqa: E402
import duman_testi  # noqa: E402


def _sonuc(gun: str, **siniflar: str) -> dict:
    return {"zaman": gun + "T04:17:00+00:00",
            "sonuclar": [{"kaynak": k, "sinif": s, "adim": "arama", "ayrinti": "timed out" if s == "ERISIM" else ""}
                         for k, s in siniflar.items()]}


class Plan(unittest.TestCase):
    def test_hepsi_tamam(self):
        ac, durum = nobetci.planla(_sonuc("2026-11-05", ictihat="TAMAM", epdk="TAMAM"), {}, [])
        self.assertEqual((ac, durum["erisilemeyen_gunler"], durum["son_gun"]), ([], {}, "2026-11-05"))

    def test_uc_gun_erisilemezse_issue(self):
        durum: dict = {}
        acilan = []
        for gun in ("2026-11-05", "2026-11-06", "2026-11-06", "2026-11-07"):
            ac, durum = nobetci.planla(_sonuc(gun, epdk="ERISIM", kvkk="TAMAM"), durum, [])
            acilan.append([i["title"] for i in ac])
        baslik = nobetci.erisim_basligi("epdk")
        self.assertEqual(acilan, [[], [], [], [baslik]])
        self.assertEqual(durum["erisilemeyen_gunler"], {"epdk": ["2026-11-05", "2026-11-06", "2026-11-07"]})
        self.assertIn("05.11.2026, 06.11.2026, 07.11.2026", ac[0]["body"])
        self.assertIn("timed out", ac[0]["body"])
        sonraki = _sonuc("2026-11-08", epdk="ERISIM")
        self.assertEqual(nobetci.planla(sonraki, durum, [{"title": baslik, "state": "OPEN"}])[0], [])
        self.assertEqual(len(nobetci.planla(sonraki, durum, [{"title": baslik, "state": "CLOSED"}])[0]), 1)

    def test_tamam_sayaci_sifirlar(self):
        _, durum = nobetci.planla(_sonuc("2026-11-05", epdk="ERISIM", btk="ERISIM"), {}, [])
        _, durum = nobetci.planla(_sonuc("2026-11-06", epdk="TAMAM", btk="ERISIM"), durum, [])
        self.assertEqual(durum["erisilemeyen_gunler"], {"btk": ["2026-11-05", "2026-11-06"]})

    def test_denenmeyen_kaynagin_sayaci_kalir(self):
        _, durum = nobetci.planla(_sonuc("2026-11-05", epdk="ERISIM"), {}, [])
        _, durum = nobetci.planla(_sonuc("2026-11-06", kvkk="TAMAM"), durum, [])
        self.assertEqual(durum["erisilemeyen_gunler"], {"epdk": ["2026-11-05"]})

    def test_bozuk_kaynak_tek_issue(self):
        for sinif in nobetci.BOZUK:
            sonuc = _sonuc("2026-11-05", gib=sinif, kvkk="TAMAM")
            ac, durum = nobetci.planla(sonuc, {"erisilemeyen_gunler": {"gib": ["2026-11-04"]}}, [])
            baslik = nobetci.bozuk_basligi("gib")
            self.assertEqual([i["title"] for i in ac], [baslik], sinif)
            self.assertIn(sinif, ac[0]["body"])
            self.assertIn("05.11.2026", ac[0]["body"])
            self.assertEqual(durum["erisilemeyen_gunler"], {}, sinif)
            self.assertEqual(nobetci.planla(sonuc, {}, [{"title": baslik, "state": "OPEN"}])[0], [])
            self.assertEqual(len(nobetci.planla(sonuc, {}, [{"title": baslik, "state": "CLOSED"}])[0]), 1)

    def test_basliklar_kaynaga_ozel(self):
        ac, _ = nobetci.planla(_sonuc("2026-11-05", gib="BOS", btk="HATA"), {}, [])
        self.assertEqual(len({i["title"] for i in ac}), 2)


class Komut(unittest.TestCase):
    def _kos(self, sonuc) -> tuple:
        with tempfile.TemporaryDirectory() as d:
            s, dur = os.path.join(d, "sonuc.json"), os.path.join(d, "nobet", "durum.json")
            if sonuc is not None:
                with open(s, "w", encoding="utf-8") as fh:
                    json.dump(sonuc, fh)
            cikti = io.StringIO()
            eski = os.environ.pop("GITHUB_STEP_SUMMARY", None)
            try:
                with redirect_stdout(cikti):
                    kod = nobetci.main(["--sonuc", s, "--durum", dur, "--kuru"])
            finally:
                if eski is not None:
                    os.environ["GITHUB_STEP_SUMMARY"] = eski
            return kod, cikti.getvalue(), os.path.exists(dur)

    def test_kuru_kosu_yazmaz(self):
        kod, cikti, yazildi = self._kos(_sonuc("2026-11-05", gib="BOS", epdk="ERISIM"))
        self.assertEqual((kod, yazildi), (0, False))
        self.assertIn("Açılırdı (kuru): " + nobetci.bozuk_basligi("gib"), cikti)
        self.assertIn("epdk: üst üste 1 gün erişilemedi", cikti)

    def test_sonuc_yoksa_issue_yok(self):
        kod, cikti, yazildi = self._kos(None)
        self.assertEqual((kod, yazildi), (0, False))
        self.assertIn("Test sonucu okunamadı", cikti)
        self.assertNotIn("Açılırdı", cikti)


class Kontroller(unittest.TestCase):
    def test_epdk_dar_arama(self):
        """Piyasa verilmeyen EPDK araması soğuk önbellekte beş piyasa sayfasını ve ~126 listeyi art arda çeker."""
        epdk = [k for k in duman_testi.KONTROLLER if k[0] == "epdk"]
        self.assertEqual(len(epdk), 1)
        arg = epdk[0][2]()
        self.assertEqual(arg["kurum"], "epdk")
        self.assertIn(arg.get("market"), ("elektrik", "dogalgaz", "petrol", "lpg"))
        self.assertLessEqual(int(arg["params"]["max_categories"]), 5)


if __name__ == "__main__":
    unittest.main()
