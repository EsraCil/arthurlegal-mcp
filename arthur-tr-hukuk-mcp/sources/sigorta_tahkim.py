"""Sigorta Tahkim Komisyonu — Hakem Karar Dergisi (sigortatahkim.org).

The Commission publishes its arbitrator decisions in quarterly PDF journals,
issue 1 (2010) to issue 66 (2026-09), ~1.8–3.5 MB each:

    https://www.sigortatahkim.org/content/CmsFiles/karardrgs<N>.pdf
    (issue 4: karardergisisayi4.pdf; issues 57–61: revizekd<N>.pdf)

Inside, every decision starts with a heading like
``12.03.2024 Tarih ve K-2024/12345 Sayılı Hakem Kararı``; that regex splits an
issue into decisions. The reference implementation finds issues through the
Tavily search API (paid key). This adapter needs no key: it indexes every
decision of every issue locally and searches within an issue live.

Citation contract: ``Sigorta Tahkim Komisyonu, 12.03.2024 tarih ve K-2024/12345 sayılı Hakem Kararı (Hakem Karar Dergisi S. 64)``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from net import Http, HttpError
from textx import iso_tarih, paginate, pdf_to_text, count_hits, excerpt
from sources import Source

BASE = "https://www.sigortatahkim.org"
_http = Http(BASE, {"Accept": "application/pdf,*/*"})
LATEST_KNOWN = 66
# Heading spellings seen across 66 issues (2010–2026):
#   "12.03.2024 Tarih ve K-2024/12345 Sayılı Hakem Kararı"          (recent)
#   "07/09/2015 tarih ve K.2015/8207 Sayılı Hakem Kararı"           (2015, slashes, dot)
#   "09.04.2025 Tarih – K-2025/180058 Sayılı Hakem Kararı"          (dash instead of "ve")
#   "21/09/2021 Tarihli - 2021/İHK-30910 Sayılı İtiraz Hakem Heyeti Kararı"
_HEAD = re.compile(
    r"(\d{2}[./]\d{2}[./]\d{4}\s+Tarih(?:li)?\s*(?:ve|[-–—])\s*"
    r"(?:K[-.]\s?\d{4}\s?/\s?\d+|\d{4}/[A-ZİÇŞĞÜÖ]{2,5}-\d+)\s+Sayılı\s+"
    r"(?:İtiraz\s+)?Hakem\s+(?:Heyeti\s+)?Kararı)", re.I)
_cache: Dict[int, str] = {}


def pdf_url(issue: int) -> str:
    if issue == 4:
        name = "karardergisisayi4.pdf"
    elif 57 <= issue <= 61:
        name = "revizekd%d.pdf" % issue
    else:
        name = "karardrgs%d.pdf" % issue
    return "%s/content/CmsFiles/%s" % (BASE, name)


def _text(issue: int) -> str:
    if issue in _cache:
        return _cache[issue]
    body, _, _ = _http.get(pdf_url(issue), timeout=180)
    text, _ = pdf_to_text(body)
    if len(_cache) > 4:
        _cache.clear()
    _cache[issue] = text
    return text


def split_decisions(text: str, min_len: int = 800) -> List[Dict[str, str]]:
    parts = _HEAD.split(text)
    out = []
    for i in range(1, len(parts) - 1, 2):
        head, body = parts[i].strip(), parts[i + 1].strip()
        if len(body) >= min_len:
            out.append({"heading": head, "body": body})
    return out


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    """Live: search inside one issue (default: latest known)."""
    issue = int(args.get("issue") or LATEST_KNOWN)
    q = (args.get("query") or "").strip()
    try:
        decisions = split_decisions(_text(issue))
    except HttpError as exc:
        return {"error": str(exc), "issue": issue}
    terms = [t for t in q.split() if len(t) > 1]
    scored = []
    for d in decisions:
        hits = sum(count_hits(d["body"], t) for t in terms) if terms else 1
        if hits:
            scored.append((hits, d))
    scored.sort(key=lambda x: -x[0])
    limit = max(1, min(int(args.get("limit") or 8), 30))
    return {"issue": issue, "query": q, "decisions_in_issue": len(decisions), "matches": len(scored),
            "results": [{"heading": d["heading"], "hits": h, "excerpt": excerpt(d["body"], terms[0] if terms else "", 350),
                         "chars": len(d["body"]),
                         "citation": "Sigorta Tahkim Komisyonu, %s (Hakem Karar Dergisi S. %d)" % (d["heading"], issue)}
                        for h, d in scored[:limit]],
            "source_url": pdf_url(issue),
            "note": "Tek sayı içinde arama. Tüm sayılarda (1–%d) konu araması için yerel indeks: semantik_ara(kurum='sigorta_tahkim'). "
                    "Karar tam metni: kurum_karari_getir(kurum='sigorta_tahkim', id='<issue>:<heading>')." % LATEST_KNOWN}


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    ref = str(args.get("id") or "").strip()
    issue_s, _, heading = ref.partition(":")
    if not issue_s.isdigit():
        return {"error": "id biçimi '<sayı>:<karar başlığı>' veya '<sayı>' (tüm dergi, sayfalı)."}
    issue = int(issue_s)
    try:
        text = _text(issue)
    except HttpError as exc:
        return {"error": str(exc)}
    if heading:
        for d in split_decisions(text):
            if d["heading"].lower() == heading.strip().lower() or heading.strip().lower() in d["heading"].lower():
                out = paginate(d["heading"] + "\n\n" + d["body"], args.get("page") or 1, int(args.get("page_chars") or 8000))
                out.update({"issue": issue, "heading": d["heading"], "source_url": pdf_url(issue)})
                return out
        return {"error": "Bu sayıda başlık bulunamadı: %s" % heading, "issue": issue}
    out = paginate(text, args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update({"issue": issue, "source_url": pdf_url(issue)})
    return out


def crawl(index, issues: Optional[List[int]] = None, log=print) -> Dict[str, Any]:
    n = 0
    for issue in issues or range(1, LATEST_KNOWN + 1):
        if getattr(index, "exists_prefix", lambda p: False)("stk:%d:" % issue):
            continue
        try:
            text = _text(issue)
        except HttpError as exc:
            log("sigorta_tahkim: issue %d failed: %s" % (issue, exc))
            continue
        decs = split_decisions(text)
        for d in decs:
            index.upsert({"ref": "stk:%d:%s" % (issue, d["heading"][:80]), "title": d["heading"],
                          "body": d["body"][:80000], "url": pdf_url(issue), "lang": "tr",
                          "date": iso_tarih(d["heading"]),
                          "status": "dergi %d" % issue, "court": "Sigorta Tahkim Komisyonu", "subject": "sigorta",
                          "citation": "Sigorta Tahkim Komisyonu, %s (Hakem Karar Dergisi S. %d)" % (d["heading"], issue),
                          "meta": {"kurum": "sigorta_tahkim", "issue": issue}})
            n += 1
        log("sigorta_tahkim: issue %d, %d decisions (total %d)" % (issue, len(decs), n))
        _cache.pop(issue, None)
    return {"indexed": n}


SEARCH_SCHEMA = {"type": "object", "properties": {
    "query": {"type": "string"}, "issue": {"type": "integer", "description": "Dergi sayısı 1–%d (varsayılan son sayı)" % LATEST_KNOWN},
    "limit": {"type": "integer", "default": 8}}}
GET_SCHEMA = {"type": "object", "properties": {"id": {"type": "string", "description": "'<sayı>:<karar başlığı>' veya '<sayı>'"},
                                               "page": {"type": "integer", "default": 1},
                                               "page_chars": {"type": "integer", "default": 8000}},
              "required": ["id"]}

SOURCE = Source(
    key="sigorta_tahkim", label="Sigorta Tahkim Komisyonu — Hakem Karar Dergisi", kind="kurum",
    notes=("66 dergi sayısı (2010–2026), her sayıda onlarca hakem kararı. Canlı arama tek sayıda; arşiv için yerel indeks. "
           "İtiraz Hakem Heyeti kararları da dergidedir; başlıktaki 'K-' numarası verbatim alıntılanır."),
    search=search, get=get, crawl=crawl, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage=BASE,
)
