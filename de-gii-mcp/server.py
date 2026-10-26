#!/usr/bin/env python3
"""de-gii-mcp — German federal law from gesetze-im-internet.de, norm by norm. Stdlib only.

    python server.py                                 # stdio
    python server.py --transport http --port 8095    # http://127.0.0.1:8095/mcp

The aggregator loads it under the de_ prefix next to the proxied de-eli tools:
de-eli answers from NeuRIS, this answers for the acts NeuRIS does not have yet.
No crawl and no index: an act is fetched when first asked for and kept in memory.
"""
from __future__ import annotations

from typing import Any, Dict

import gii
from mcpcore import McpError, Tool, run

__version__ = gii.__version__

WARNING = "UYARI: veri çekilemedi, teyidiniz gerekli: https://www.gesetze-im-internet.de/"

INSTRUCTIONS = """Alman federal kanun metni: gesetze-im-internet.de (Federal Adalet Bakanlığı ve juris).

NE ZAMAN. Bir Alman kanun maddesinin metni gerektiğinde norm_getir kullanın:
kanun="BGB", norm="823". NeuRIS (de_search, de_get_text) test aşamasındadır ve BGB,
HGB, StGB, ZPO, StPO, AO, UrhG, GWB, InsO gibi temel kanunları henüz içermez;
norm_getir bunların güncel konsolide metnini verir. Kısaltmasını bilmediğiniz kanunu
gesetz_ara ile adından bulun ve dönen slug'ı norm_getir'e verin.

ATIF. citation ("§ 823 BGB") ve source_url alanlarını birebir kullanın; stand alanı
son değişikliği gösterir. Metin konsolide ve resmî olmayan metindir; resmî olan
yalnız Bundesgesetzblatt'taki yayımdır. Çekilemeyen maddeyi ezberden yazmayın;
"%s" satırını kullanın.""" % WARNING


def _t_norm(args: Dict[str, Any]) -> Any:
    kanun = str(args.get("kanun") or "").strip()
    nr = str(args.get("norm") or "").strip()
    if not kanun or not nr:
        raise McpError("kanun ve norm gerekli: kanun='BGB', norm='823' (madde için Art. numarası, ör. '1').")
    try:
        return gii.norm(kanun, nr, page=int(args.get("page") or 1))
    except gii.GiiError as exc:
        out: Dict[str, Any] = {"error": str(exc), "kanun": kanun, "norm": nr}
        if exc.candidates:
            out["candidates"] = exc.candidates
        if exc.status is None and not exc.candidates and "unreachable" in str(exc):
            out["warning"] = WARNING
        return out


def _t_search(args: Dict[str, Any]) -> Any:
    query = str(args.get("sorgu") or "").strip()
    if not query:
        raise McpError("sorgu gerekli: kanunun adı ya da adından bir parça (ör. 'Gesetzbuch').")
    try:
        hits = gii.search_laws(query, int(args.get("limit") or 10))
    except gii.GiiError as exc:
        return {"error": str(exc), "warning": WARNING}
    return {"query": query, "returned": len(hits), "results": hits,
            "note": "Maddeyi getirmek için norm_getir(kanun=<slug ya da kısaltma>, norm=...)."}


def _t_status(args: Dict[str, Any]) -> Any:
    return gii.status()


TOOLS = [
    Tool("norm_getir",
         "Alman federal kanun maddesinin güncel konsolide metni, gesetze-im-internet.de'den: "
         "kanun='BGB', norm='823' ('§ 556d' ya da Art. için '1' de olur). Başlık, Stand satırı, "
         "atıf ve maddenin kendi sayfasıyla döner. NeuRIS'te olmayan BGB, HGB, StGB, ZPO, StPO, "
         "AO, UrhG, GWB, InsO burada var.",
         {"type": "object", "properties": {
             "kanun": {"type": "string",
                       "description": "Kısaltma (BGB, StGB, GmbHG), sayfa adı (bgb, ao_1977) ya da tam ad."},
             "norm": {"type": "string", "description": "Madde: '823', '§ 823', '556d'; Art. için '1'."},
             "page": {"type": "integer", "minimum": 1, "default": 1,
                      "description": "Çok uzun maddeler 20.000 karakterlik sayfalara bölünür."}},
          "required": ["kanun", "norm"]},
         _t_norm),
    Tool("gesetz_ara",
         "gesetze-im-internet.de içindekiler listesinde kanun adı arar (yaklaşık 6.100 kanun ve "
         "yönetmelik). Kısaltmasını bilmediğiniz kanunun sayfa adını (slug) bulur; norm_getir'e verin.",
         {"type": "object", "properties": {
             "sorgu": {"type": "string", "description": "Kanun adı ya da adından bir parça."},
             "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}},
          "required": ["sorgu"]},
         _t_search),
    Tool("server_status", "Kaynağın erişilebilirliği ve bellekteki kanunlar.",
         {"type": "object", "properties": {}}, _t_status),
]


if __name__ == "__main__":
    run(TOOLS, name="de-gii-mcp", version=__version__, instructions=INSTRUCTIONS)
