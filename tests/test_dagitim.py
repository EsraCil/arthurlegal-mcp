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

_spec = importlib.util.spec_from_file_location(
    "canli_denetim", os.path.join(ROOT, ".github", "dagitim", "canli_denetim.py"))
canli_denetim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(canli_denetim)


def _akis() -> str:
    with open(AKIS, encoding="utf-8") as fh:
        return fh.read()


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
        self.assertIn("--dockerfile Dockerfile.code", metin)
        self.assertIn("cancel-in-progress: false", metin)

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
