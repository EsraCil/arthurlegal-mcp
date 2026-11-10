"""Doktrin ve sözleşme aramasında yeniden sıralama havuzu ve sözleşme facet'leri (F-198). Ağa çıkmaz.

Üst kaynaklar sıralamıyor: DOAJ eşleşmeleri kimlik sırasıyla, ResourceContracts tek kelimelik sorguda
hepsini aynı puanla döndürüyor. limit=3 ile havuz 9 (doktrin) ve 12 (sözleşme) adayda kalıyor, konuya
uyan kayıt havuza hiç girmiyordu. Facet'ler sayı değil, sayfadaki farklı değerlerdir.

    python -m unittest discover -s tests -p "test_siralama_havuzu.py"
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for alt in ("lex-scholar-api", "resourcecontracts-api"):
    if os.path.join(ROOT, alt) not in sys.path:
        sys.path.insert(0, os.path.join(ROOT, alt))

from lexscholar import mcp_server as scholar  # noqa: E402
from resourcecontracts import client as rc_client  # noqa: E402
from resourcecontracts import mcp_server as contracts  # noqa: E402


def _sahte_sirala(query, docs, fields=(), limit=0):
    """semantic_rerank yerine: embeddings sağlayıcısına gitmez, sırayı korur."""
    return {"method": "sahte", "results": list(docs)[: limit or len(docs)]}


class ScholarPoolTest(unittest.TestCase):
    def ara(self, **args):
        istenen = []

        def search(query, limit=20, **kw):
            istenen.append(limit)
            return {"results": [{"title": "t%d" % i} for i in range(limit)]}

        with mock.patch.object(scholar._client, "search", search), \
                mock.patch.object(scholar, "semantic_rerank", _sahte_sirala):
            out = scholar._t_search_articles(dict({"query": "data protection"}, **args))
        return istenen[0], out

    def test_small_limit_still_reranks_a_real_pool(self):
        istenen, out = self.ara(limit=3, max_sources=2)
        self.assertEqual(istenen, scholar._POOL_MIN)
        self.assertEqual(out["ranking"]["candidates_considered"], scholar._POOL_MIN)
        self.assertEqual(out["returned"], 3)

    def test_large_limits_keep_the_multiplier_and_the_cap(self):
        self.assertEqual(self.ara(limit=20)[0], 60)
        self.assertEqual(self.ara(limit=100)[0], scholar._POOL_MAX)

    def test_no_rerank_asks_for_the_limit_only(self):
        self.assertEqual(self.ara(limit=3, rerank=False)[0], 3)


class ContractsPoolTest(unittest.TestCase):
    def ara(self, **args):
        istenen = []

        def search(query, per_page=20, **kw):
            istenen.append(per_page)
            return {"total": 5126, "per_page": per_page, "facets": {},
                    "results": [{"name": "c%d" % i} for i in range(per_page)]}

        with mock.patch.object(contracts._client, "search", search), \
                mock.patch.object(contracts, "semantic_rerank", _sahte_sirala):
            out = contracts._t_search_contracts(args)
        return istenen[0], out

    def test_small_page_still_reranks_a_real_pool(self):
        istenen, out = self.ara(query="mining", per_page=3)
        self.assertEqual(istenen, contracts._POOL_MIN)
        self.assertEqual(out["ranking"]["candidates_considered"], contracts._POOL_MIN)
        self.assertEqual(len(out["results"]), 3)
        self.assertEqual(self.ara(query="mining", per_page=20)[0], 80)

    def test_browsing_returns_the_page_asked_for(self):
        # Boş sorgu sıralanmaz; önceden 4 katı aday olduğu gibi dönüyordu.
        istenen, out = self.ara(per_page=3)
        self.assertEqual(istenen, 3)
        self.assertEqual(len(out["results"]), 3)
        self.assertEqual(self.ara(query="mining", per_page=3, rerank=False)[0], 3)


class ContractsFacetTest(unittest.TestCase):
    def test_facets_are_lists_of_values_not_counts(self):
        # Canlı yanıtın biçimi (2026-11-10, q=mining): değerin sayfadaki ilk sırası anahtar olur.
        upstream = {"total": 5126, "country": {"44": "AO", "10": "AU", "0": "ST"},
                    "year": {"81": 1960, "4": 1997}, "resource": ["Hydrocarbons"], "results": []}
        c = rc_client.ResourceContractsClient()
        with mock.patch.object(c, "_get_json", lambda path, params=None: upstream):
            facets = c.search("mining", per_page=3)["facets"]
        self.assertEqual(facets, {"country": ["AO", "AU", "ST"], "year": [1960, 1997],
                                  "resource": ["Hydrocarbons"]})
        self.assertIsNone(rc_client._facet_values(None))


if __name__ == "__main__":
    unittest.main()
