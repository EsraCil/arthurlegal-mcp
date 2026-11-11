"""Deterministic source router — pick the 2-3 sources that fit the question.

The point of this module is that a query should **not** hit all eight discovery sources.
Blind fan-out is slow, rude to the upstreams, and would burn OpenAlex's metered
budget (~100 anonymous searches/day) on questions DOAJ answers for free.

Routing is rule-based, not model-based: no LLM call, no extra dependency, and
the same query always routes the same way, which makes behaviour auditable.

Signals, in order of authority:

1. **a DOI in the query** — go straight to the resolvers, skip discovery;
2. **an explicit ``source``** — the caller overrides everything;
3. **an explicit ``jurisdiction``/``language``** — deterministic mapping;
4. **language of the query text** — Spanish/Portuguese → SciELO+Dialnet, French
   → HAL, and so on;
5. **topic** — oil/gas/arbitration vocabulary pulls in Law Review Commons,
   whose subject sets are the best topical index of energy scholarship anywhere;
6. **fallbacks** — DOAJ always anchors, because it is cheap, burst-tolerant and
   the only source that *guarantees* peer review.

Every decision is reported back to the caller (``sources_skipped`` with a
reason), so nothing is silently dropped.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .sources import DISCOVERY, openalex

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")

# Jurisdiction -> sources that actually hold that country's scholarship.
_COUNTRY_SOURCES: Dict[str, List[str]] = {
    "FR": ["hal", "doaj", "openaire"],
    "ES": ["dialnet", "scielo", "doaj"],
    "PT": ["scielo", "doaj", "openaire"],
    "BR": ["scielo", "doaj"],
    "CL": ["scielo", "doaj"],
    "MX": ["scielo", "doaj"],
    "CO": ["scielo", "doaj"],
    "AR": ["scielo", "doaj"],
    "PE": ["scielo", "doaj"],
    "VE": ["scielo", "doaj"],
    "US": ["lawreviewcommons", "doaj", "openaire"],
    "GB": ["doaj", "openaire"],
    "DE": ["openaire", "doaj"],
    "IT": ["doaj", "openaire"],
    "NL": ["doaj", "openaire"],
    "PL": ["doaj", "openaire"],
    "RO": ["doaj", "openaire"],
    "RU": ["doaj", "openalex"],
    "UA": ["doaj", "openaire"],
    "TR": ["dergipark", "doaj", "openalex"],
    "ID": ["doaj"],
    "IR": ["doaj"],
    "EG": ["doaj"],
    "IQ": ["doaj"],
    "ZA": ["doaj", "openaire"],
    "KZ": ["doaj", "openalex"],
    "NG": ["doaj", "openaire"],
    "KE": ["doaj", "openaire"],
    "GH": ["doaj", "openaire"],
    "CZ": ["doaj", "openaire"],
    "HU": ["doaj", "openaire"],
    "HR": ["doaj", "openaire"],
    "RS": ["doaj", "openaire"],
    "GR": ["doaj", "openaire"],
    "BE": ["openaire", "doaj"],
    "AT": ["openaire", "doaj"],
    "SE": ["openaire", "doaj"],
    "NO": ["openaire", "doaj"],
    "FI": ["openaire", "doaj"],
    "DK": ["openaire", "doaj"],
    "IE": ["openaire", "doaj"],
    "CA": ["openalex", "doaj"],
    "AU": ["openalex", "doaj"],
    "NZ": ["openalex", "doaj"],
    "CN": ["openalex", "doaj"],
    "KR": ["openalex", "doaj"],
    "GE": ["openalex", "doaj"],
    "UZ": ["openalex", "doaj"],
    "SA": ["openalex", "doaj"],
    "QA": ["openalex", "doaj"],
    "IL": ["openalex", "doaj"],
    "CH": ["openaire", "doaj"],
    # Jurisdictions with zero DOAJ law journals: OpenAlex is the only way in.
    # (Verified: AZ has 0 DOAJ law journals but 193 OA law works in OpenAlex.)
    "AZ": ["openalex", "openaire"],
    "IN": ["openalex", "doaj"],
    "JP": ["openalex", "doaj"],
    "AE": ["openalex", "doaj"],
    "TM": ["openalex", "doaj"],
    "KG": ["openalex", "doaj"],
}

# Countries with no dedicated open-access law-journal index; OpenAlex is the
# only reliable route in, so the router must not fall back to DOAJ alone.
NO_DOAJ_LAW_JOURNALS = frozenset({"AZ", "IN", "JP", "AE", "TM", "KG", "QA", "SA"})

_LANG_SOURCES: Dict[str, List[str]] = {
    "fr": ["hal", "doaj"],
    "es": ["dialnet", "scielo", "doaj"],
    "pt": ["scielo", "doaj"],
    "en": ["doaj", "lawreviewcommons"],
    "tr": ["dergipark", "doaj", "openalex"],
    "de": ["openaire", "doaj"],
    "ru": ["doaj", "openalex"],
}

# Cheap language sniffing on distinctive function words / diacritics.
_LANG_HINTS: List[Tuple[str, Tuple[str, ...]]] = [
    ("es", (" derecho", " ley ", " contrato", " arbitraje", " petróleo", " minería", "jurídic")),
    ("pt", (" direito", " lei ", " contrato", " arbitragem", " petróleo", " mineração", "jurídic")),
    ("fr", (" droit", " loi ", " contrat", " arbitrage", " pétrole", " juridique", " énergie")),
    ("tr", (" hukuk", " sözleşme", " tahkim", " petrol", " enerji", " madencilik", " mevzuat")),
    ("de", (" recht", " gesetz", " vertrag", " schiedsverfahren", " energierecht")),
    ("ru", ("прав", "закон", "договор", "арбитраж", "нефт", "энерг")),
]

# Topic vocabulary that makes Law Review Commons worth the OAI cost.
_LRC_TOPICS = (
    "oil", "gas", "petroleum", "hydrocarbon", "mineral", "mining", "energy",
    "arbitration", "arbitral", "icsid", "uncitral", "investor-state",
    "production sharing", "psa", "concession", "pipeline", "lng", "opec",
    "natural resource", "extractive", "utility", "electricity", "renewable",
)


def detect_language(text: str) -> Optional[str]:
    """Best-effort language guess from distinctive legal vocabulary."""
    if not text:
        return None
    padded = " " + text.lower() + " "
    best, best_hits = None, 0
    for lang, hints in _LANG_HINTS:
        hits = sum(1 for h in hints if h in padded)
        if hits > best_hits:
            best, best_hits = lang, hits
    return best


def find_doi(text: str) -> Optional[str]:
    match = DOI_RE.search(text or "")
    return match.group(0).rstrip(".,;)") if match else None


def route(
    query: str = "",
    *,
    source: str = "auto",
    jurisdiction: Optional[str] = None,
    language: Optional[str] = None,
    peer_reviewed_only: bool = False,
    full_text_only: bool = False,
    max_sources: int = 3,
) -> Dict[str, Any]:
    """Decide which sources to query. Returns the plan and why."""
    skipped: List[Dict[str, str]] = []
    reasons: List[str] = []

    # 1. explicit override
    if source and source not in ("auto", "all"):
        picked = [s.strip() for s in source.split(",") if s.strip()]
        return _plan(picked, ["explicit source=%s" % source], skipped, None)

    if source == "all":
        picked = [s for s in DISCOVERY if not _skip_openalex(s, skipped)]
        return _plan(picked, ["source=all (explicit fan-out)"], skipped, None)

    # 2. a DOI short-circuits discovery entirely
    doi = find_doi(query)
    if doi:
        return _plan(["crossref", "unpaywall"],
                     ["query contains a DOI (%s) -> resolvers only" % doi],
                     skipped, doi)

    candidates: List[str] = []

    # 3. explicit jurisdiction wins over sniffing
    country = (jurisdiction or "").strip().upper()[:2] or None
    if country:
        mapped = _COUNTRY_SOURCES.get(country)
        if mapped:
            candidates += mapped
            reasons.append("jurisdiction=%s -> %s" % (country, ", ".join(mapped)))
        elif country in NO_DOAJ_LAW_JOURNALS:
            candidates += ["openalex", "openaire"]
            reasons.append("jurisdiction=%s has no open-access law journal index "
                           "-> openalex, openaire" % country)
        else:
            candidates += ["doaj", "openaire"]
            reasons.append("jurisdiction=%s not specially mapped -> doaj, openaire" % country)

    # 4. language (explicit, else sniffed from the query)
    lang = (language or "").strip().lower()[:2] or detect_language(query)
    if lang:
        mapped = _LANG_SOURCES.get(lang, [])
        if mapped:
            candidates += mapped
            reasons.append("language=%s -> %s" % (lang, ", ".join(mapped)))

    # 5. topical pull toward Law Review Commons — but it is a US-only corpus,
    #    so it must not gatecrash a query that was scoped to another country.
    low = (query or "").lower()
    if any(t in low for t in _LRC_TOPICS):
        if country and country != "US":
            skipped.append({"source": "lawreviewcommons",
                            "reason": "US-only corpus; query is scoped to %s" % country})
        else:
            candidates.append("lawreviewcommons")
            reasons.append("energy/arbitration vocabulary -> lawreviewcommons")

    # 6. DOAJ anchors by default: cheap, burst-tolerant, peer-review guaranteed.
    #    Skipped only where it demonstrably holds nothing for that jurisdiction.
    if country in NO_DOAJ_LAW_JOURNALS:
        skipped.append({"source": "doaj",
                        "reason": "no DOAJ law journals published in %s" % country})
    else:
        candidates.append("doaj")
        if "doaj" not in " ".join(reasons):
            reasons.append("doaj anchors every query (peer-review guaranteed)")

    picked: List[str] = []
    for name in candidates:
        if name in picked or name not in DISCOVERY:
            continue
        if peer_reviewed_only and name == "lawreviewcommons":
            skipped.append({"source": name,
                            "reason": "peer_reviewed_only=true and US law reviews are student-edited"})
            continue
        if full_text_only and name in ("dialnet", "doaj", "openaire", "openalex"):
            skipped.append({"source": name, "reason": "full_text_only=true and this source links out"})
            continue
        if _skip_openalex(name, skipped):
            continue
        picked.append(name)
        if len(picked) >= max_sources:
            break

    if not picked:
        picked = ["doaj"]
        reasons.append("all candidates filtered out -> doaj fallback")

    for name in DISCOVERY:
        if name not in picked and not any(s["source"] == name for s in skipped):
            skipped.append({"source": name, "reason": "not selected by router for this query"})

    return _plan(picked, reasons, skipped, None)


def _skip_openalex(name: str, skipped: List[Dict[str, str]]) -> bool:
    """Keep OpenAlex out when its metered budget is nearly gone."""
    if name == "openalex" and openalex.budget_low():
        skipped.append({
            "source": "openalex",
            "reason": "metered budget low (%s credits left, no API key) - set "
                      "OPENALEX_API_KEY to raise it" % openalex.budget().get("remaining"),
        })
        return True
    return False


def _plan(picked: List[str], reasons: List[str],
          skipped: List[Dict[str, str]], doi: Optional[str]) -> Dict[str, Any]:
    return {"sources": picked, "reasons": reasons, "skipped": skipped, "doi": doi}
