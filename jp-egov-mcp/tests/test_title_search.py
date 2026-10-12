"""Title matches lead `search_laws` (2026-10-11).

e-Gov's full-text index answers in law-number order, oldest first. 個人情報の保護
matched 513 statutes and the Act on the Protection of Personal Information was
not among the 20 on page one; its exact title found it nowhere either. The title
endpoint (`/laws?law_title=`) also matches abbreviations: 独禁法, 労基法.

    python -m unittest discover -s tests          (from jp-egov-mcp/)
"""

from __future__ import annotations

import unittest
from unittest import mock

import egov
import server


def _law(law_id, title, law_type="Act", abbrev="", repealed=False, status="CurrentEnforced"):
    return {"law_info": {"law_id": law_id, "law_type": law_type, "law_num": "", "promulgation_date": ""},
            "current_revision_info": {"law_title": title, "abbrev": abbrev, "law_type": law_type,
                                      "current_revision_status": status,
                                      "repeal_status": "Repeal" if repealed else "None"}}


TITLES = {"total_count": 4, "laws": [
    _law("C1", "個人情報の保護に関する法律施行令", "CabinetOrder", "個人情報保護法施行令"),
    _law("A2", "行政機関の保有する個人情報の保護に関する法律", abbrev="行政機関個人情報保護法", repealed=True),
    _law("A1", "個人情報の保護に関する法律", abbrev="個人情報保護法"),
    _law("A3", "独立行政法人等の保有する個人情報の保護に関する法律", abbrev="独立行政法人個人情報保護法"),
]}
KEYWORD = {"total_count": 513, "sentence_count": 900, "next_offset": 30, "items": [
    {"law_info": {"law_id": "OLD1", "law_type": "Act"}, "revision_info": {"law_title": "刑事補償法"},
     "sentences": [{"position": "第一条", "text": "<span>個人情報の保護</span>"}]},
    {"law_info": {"law_id": "A1", "law_type": "Act"}, "revision_info": {"law_title": "個人情報の保護に関する法律"},
     "sentences": [{"position": "第一条", "text": "この法律は、<span>個人情報の保護</span>"}]},
]}


def fake_get(path, params=None, timeout=90):
    return TITLES if path == "/laws" else KEYWORD


class TitleRankTests(unittest.TestCase):
    def test_exact_abbreviation_then_acts_in_force_then_shorter(self):
        with mock.patch.object(egov, "_get", fake_get):
            got = egov.EgovClient().search_titles("個人情報保護法")["results"]
        self.assertEqual([r["law_id"] for r in got], ["A1", "A3", "A2", "C1"])

    def test_a_numbered_title_and_a_previous_enforced_flag_do_not_bury_the_act(self):
        # e-Gov, 2026-10-12: the Antimonopoly Act's title starts with its law number, and its only
        # enforced revision is flagged PreviousEnforced; a 78-character special law outranked it.
        laws = {"total_count": 2, "laws": [
            _law("502AC32", "地域における一般乗合旅客自動車運送事業及び銀行業に係る基盤的なサービスの提供の"
                            "維持を図るための私的独占の禁止及び公正取引の確保に関する法律の特例に関する法律"),
            _law("322AC54", "昭和二十二年法律第五十四号（私的独占の禁止及び公正取引の確保に関する法律）",
                 abbrev="独禁法,独占禁止法", status="PreviousEnforced"),
        ]}
        with mock.patch.object(egov, "_get", lambda path, params=None, timeout=90: laws):
            client = egov.EgovClient()
            self.assertEqual(client.search_titles("私的独占の禁止")["results"][0]["law_id"], "322AC54")
            self.assertEqual(client.search_titles("私的独占の禁止及び公正取引の確保に関する法律")["results"][0]["law_id"],
                             "322AC54")

    def test_without_an_exact_match_the_act_leads_its_orders(self):
        with mock.patch.object(egov, "_get", fake_get):
            got = egov.EgovClient().search_titles("個人情報の保護")["results"]
        self.assertEqual(got[0]["law_id"], "A1")
        self.assertEqual(got[-1]["law_id"], "C1")


class SearchToolTests(unittest.TestCase):
    def test_title_matches_lead_and_keep_their_matched_sentences(self):
        with mock.patch.object(egov, "_get", fake_get):
            out = server._t_search({"keyword": "個人情報の保護"})
        ids = [r["law_id"] for r in out["results"]]
        self.assertEqual(ids[0], "A1")
        self.assertEqual(ids.count("A1"), 1, "a statute found both ways is listed once")
        self.assertEqual(out["results"][0]["match"], "title")
        self.assertTrue(out["results"][0]["matches"], "the full-text sentences move onto the title hit")
        self.assertEqual(out["results"][-1]["law_id"], "OLD1")
        self.assertEqual(out["results"][-1]["match"], "text")
        self.assertEqual(out["title_matches"], 4)

    def test_later_pages_are_full_text_only(self):
        calls = []

        def spy(path, params=None, timeout=90):
            calls.append(path)
            return fake_get(path, params, timeout)

        with mock.patch.object(egov, "_get", spy):
            out = server._t_search({"keyword": "個人情報の保護", "offset": 30})
        self.assertNotIn("/laws", calls)
        self.assertEqual(out["title_matches"], 0)

    def test_no_full_text_match_is_an_empty_page_not_an_error(self):
        def abbrev_only(path, params=None, timeout=90):
            if path == "/keyword":           # e-Gov: 取得結果が０件です
                raise egov.EgovNotFound("not found: /keyword")
            return TITLES

        with mock.patch.object(egov, "_get", abbrev_only):
            out = server._t_search({"keyword": "個人情報保護法"})
        self.assertEqual(out["results"][0]["law_id"], "A1")
        self.assertEqual(out["total_upstream"], 0)

        def nothing(path, params=None, timeout=90):
            raise egov.EgovNotFound("not found: %s" % path)

        with mock.patch.object(egov, "_get", nothing):
            self.assertEqual(server._t_search({"keyword": "zzqq"})["results"], [])

    def test_a_failing_title_search_still_returns_the_full_text_page(self):
        def half(path, params=None, timeout=90):
            if path == "/laws":
                raise egov.EgovError("e-Gov HTTP 500 on /laws")
            return KEYWORD

        with mock.patch.object(egov, "_get", half):
            out = server._t_search({"keyword": "個人情報の保護"})
        self.assertEqual(len(out["results"]), 2)
        self.assertIn("500", out["title_search_error"])


if __name__ == "__main__":
    unittest.main()
