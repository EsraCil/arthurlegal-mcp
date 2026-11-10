"""Otomatik Fly dağıtımının güvenlik kemeri: iş akışının bekçileri ve canlı denetimin kararları.

.github/workflows/fly-dagitim.yml yalnız bu deponun master'ına push'la gelen yeşil Testler koşusunda dağıtır ve
sırrı yalnız flyctl adımlarına verir. .github/dagitim/canli_denetim.py düşen araç sayısını, düşen indeks belge
sayısını ve eski imajda kalan makineyi hata sayar. Veri dalından (veri/ek) derlemeye yalnız <arka uç>/ek/
altındaki ek ve kayıt dosyaları, düzenli dosya olarak girer (.github/dagitim/ek_getir.py). Kurum kararı taraması
(kurum-taramasi.yml) her işte en dar yetkiyle çalışır. Ağa çıkmaz (git testleri yerel, geçici depolarla).

    python -m unittest tests.test_dagitim
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AKIS = os.path.join(ROOT, ".github", "workflows", "fly-dagitim.yml")
TARAMA = os.path.join(ROOT, ".github", "workflows", "kurum-taramasi.yml")

_BETIK = os.path.join(ROOT, ".github", "dagitim", "canli_denetim.py")
if not os.path.exists(_BETIK):
    # OSS Scanner imajı .github klasörünü almaz (.dockerignore); orada denetlenecek iş akışı yoktur.
    raise unittest.SkipTest(".github klasörü bu ortamda yok (imaj içi koşu)")

_spec = importlib.util.spec_from_file_location("canli_denetim", _BETIK)
canli_denetim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(canli_denetim)

_spec = importlib.util.spec_from_file_location("ek_getir", os.path.join(ROOT, ".github", "dagitim", "ek_getir.py"))
ek_getir = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ek_getir)


def _akis(yol: str = AKIS) -> str:
    with open(yol, encoding="utf-8") as fh:
        return fh.read()


def _adimlar(metin: str) -> list[str]:
    """Bir işin adımları, metin olarak (YAML ayrıştırıcısı yok: adımlar 6 boşluk + '- ' ile başlar)."""
    return [p for p in re.split(r"\n(?=      - )", metin.split("steps:", 1)[1]) if p.strip()]


def _is(metin: str, ad: str) -> str:
    """kurum-taramasi.yml'de bir işin bloğu (iki boşluklu '<ad>:' satırından bir sonrakine)."""
    bas = metin.index("\n  %s:\n" % ad)
    sonraki = re.search(r"\n  [a-z_]+:\n", metin[bas + 1:])
    return metin[bas: bas + 1 + sonraki.start()] if sonraki else metin[bas:]


def _betik() -> str:
    with open(os.path.join(ROOT, ".github", "dagitim", "dagit.sh"), encoding="utf-8") as fh:
        return fh.read()


class DeployRetryTest(unittest.TestCase):
    """dagit.sh yalnız derleme aşamasındaki hatada yeniden dener; sürüm/sağlık hatası doğrudan geri dönüşe gider."""

    def _derleme_hatasi(self, gunluk: str) -> bool:
        desen = re.search(r'grep -qE "([^"]+)"', _betik()).group(1)
        return re.search(desen, gunluk, re.M) is not None

    def test_builder_failures_are_retried(self) -> None:
        # 2026-11-07 ilk otomatik koşu: derleyici makinesi zamanında açılmadı
        self.assertTrue(self._derleme_hatasi(
            "✓ compatible remote builder found\n"
            "Error: failed to fetch an image or build from source: error building: failed to get status: "
            "rpc error: code = Unavailable\n"))
        self.assertTrue(self._derleme_hatasi("Error: failed to create remote builder: deadline_exceeded\n"))

    def test_release_failures_are_not_retried(self) -> None:
        self.assertFalse(self._derleme_hatasi(
            "✓ compatible remote builder found\n--> Build Summary:\nimage size: 2.6 GB\n"
            "Error: timeout reached waiting for health checks to pass for machine 85e991b4657008\n"))
        self.assertFalse(self._derleme_hatasi("✓ compatible remote builder found\n"))


class WorkflowGuardsTest(unittest.TestCase):
    def test_runs_only_for_pushes_to_this_repos_master(self) -> None:
        metin = _akis()
        for bekci in ("workflow_run.conclusion == 'success'", "workflow_run.event == 'push'",
                      "workflow_run.head_branch == 'master'",
                      "workflow_run.head_repository.full_name == github.repository",
                      "github.ref == 'refs/heads/master'"):
            self.assertIn(bekci, metin)

    def test_every_action_is_pinned_to_a_commit(self) -> None:
        for satir in re.findall(r"uses:\s*(\S+)", _akis()):
            self.assertRegex(satir, r"@[0-9a-f]{40}$")

    def test_secret_only_reaches_flyctl_steps(self) -> None:
        metin = _akis()
        self.assertNotIn("FLY_API_TOKEN", metin.split("steps:")[0].split("jobs:")[1])
        self.assertIn("bash .github/dagitim/dagit.sh", metin)
        self.assertIn("--dockerfile Dockerfile.code", _betik())
        self.assertIn("cancel-in-progress: false", metin)

    def test_rollback_also_runs_when_the_run_is_cancelled(self) -> None:
        metin = _akis()
        self.assertIn("(failure() || cancelled()) && steps.once.outcome == 'success'", metin)
        self.assertIn("(failure() || cancelled()) && steps.geri.outcome == 'success'", metin)

    def test_only_master_head_is_deployed(self) -> None:
        self.assertIn("git ls-remote origin refs/heads/master", _akis())


class LiveCheckDecisionsTest(unittest.TestCase):
    def test_fewer_tools_is_an_error_unless_allowed(self) -> None:
        once = {"/mcp": 130, "/directory/mcp": 107}
        self.assertEqual(canli_denetim.karsilastir(once, {"/mcp": 130, "/directory/mcp": 107}, False), [])
        self.assertEqual(canli_denetim.karsilastir(once, {"/mcp": 131, "/directory/mcp": 107}, False), [])
        self.assertEqual(len(canli_denetim.karsilastir(once, {"/mcp": 129, "/directory/mcp": 107}, False)), 1)
        self.assertEqual(canli_denetim.karsilastir(once, {"/mcp": 129, "/directory/mcp": 107}, True), [])

    def test_empty_list_is_an_error_even_without_a_before(self) -> None:
        self.assertEqual(len(canli_denetim.karsilastir({}, {"/mcp": 0, "/directory/mcp": 107}, True)), 1)
        self.assertEqual(canli_denetim.karsilastir({}, {"/mcp": 130, "/directory/mcp": 107}, False), [])

    def test_every_started_machine_must_run_the_new_image(self) -> None:
        def makine(i, durum, etiket):
            return {"id": i, "state": durum, "config": {"image": f"registry.fly.io/arthurlegal-mcp:{etiket}"}}

        yeni = "gh-abc1234"
        self.assertEqual(canli_denetim.makineler_tamam([makine("a", "started", yeni), makine("b", "started", yeni)],
                                                       yeni), [])
        self.assertEqual(len(canli_denetim.makineler_tamam([makine("a", "started", yeni),
                                                            makine("b", "started", "eski")], yeni)), 1)
        self.assertEqual(canli_denetim.makineler_tamam([makine("a", "started", yeni),
                                                        makine("b", "stopped", "eski")], yeni), [])
        self.assertEqual(canli_denetim.makineler_tamam([makine("a", "stopped", yeni)], yeni), ["çalışan makine yok"])

    def test_index_documents_must_not_drop(self) -> None:
        durum = {"backend_status": {"tr": {"local_index": {"documents": 19599, "path": "x"}},
                                    "pl": {"indexed_documents": 28313}, "nl": {"indexed_decisions": 7050},
                                    "jp": {"tools": 5}, "de": "hata"}}
        self.assertEqual(canli_denetim.belge_sayilari(durum), {"tr": 19599, "pl": 28313, "nl": 7050})
        once = {"tr": 19599, "pl": 28313}
        self.assertEqual(canli_denetim.belgeleri_karsilastir(once, {"tr": 19600, "pl": 28313}, False), [])
        self.assertEqual(len(canli_denetim.belgeleri_karsilastir(once, {"tr": 19498, "pl": 28313}, False)), 1)
        self.assertEqual(canli_denetim.belgeleri_karsilastir(once, {"tr": 19498, "pl": 28313}, True), [])
        self.assertEqual(len(canli_denetim.belgeleri_karsilastir(once, {"pl": 28313}, True)), 1)


class IndexUpdateDeployTest(unittest.TestCase):
    """fly-dagitim.yml: ekler sırsız adımda gelir, derlemeden önce; etiket veri dalının ucunu taşır."""

    def test_secret_only_in_flyctl_steps(self) -> None:
        for adim in _adimlar(_akis()):
            if "FLY_API_TOKEN" in adim:
                self.assertTrue("flyctl" in adim or "dagit.sh" in adim, adim[:200])
        ek = next(a for a in _adimlar(_akis()) if "ek_getir.py" in a)
        self.assertNotIn("secrets.", ek)
        self.assertNotIn("FLY_API_TOKEN", ek)
        self.assertIn("python .github/dagitim/ek_getir.py --hedef .", ek)

    def test_updates_are_fetched_before_the_build(self) -> None:
        metin = _akis()
        self.assertLess(metin.index("ek_getir.py"), metin.index("bash .github/dagitim/dagit.sh"))
        self.assertIn('--etiket "$ETIKET"', metin)
        self.assertIn('etiket="${ETIKET:-gh-${SHA::7}}"', _betik())
        self.assertIn('etiket="$etiket-ek-${EK_SHA::7}"', metin)

    def test_fetch_script_never_checks_out_the_data_branch(self) -> None:
        """Veri dalı çalışma ağacına alınmaz, birleştirilmez: kod getiremez, kodun üstüne yazamaz."""
        with open(os.path.join(ROOT, ".github", "dagitim", "ek_getir.py"), encoding="utf-8") as fh:
            betik = fh.read()
        for yasak in ('"checkout"', '"switch"', '"merge"', '"pull"', '"worktree"', '"reset"', "shell=True",
                      "exec(", "eval(", "pickle"):
            self.assertNotIn(yasak, betik)
        self.assertIn('open(cikti, "xb")', betik)


class DataBranchPathsTest(unittest.TestCase):
    """Daldan yalnız <arka uç>/ek/ altındaki ek ve kayıt, yalnız 100644 (yürütülebilir değil, bağ değil)."""

    def test_only_index_update_paths(self) -> None:
        kabul = ("arthur-tr-hukuk-mcp/ek/2026-11-10.jsonl.gz", "arthur-tr-hukuk-mcp/ek/2026-11-10-2.jsonl.gz",
                 "arthur-tr-hukuk-mcp/ek/kayit.json.gz", "pl-sejm-mcp/ek/2026-11-10.jsonl.gz")
        for yol in kabul:
            self.assertEqual(ek_getir.izinli(yol, "100644", "blob"), yol.split("/")[0], yol)
        red = ("arthur-tr-hukuk-mcp/server.py", "start.sh", ".github/workflows/x.yml", "indeks_ek.py",
               "arthur-tr-hukuk-mcp/ek/ek.py", "arthur-tr-hukuk-mcp/ek/2026-11-10.jsonl",
               "arthur-tr-hukuk-mcp/ek/../server.py", "../arthur-tr-hukuk-mcp/ek/2026-11-10.jsonl.gz",
               "arthur-tr-hukuk-mcp/data/ek/2026-11-10.jsonl.gz", "olmayan-arka-uc/ek/2026-11-10.jsonl.gz",
               "arthur-tr-hukuk-mcp/ek/2026-11-10-0.jsonl.gz", "Arthur/ek/2026-11-10.jsonl.gz",
               "arthur-tr-hukuk-mcp/ek/alt/2026-11-10.jsonl.gz")
        for yol in red:
            self.assertIsNone(ek_getir.izinli(yol, "100644", "blob"), yol)
        tamam = "arthur-tr-hukuk-mcp/ek/2026-11-10.jsonl.gz"
        for kip, tur in (("100755", "blob"), ("120000", "blob"), ("160000", "commit"), ("040000", "tree")):
            self.assertIsNone(ek_getir.izinli(tamam, kip, tur), kip)


def _git(kok: str, *args: str, girdi: bytes | None = None) -> str:
    ortam = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                 GIT_COMMITTER_EMAIL="t@t")
    sonuc = subprocess.run(["git", *args], cwd=kok, input=girdi, capture_output=True, env=ortam, check=True)
    return sonuc.stdout.decode().strip()


@unittest.skipIf(shutil.which("git") is None, "git yok")
class FetchFromDataBranchTest(unittest.TestCase):
    """ek_getir.getir gerçek git ile: yerel 'origin', veri/ek dalında izinli ve izinsiz girdiler."""

    def setUp(self) -> None:
        self.E = ek_getir.indeks_ek
        self.dizin = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dizin, True)
        kaynak = os.path.join(self.dizin, "kaynak")
        os.makedirs(os.path.join(kaynak, "arthur-tr-hukuk-mcp"))
        with open(os.path.join(kaynak, "arthur-tr-hukuk-mcp", "server.py"), "w") as fh:
            fh.write("# kod\n")
        _git(kaynak, "init", "-q", "-b", "master")
        _git(kaynak, "add", ".")
        _git(kaynak, "commit", "-q", "-m", "kod")
        self.kaynak = kaynak
        self.klon = os.path.join(self.dizin, "klon")
        _git(self.dizin, "clone", "-q", "file:///" + kaynak.replace("\\", "/").lstrip("/"), "klon")

    def _veri_dali(self, girdiler: dict) -> None:
        """girdiler: yol -> (kip, içerik). Dal, dosya sisteminden bağımsız kurulur (bağ ve kip dahil)."""
        indeks = os.path.join(self.dizin, "indeks")
        os.environ["GIT_INDEX_FILE"] = indeks
        try:
            for yol, (kip, icerik) in girdiler.items():
                nesne = _git(self.kaynak, "hash-object", "-w", "--stdin", girdi=icerik)
                _git(self.kaynak, "update-index", "--add", "--cacheinfo", "%s,%s,%s" % (kip, nesne, yol))
            agac = _git(self.kaynak, "write-tree")
            commit = _git(self.kaynak, "commit-tree", agac, "-m", "veri")
            _git(self.kaynak, "update-ref", "refs/heads/veri/ek", commit)
        finally:
            del os.environ["GIT_INDEX_FILE"]

    def _ek_bayt(self, ad: str = "arthur-tr-hukuk-mcp", tarih: str = "2026-11-10") -> bytes:
        yol = os.path.join(self.dizin, "%s.jsonl.gz" % tarih)
        belge = {"ref": "rekabet:1", "title": "t", "body": "b", "url": "", "lang": "tr", "date": "", "status": "",
                 "court": "", "subject": "rekabet", "citation": "", "meta": {}}
        self.E.dosya_yaz(yol, {"ek": 1, "indeks": ad, "tarih": tarih}, [belge])
        with open(yol, "rb") as fh:
            return fh.read()

    def test_no_branch_means_no_updates(self) -> None:
        hedef = os.path.join(self.dizin, "hedef")
        self.assertEqual(ek_getir.getir(hedef, False, kok=self.klon)["dal"], False)
        with self.assertRaises(ek_getir.Hata):
            ek_getir.getir(hedef, True, kok=self.klon)

    def test_only_allowed_files_arrive(self) -> None:
        ek = self._ek_bayt()
        kayit = gzip.compress(json.dumps({
            "kayit": 1, "indeks": "arthur-tr-hukuk-mcp", "guncelleme": "x", "belge": 1,
            "kurumlar": {"rekabet": {"refler": ["rekabet:1"], "en_yeni": ""}}, "metinsiz": []}).encode())
        self._veri_dali({
            "arthur-tr-hukuk-mcp/ek/2026-11-10.jsonl.gz": ("100644", ek),
            "arthur-tr-hukuk-mcp/ek/kayit.json.gz": ("100644", kayit),
            "arthur-tr-hukuk-mcp/ek/2026-11-11.jsonl.gz": ("100755", ek),        # yürütülebilir kip
            "arthur-tr-hukuk-mcp/ek/2026-11-12.jsonl.gz": ("120000", b"../../start.sh"),   # bağ
            "arthur-tr-hukuk-mcp/server.py": ("100644", b"import os; os.system('x')"),
            "start.sh": ("100644", b"#!/bin/sh\nrm -rf /\n"),
            ".github/workflows/x.yml": ("100644", b"on: push"),
            "baska/ek/2026-11-10.jsonl.gz": ("100644", ek),                      # bu depoda olmayan arka uç
        })
        hedef = os.path.join(self.dizin, "hedef")
        sonuc = ek_getir.getir(hedef, True, kok=self.klon)
        self.assertEqual(sorted(sonuc["dosyalar"]), ["arthur-tr-hukuk-mcp/ek/2026-11-10.jsonl.gz",
                                                     "arthur-tr-hukuk-mcp/ek/kayit.json.gz"])
        yazilan = sorted(os.path.relpath(os.path.join(k, d), hedef).replace("\\", "/")
                         for k, _, dosyalar in os.walk(hedef) for d in dosyalar)
        self.assertEqual(yazilan, sorted(sonuc["dosyalar"]))
        self.assertEqual(len(sonuc["yok_sayilan"]), 6)
        with open(os.path.join(self.klon, "arthur-tr-hukuk-mcp", "server.py")) as fh:
            self.assertEqual(fh.read(), "# kod\n", "kod değişmemeli")
        self.assertEqual(_git(self.klon, "status", "--porcelain"), "")

    def test_invalid_update_stops_the_deploy(self) -> None:
        self._veri_dali({"arthur-tr-hukuk-mcp/ek/2026-11-10.jsonl.gz": ("100644", self._ek_bayt("pl-sejm-mcp"))})
        with self.assertRaisesRegex(ek_getir.Hata, "denetimden geçmedi"):
            ek_getir.getir(os.path.join(self.dizin, "hedef"), False, kok=self.klon)


class ScanWorkflowTest(unittest.TestCase):
    """kurum-taramasi.yml: zamanlama, kuru koşu, işlere göre en dar yetki, yazma yalnız master'da."""

    def test_schedule_and_dry_run(self) -> None:
        metin = _akis(TARAMA)
        self.assertIn('cron: "30 3 * * 1,4"', metin)
        self.assertIn("workflow_dispatch:", metin)
        self.assertRegex(metin, r"kuru:\n\s+description:[^\n]+\n\s+type: boolean")

    def test_every_action_is_pinned_to_a_commit(self) -> None:
        for satir in re.findall(r"uses:\s*(\S+)", _akis(TARAMA)):
            self.assertRegex(satir, r"@[0-9a-f]{40}$")

    def test_least_privilege_per_job(self) -> None:
        metin = _akis(TARAMA)
        self.assertIn("permissions:\n  contents: read\n", metin.split("jobs:")[0])
        tara, yaz, tetikle = _is(metin, "tara"), _is(metin, "yaz"), _is(metin, "tetikle")
        self.assertIn("permissions:\n      contents: read\n", tara)
        self.assertNotIn("write", tara)
        self.assertIn("persist-credentials: false", tara)
        self.assertIn("permissions:\n      contents: write\n      actions: read\n", yaz)
        self.assertIn("permissions:\n      actions: write\n", tetikle)
        self.assertNotIn("contents:", tetikle)
        self.assertNotIn("secrets.", metin)
        for blok in (yaz, tetikle):
            self.assertNotIn("ek_tarama.py", blok)        # kaynak siteleri işleyen kod yazma yetkisiyle koşmaz

    def test_writes_only_from_master_to_the_data_branch(self) -> None:
        metin = _akis(TARAMA)
        yaz = _is(metin, "yaz")
        self.assertIn("github.ref == 'refs/heads/master'", yaz)
        self.assertIn("github.event_name != 'push'", yaz)
        self.assertIn("!inputs.kuru", yaz)
        self.assertIn("git -C veri push origin HEAD:refs/heads/veri/ek", yaz)
        self.assertEqual(re.findall(r"push origin \S+", metin), ["push origin HEAD:refs/heads/veri/ek"])
        self.assertIn("python kod/indeks_ek.py dogrula", yaz)
        self.assertIn("needs: yaz", _is(metin, "tetikle"))
        self.assertIn("fly-dagitim.yml/dispatches", _is(metin, "tetikle"))
        self.assertIn("-f ref=master", _is(metin, "tetikle"))

    def test_scan_packages_match_the_image(self) -> None:
        with open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8") as fh:
            pypdf = re.search(r'"pypdf==([^"]+)"', fh.read()).group(1)
        self.assertIn('"pypdf==%s"' % pypdf, _akis(TARAMA))

    def test_tests_do_not_run_on_the_data_branch(self) -> None:
        self.assertIn('"!veri/**"', _akis(os.path.join(ROOT, ".github", "workflows", "testler.yml")))


if __name__ == "__main__":
    unittest.main()
