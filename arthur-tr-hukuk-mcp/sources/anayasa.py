"""Anayasa Mahkemesi — norm denetimi ve bireysel başvuru (Kararlar Bilgi Bankası).

Both SPA hosts share one backend:

    POST https://normkararlarbilgibankasi.anayasa.gov.tr/api/core/public/search
    POST https://kararlarbilgibankasi.anayasa.gov.tr/api/core/public/search

    {"kararTipi": "NormDenetimi" | "BireyselBasvuru", "query": "...", "page": 1, "size": 10}
    {"kararTipi": ..., "id": "<uuid>", "page": 1, "size": 1}   -> data[0].icerik = full HTML

The SPA addresses a decision as base64url("kbb:" + uuid). ``source_url`` here is
that SPA link, so it opens in a browser.

Citation contract: ``AYM, E. 2021/123, K. 2022/45, 15.06.2022 (RG 20.07.2022/31898)``
for norm denetimi; ``AYM, <Başvurucu adı> Başvurusu, B. No: 2019/12345, 12.01.2023``
for bireysel başvuru. All from the record.

Endpoint discovery: saidsurucu/yargi-mcp (MIT).
"""

from __future__ import annotations

import base64
from typing import Any, Dict, Optional
from urllib.parse import quote

from net import Http, HttpError
from textx import html_to_text, paginate, strip_tags
from sources import Source

NORM_HOST = "https://normkararlarbilgibankasi.anayasa.gov.tr"
BB_HOST = "https://kararlarbilgibankasi.anayasa.gov.tr"
PATH = "/api/core/public/search"
TIPI = {"norm": "NormDenetimi", "bireysel": "BireyselBasvuru"}

_http = Http("", {"Accept": "application/json"}, allowed_domains=["anayasa.gov.tr"])


def _host(tipi: str) -> str:
    return NORM_HOST if tipi == "NormDenetimi" else BB_HOST


def _spa_url(tipi: str, uuid: str) -> str:
    tok = base64.urlsafe_b64encode(("kbb:" + uuid).encode()).decode().rstrip("=")
    return "%s/kbb/pages/search/%s?id=%s&type=%s" % (_host(tipi), tipi, quote(tok), tipi)


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    q = (args.get("query") or "").strip()
    kind = args.get("kind") or "norm"
    tipi = TIPI.get(kind)
    if not tipi:
        return {"error": "kind 'norm' veya 'bireysel' olmalı."}
    body: Dict[str, Any] = {"kararTipi": tipi, "page": max(1, int(args.get("page") or 1)),
                            "size": max(1, min(int(args.get("page_size") or 10), 50))}
    if q:
        body["query"] = q
    try:
        data = _http.post_json(_host(tipi) + PATH, body)
    except HttpError as exc:
        return {"error": str(exc)}
    items = []
    for r in data.get("data") or []:
        uuid = r.get("id") or ""
        item = {
            "id": uuid,
            "kind": kind,
            "karar_no": r.get("kararNo"),
            "esas_no": r.get("esasNo"),
            "basvuru_no": r.get("basvuruNo"),
            "karar_tarihi": (r.get("kararTarihi") or "")[:10],
            "rg": "%s/%s" % ((r.get("resmiGazeteTarihi") or "")[:10], r.get("resmiGazeteSayisi") or "") if r.get("resmiGazeteTarihi") else None,
            "subject": strip_tags(r.get("kararKonusu") or r.get("basvuruKonusu") or "")[:600],
            "applicant": r.get("basvurucu") or r.get("basvuran"),
            "source_url": _spa_url(tipi, uuid),
        }
        item["citation"] = _citation(item)
        items.append(item)
    return {"query": q, "kind": kind, "total": data.get("total", 0), "page": body["page"],
            "results": items, "note": "Tam metin: aym_getir(id, kind)."}


def _citation(it: Dict[str, Any]) -> str:
    if it["kind"] == "norm":
        s = "AYM"
        if it.get("esas_no"):
            s += ", E. %s" % it["esas_no"]
        if it.get("karar_no"):
            s += ", K. %s" % it["karar_no"]
        if it.get("karar_tarihi"):
            s += ", %s" % it["karar_tarihi"]
        if it.get("rg"):
            s += " (RG %s)" % it["rg"]
        return s
    s = "AYM"
    if it.get("applicant"):
        s += ", %s Başvurusu" % it["applicant"]
    if it.get("basvuru_no"):
        s += ", B. No: %s" % it["basvuru_no"]
    if it.get("karar_tarihi"):
        s += ", %s" % it["karar_tarihi"]
    return s


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    uuid = str(args.get("id") or "").strip()
    kind = args.get("kind") or "norm"
    tipi = TIPI.get(kind)
    if not uuid or not tipi:
        return {"error": "id ve kind ('norm' | 'bireysel') gerekli."}
    try:
        data = _http.post_json(_host(tipi) + PATH, {"kararTipi": tipi, "id": uuid, "page": 1, "size": 1})
    except HttpError as exc:
        return {"error": str(exc)}
    rows = data.get("data") or []
    if not rows:
        return {"error": "Karar bulunamadı: %s" % uuid}
    r = rows[0]
    text = html_to_text(r.get("icerik") or "")
    out = paginate(text, args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update({"id": uuid, "kind": kind, "karar_no": r.get("kararNo"), "esas_no": r.get("esasNo"),
                "basvuru_no": r.get("basvuruNo"), "karar_tarihi": (r.get("kararTarihi") or "")[:10],
                "source_url": _spa_url(tipi, uuid)})
    return out


SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Serbest metin (norm denetimi: kanun adı/madde; bireysel: hak, başvurucu)."},
        "kind": {"type": "string", "enum": ["norm", "bireysel"], "default": "norm"},
        "page": {"type": "integer", "minimum": 1, "default": 1},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
    },
    "required": ["query"],
}
GET_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "aym_ara sonucundaki id (UUID)"},
        "kind": {"type": "string", "enum": ["norm", "bireysel"], "default": "norm"},
        "page": {"type": "integer", "minimum": 1, "default": 1},
        "page_chars": {"type": "integer", "default": 8000},
    },
    "required": ["id"],
}

SOURCE = Source(
    key="aym",
    label="Anayasa Mahkemesi — norm denetimi + bireysel başvuru",
    kind="ictihat",
    notes=("kind='norm' iptal/itiraz davaları; kind='bireysel' temel hak başvuruları. "
           "Alıntı citation alanından; RG künyesi kayıttan gelir."),
    search=search, get=get, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage="https://kararlarbilgibankasi.anayasa.gov.tr",
)
