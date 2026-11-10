"""Otomatik Fly dağıtımının güvenlik kemeri: iş akışının bekçileri ve canlı denetimin kararları.

.github/workflows/fly-dagitim.yml yalnız bu deponun master'ına push'la gelen yeşil Testler koşusunda dağıtır ve
sırrı yalnız flyctl adımlarına verir. .github/dagitim/canli_denetim.py düşen araç sayısını ve eski imajda kalan
makineyi hata sayar. Ağa çıkmaz.

    python -m unittest tests.test_dagitim
"""

from __future__ import annotations

import importlib.util
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AKIS = os.path.join(ROOT, ".github", "workflows", "fly-dagitim.yml")

_BETIK = os.path.join(ROOT, ".github", "dagitim", "canli_denetim.py")
if not os.path.exists(_BETIK):
    # OSS Scanner imajı .github klasörünü almaz (.dockerignore); orada denetlenecek iş akışı yoktur.
    raise unittest.SkipTest(".github klasörü bu ortamda yok (imaj içi koşu)")

_spec = importlib.util.spec_from_file_location("canli_denetim", _BETIK)
canli_denetim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(canli_denetim)


def _akis() -> str:
    with open(AKIS, encoding="utf-8") as fh:
        return fh.read()


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


if __name__ == "__main__":
    unittest.main()
