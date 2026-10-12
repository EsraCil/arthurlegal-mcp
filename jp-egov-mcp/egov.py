#!/usr/bin/env python3
"""Client for Japan's e-Gov 法令API v2 — https://laws.e-gov.go.jp/api/2

Official, no key, JSON. Publishes the consolidated text of Japanese statutes and
cabinet orders, with the amendment and repeal metadata needed to say whether a
provision is actually in force.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

BASE = "https://laws.e-gov.go.jp/api/2"
UA = "ArthurLegal-MCP/1.0 (+https://github.com/beerbottle90/arthurlegal-mcp)"

LAW_ID = re.compile(r"^[0-9A-Z]{6,}$")

# A statute before the orders made under it, when several titles contain the keyword.
_TYPE_ORDER = {t: i for i, t in enumerate(
    ("Constitution", "Act", "CabinetOrder", "ImperialOrder", "MinisterialOrdinance", "Rule", "Misc"))}
# Some titles carry the law number in front: 昭和二十二年法律第五十四号（私的独占の禁止及び公正取引の確保に関する法律）.
_NUMBERED_TITLE = re.compile(r"^(?:明治|大正|昭和|平成|令和)\S*?号（(.+)）$")


class EgovError(RuntimeError):
    pass


class EgovNotFound(EgovError):
    """HTTP 404. On a search it means no result (e-Gov: 取得結果が０件です), not a missing endpoint."""


def _get(path: str, params: Optional[Dict[str, Any]] = None, timeout: int = 90) -> Dict[str, Any]:
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in ("", None)})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise EgovNotFound("not found: %s" % path) from exc
        detail = ""
        try:
            detail = (json.loads(exc.read().decode("utf-8")) or {}).get("message", "")
        except Exception:  # noqa: BLE001 - the body is best-effort context
            pass
        if exc.code == 400 and "asof" in detail:
            # e-Gov keeps point-in-time revisions only from 2017-04-01 and says so
            # in Japanese. Passing that through untranslated would read as a bug
            # rather than as the coverage limit it is.
            raise EgovError(
                "e-Gov has no point-in-time revisions before 2017-04-01, so `as_of` "
                "cannot answer for a date earlier than that. Upstream said: %s" % detail
            ) from exc
        raise EgovError("e-Gov HTTP %d on %s%s"
                        % (exc.code, path, (" - " + detail) if detail else "")) from exc
    except urllib.error.URLError as exc:
        raise EgovError("e-Gov unreachable: %s" % exc.reason) from exc
    except ValueError as exc:
        raise EgovError("e-Gov returned non-JSON") from exc


def _text(node: Any) -> str:
    """Flatten the tag/children tree to plain text."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "".join(_text(c) for c in (node.get("children") or []))
    if isinstance(node, list):
        return "".join(_text(c) for c in node)
    return ""


def _find(node: Any, tag: str, out: List[Dict[str, Any]], limit: int = 0) -> None:
    if limit and len(out) >= limit:
        return
    if isinstance(node, dict):
        if node.get("tag") == tag:
            out.append(node)
        for child in (node.get("children") or []):
            _find(child, tag, out, limit)
    elif isinstance(node, list):
        for child in node:
            _find(child, tag, out, limit)


def _force(rev: Dict[str, Any]) -> Dict[str, Any]:
    """The in-force picture, assembled from fields that are easy to read wrongly.

    `remain_in_force` does NOT mean "this law is in force". In e-Gov's model it
    is 残存効力 -- whether a *repealed* law keeps residual effect through
    transitional provisions. Live statutes carry False here: the Local Autonomy
    Act, plainly in force, reports remain_in_force=False. Reading it as the
    in-force flag marks the entire live corpus as repealed.

    The authoritative pair is `current_revision_status` (CurrentEnforced) and
    `repeal_status` ("None" when never repealed). They are reported alongside
    the verdict so a caller can check the reasoning rather than trust it.
    """
    repeal_status = rev.get("repeal_status") or ""
    revision_status = rev.get("current_revision_status") or ""
    repealed = bool(rev.get("repeal_date")) or repeal_status not in ("", "None", "NotRepealed")
    return {
        "in_force": (not repealed) and revision_status == "CurrentEnforced",
        "repeal_status": repeal_status,
        "repeal_date": rev.get("repeal_date") or "",
        "current_revision_status": revision_status,
        # Residual effect of an already-repealed law, not a live-status flag.
        "residual_effect_after_repeal": bool(rev.get("remain_in_force")),
        "amendment_enforcement_date": rev.get("amendment_enforcement_date", ""),
        # A statute can be in force today while carrying an amendment that is
        # not. Reporting only the first would present a future rule as current.
        "amendment_scheduled_enforcement_date": rev.get("amendment_scheduled_enforcement_date", ""),
        "amendment_law_title": rev.get("amendment_law_title", ""),
    }


def _summary(law_info: Dict[str, Any], rev: Dict[str, Any]) -> Dict[str, Any]:
    law_id = law_info.get("law_id", "")
    title = rev.get("law_title", "")
    return {
        "law_id": law_id,
        "title": title,
        "title_kana": rev.get("law_title_kana", ""),
        "abbrev": rev.get("abbrev", ""),
        "law_num": law_info.get("law_num", ""),
        "law_type": law_info.get("law_type", ""),
        "promulgation_date": law_info.get("promulgation_date", ""),
        "category": rev.get("category", ""),
        "status": _force(rev),
        "source_url": "https://laws.e-gov.go.jp/law/%s" % law_id if law_id else "",
        "citation": "%s（%s）" % (title, law_info.get("law_num", "")) if title else law_id,
    }


class EgovClient:
    def search(self, keyword: str, max_sentences: int = 30, offset: int = 0) -> Dict[str, Any]:
        """Full-text keyword search, grouped by statute.

        e-Gov's ``limit`` counts matching SENTENCES, not statutes: limit=3 came
        back as three sentences inside a single Act, while limit=30 spanned five.
        Asking for "10 results" and receiving one statute looks like a thin
        corpus; it is only a thin page. The parameter is named for what it
        actually bounds, and the response reports both counts.
        """
        if not (keyword or "").strip():
            raise EgovError("keyword is required")
        try:
            data = _get("/keyword", {"keyword": keyword,
                                     "limit": max(1, min(int(max_sentences), 100)),
                                     "offset": max(0, int(offset))})
        except EgovNotFound:
            # No sentence matches: an abbreviation such as 独禁法 is never written in a
            # statute. This used to reach the caller as "not found: /keyword".
            data = {}
        results = []
        for item in data.get("items", []):
            rec = _summary(item.get("law_info") or {}, item.get("revision_info") or {})
            snippets = []
            for s in (item.get("sentences") or [])[:5]:
                # The API marks hits with <span>; keep the text, drop the markup.
                snippets.append({"position": s.get("position", ""),
                                 "text": re.sub(r"</?span[^>]*>", "", s.get("text", ""))})
            rec["matches"] = snippets
            results.append(rec)
        return {
            "total": data.get("total_count", 0),
            "sentence_count": data.get("sentence_count", 0),
            "statutes_on_this_page": len(results),
            "next_offset": data.get("next_offset"),
            "results": results,
        }

    def search_titles(self, keyword: str, limit: int = 10) -> Dict[str, Any]:
        """Statutes whose title or abbreviation contains ``keyword``, best first.

        ``/keyword`` is a full-text index returned in law-number order, oldest
        first. A phrase that many statutes quote fills its first page with old
        Acts and the statute itself never shows: 個人情報の保護 matched 513
        statutes and the Act on the Protection of Personal Information was not
        among the 20 on page one (2026-10-11). ``/laws?law_title=`` matches titles
        and abbreviations (独禁法, 労基法, 個人情報保護法). Order: exact title or
        abbreviation, then Acts before the orders made under them, unrepealed before
        repealed, shorter title first.
        """
        kw = (keyword or "").strip()
        if not kw:
            return {"total": 0, "results": []}
        try:
            data = _get("/laws", {"law_title": kw, "limit": 100})
        except EgovNotFound:
            data = {}
        found = []
        for item in data.get("laws", []):
            rev = item.get("current_revision_info") or item.get("revision_info") or {}
            found.append(_summary(item.get("law_info") or {}, rev))

        def rank(r: Dict[str, Any]) -> tuple:
            m = _NUMBERED_TITLE.match(r["title"])
            bare = m.group(1) if m else r["title"]
            exact = kw in (r["title"], bare) or kw in (r.get("abbrev") or "").split(",")
            # Repeal, not the in-force verdict: e-Gov marks the only enforced revision of a few
            # live statutes PreviousEnforced (the Antimonopoly Act, 2026-10-12).
            repealed = bool(r["status"]["repeal_date"]) or \
                r["status"]["repeal_status"] not in ("", "None", "NotRepealed")
            return (not exact, _TYPE_ORDER.get(r["law_type"], len(_TYPE_ORDER)), repealed, len(bare))

        found.sort(key=rank)
        return {"total": data.get("total_count", 0), "results": found[:max(1, int(limit))]}

    def list_laws(self, limit: int = 20, offset: int = 0) -> Dict[str, Any]:
        data = _get("/laws", {"limit": max(1, min(int(limit), 100)),
                              "offset": max(0, int(offset))})
        out = []
        for item in data.get("laws", []):
            rev = item.get("current_revision_info") or item.get("revision_info") or {}
            out.append(_summary(item.get("law_info") or {}, rev))
        return {"total": data.get("total_count", 0),
                "next_offset": data.get("next_offset"),
                "results": out}

    def get_law(self, law_id: str, as_of: str = "") -> Dict[str, Any]:
        law_id = (law_id or "").strip().upper()
        if not LAW_ID.match(law_id):
            raise EgovError("law_id looks wrong: %r (e.g. 417AC0000000086)" % law_id)
        data = _get("/law_data/%s" % urllib.parse.quote(law_id),
                    {"asof": as_of} if as_of else None)
        rec = _summary(data.get("law_info") or {}, data.get("revision_info") or {})
        rec["as_of"] = as_of or "current"
        rec["_full_text"] = data.get("law_full_text")
        return rec

    def get_text(self, law_id: str, as_of: str = "",
                 offset: int = 0, max_chars: int = 20000) -> Dict[str, Any]:
        rec = self.get_law(law_id, as_of)
        body = _text(rec.pop("_full_text", None))
        total = len(body)
        offset = max(0, int(offset))
        max_chars = max(500, min(int(max_chars), 100000))
        rec["text"] = body[offset:offset + max_chars]
        rec["total_chars"] = total
        rec["offset"] = offset
        rec["next_offset"] = offset + max_chars if offset + max_chars < total else None
        return rec

    def get_article(self, law_id: str, article: str, as_of: str = "") -> Dict[str, Any]:
        """One article by its arabic number, e.g. "331" for 第三百三十一条."""
        rec = self.get_law(law_id, as_of)
        tree = rec.pop("_full_text", None)
        nodes: List[Dict[str, Any]] = []
        _find(tree, "Article", nodes)
        wanted = str(article).strip()
        for node in nodes:
            if str((node.get("attr") or {}).get("Num", "")) == wanted:
                caption, title = [], []
                _find(node, "ArticleCaption", caption, 1)
                _find(node, "ArticleTitle", title, 1)
                rec["article"] = wanted
                rec["article_title"] = _text(title[0]) if title else ""
                rec["article_caption"] = _text(caption[0]) if caption else ""
                rec["text"] = _text(node)
                return rec
        raise EgovError(
            "article %s not found in %s. The statute has %d articles; note that "
            "e-Gov numbers inserted articles as e.g. '331_2' (第三百三十一条の二)."
            % (wanted, law_id, len(nodes)))
