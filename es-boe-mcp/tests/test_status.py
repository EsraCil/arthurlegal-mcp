"""Status from the head of the text (F-197, 2026-10-26).

BOE's list flags an act only as 'vigencia agotada' or not; the index called every
other act 'vigente'. 158 of them open their consolidated text with BOE's own note
"Norma derogada, ...", so only_in_force=true returned repealed acts. Only an
unqualified repeal or annulment already in effect overrides the flag; a partial
one ("salvo", "en cuanto se oponga", "sin perjuicio de") or one taking effect
later stays 'vigente'. The heads below are verbatim from the index of 2026-10-10.

    python -m unittest discover -s tests          (from es-boe-mcp/)
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

import es_fakes
import retrieval

spec = importlib.util.spec_from_file_location("es_crawl_status", os.path.join(es_fakes.BACKEND, "crawl.py"))
crawl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(crawl)

TODAY = "2026-10-26"
DEROGADA = ("Norma derogada, con efectos de 1 de enero de 2022, por la disposición derogatoria única de "
            "la Ley 16/2021, de 20 de diciembre. Ref. BOE-A-2022-3413#dd\n\nPREÁMBULO")
CONGRESO = ("Norma derogada por Acuerdo del Congreso de los Diputados publicado por Resolución de 16 de "
            "septiembre de 2026. Ref. BOE-A-2026-19419\n\nI")
ANULADA = "Norma anulada por Sentencia del TS de 17 de diciembre de 2024. Ref. BOE-A-2025-3194.\n\nLa Ley"
SALVO = ("Norma derogada, salvo las disposiciones final 2 y adicional 1 y el Anexo V, por la disposición "
         "derogatoria 1 de la Ley 3/2020, de 27 de julio. Ref. BOE-A-2020-9793#dd.")
SE_OPONGA = ("Norma derogada, en cuanto se oponga, por la disposición final 1 de la Orden de 27 de febrero "
             "de 1986. Ref. BOE-A-1986-5257#df")
SIN_PERJUICIO = ("Norma derogada, con efectos de 7 de diciembre de 2018, sin perjuicio de lo previsto en la "
                 "disposición adicional 14 de la Ley Orgánica 3/2018, de 5 de diciembre, según establece su "
                 "disposición derogatoria única.1. Ref. BOE-A-2018-16673#dd")
FUTURA = ("Norma derogada, con efectos de 1 de marzo de 2027, por la disposición derogatoria única del Real "
          "Decreto 1/2026. Ref. BOE-A-2026-1")


class ActStatusTests(unittest.TestCase):
    def test_an_unqualified_repeal_in_effect_overrides_the_list_flag(self):
        self.assertEqual(crawl.act_status("vigente", DEROGADA, TODAY), "derogada")
        self.assertEqual(crawl.act_status("vigente", CONGRESO, TODAY), "derogada")
        self.assertEqual(crawl.act_status("vigente", ANULADA, TODAY), "anulada")

    def test_a_partial_saved_or_future_repeal_stays_in_force(self):
        for head in (SALVO, SE_OPONGA, SIN_PERJUICIO, FUTURA, "Norma modificada en lo que sea necesario", ""):
            self.assertEqual(crawl.act_status("vigente", head, TODAY), "vigente", head[:40])
        self.assertEqual(crawl.act_status("vigente", FUTURA, "2027-03-01"), "derogada")

    def test_boe_labels_are_kept_and_the_rule_is_idempotent(self):
        self.assertEqual(crawl.act_status("vigencia agotada", SALVO, TODAY), "vigencia agotada")
        self.assertEqual(crawl.act_status("", DEROGADA, TODAY), "")
        self.assertEqual(crawl.act_status("derogada", DEROGADA, TODAY), "derogada")
        self.assertEqual(crawl.act_status("derogada", SALVO, TODAY), "vigente")


class RepairStatusTests(unittest.TestCase):
    def setUp(self):
        self.idx = retrieval.Index(os.path.join(tempfile.mkdtemp(), "index.db"))
        self.addCleanup(self.idx.db.close)
        for ref, status, head in (("BOE-A-2016-8275", "vigente", DEROGADA), ("BOE-A-2018-8156", "vigente", SALVO),
                                  ("BOE-A-2023-3344", "vigencia agotada", ANULADA),
                                  ("BOE-A-1889-4763", "vigente", "")):
            body = "Ley\nJefatura del Estado" + (crawl.TEXT_MARK + head if head else "")
            doc_id = self.idx.upsert({"ref": ref, "title": ref, "body": body, "status": status, "lang": "es",
                                      "meta": {"text_chars": len(head)}})
            self.idx.db.execute("INSERT INTO vecs(doc_id, dim, vec, model) VALUES(?,?,?,?)",
                                (doc_id, 2, b"\x00" * 8, retrieval.embeddings_model()))
        self.idx.db.commit()

    def statuses(self):
        return dict(self.idx.db.execute("SELECT ref, status FROM docs").fetchall())

    def test_only_the_status_column_changes_and_vectors_stay(self):
        before = [tuple(r) for r in self.idx.db.execute("SELECT ref, title, body, meta FROM docs ORDER BY id")]
        self.assertEqual(crawl.repair_status(self.idx, TODAY), {"vigente -> derogada": 1})
        self.assertEqual(self.statuses(), {"BOE-A-2016-8275": "derogada", "BOE-A-2018-8156": "vigente",
                                           "BOE-A-2023-3344": "vigencia agotada", "BOE-A-1889-4763": "vigente"})
        after = [tuple(r) for r in self.idx.db.execute("SELECT ref, title, body, meta FROM docs ORDER BY id")]
        self.assertEqual(before, after)
        self.assertEqual(self.idx.db.execute("SELECT COUNT(*) FROM vecs").fetchone()[0], 4)
        self.assertEqual(crawl.repair_status(self.idx, TODAY), {})          # a rerun changes nothing

    def crawl_list(self, items):
        with mock.patch.object(crawl.BoeClient, "list_consolidated",
                               lambda self, limit=100, offset=0: [] if offset else items), \
                mock.patch.object(crawl.time, "sleep", lambda s: None):
            crawl.crawl(self.idx)

    def vectors(self):
        return sorted(r[0] for r in self.idx.db.execute("SELECT d.ref FROM vecs v JOIN docs d ON d.id = v.doc_id"))

    def test_a_recrawl_uses_the_head_and_leaves_unchanged_acts_and_their_vectors_alone(self):
        items = [{"id": ref, "title": ref, "rango": "Ley", "departamento": "Jefatura del Estado",
                  "url": "https://www.boe.es/buscar/act.php?id=" + ref, "status": "vigente"}
                 for ref in ("BOE-A-2016-8275", "BOE-A-2018-8156")]
        self.crawl_list(items)
        self.assertEqual(self.statuses()["BOE-A-2016-8275"], "derogada")   # list says vigente, head says no
        self.assertEqual(self.statuses()["BOE-A-2018-8156"], "vigente")
        # The seeded rows differ from the listed ones (citation, url), so those two were rewritten.
        self.assertEqual(self.vectors(), ["BOE-A-1889-4763", "BOE-A-2023-3344"])
        self.assertEqual(json.loads(self.idx.db.execute(
            "SELECT meta FROM docs WHERE ref = 'BOE-A-2016-8275'").fetchone()[0])["text_chars"], len(DEROGADA))
        self.idx.db.execute("INSERT INTO vecs(doc_id, dim, vec, model) SELECT id, 2, ?, ? FROM docs "
                            "WHERE id NOT IN (SELECT doc_id FROM vecs)", (b"\x00" * 8, retrieval.embeddings_model()))
        self.idx.db.commit()
        self.crawl_list(items)                                            # nothing changed upstream
        self.assertEqual(len(self.vectors()), 4, "an unchanged act keeps its vector on a re-crawl")


if __name__ == "__main__":
    unittest.main()
