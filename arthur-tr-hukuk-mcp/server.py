#!/usr/bin/env python3
"""arthur-tr-hukuk-mcp (eski adı ArthurLegalTR) — Türk hukuku MCP sunucusu: içtihat, mevzuat, düzenleyici kurum
kararları ve semantik arama tek uçta.

    python server.py                                  # stdio (Claude Desktop, Claude Code)
    python server.py --transport http --port 8080     # http://127.0.0.1:8080/mcp

Why this exists
---------------
Turkish public law lives on seventeen different websites with seventeen
different ideas of what an API is. Said Sürücü's yargi-mcp / mevzuat-mcp
(MIT) proved the upstream endpoints; this server takes that knowledge, drops
the framework and the paid search keys (Brave, Tavily, OpenRouter), adds the
sector regulators the energy / finance practice actually cites — EPDK, SPK,
BDDK, Sigorta Tahkim — and puts a local FTS5 + vector index behind them
so a question asked in plain Turkish finds a decision that shares none of its
words.

Standard library only. ``pypdf`` is the one optional dependency (PDF text).

Tool families
-------------
``ictihat_*``      Yargıtay, Danıştay, BAM, yerel mahkeme, KYB   (Bedesten, live)
``aym_*``          Anayasa Mahkemesi                              (KBB API, live)
``uyusmazlik_*``   Uyuşmazlık Mahkemesi                           (live)
``mevzuat_*``      kanun … tebliğ, madde ağacı, gerekçe            (Bedesten, live)
``resmi_gazete_*`` günlük fihrist + belge                          (live)
``kurum_karari_*`` 8 düzenleyici kurum, tek arayüz                 (live + local)
``semantik_ara``   yerel indeks: hibrit (BM25 + trigram + vektör)  (local)
``status``         hangi kaynak ayakta, indeks ne kadar dolu
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mcpcore import McpError, Tool, run  # noqa: E402
import retrieval  # noqa: E402
import sources  # noqa: E402
from sources import bedesten_ictihat, bedesten_mevzuat, anayasa, uyusmazlik, resmi_gazete, spk  # noqa: E402
from textx import HAS_PYPDF  # noqa: E402

__version__ = "0.5.1"
INDEX_PATH = os.environ.get("INDEX_PATH") or os.path.join(HERE, "data", "index.db")

_index: Optional[retrieval.Index] = None


def index() -> retrieval.Index:
    global _index
    if _index is None:
        os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
        _index = retrieval.Index(INDEX_PATH)
    return _index


def _wrap(fn):
    """Turn adapter exceptions into readable tool errors instead of crashes."""
    def handler(args: Dict[str, Any]) -> Any:
        try:
            return fn(args or {})
        except McpError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise McpError("%s: %s" % (type(exc).__name__, exc)) from exc
    return handler


# --------------------------------------------------------------------------- #
# Kurum (regulator) tools — one interface over every regulator adapter          #
# --------------------------------------------------------------------------- #
KURUM = sources.kurum_sources()
KURUM_KEYS = [s.key for s in KURUM]


def _kurum(args: Dict[str, Any]) -> sources.Source:
    key = (args.get("kurum") or "").strip().lower()
    src = sources.get(key)
    if not src or src.kind not in ("kurum", "kurallar"):
        raise McpError("kurum şu değerlerden biri olmalı: %s" % ", ".join(KURUM_KEYS))
    return src


def t_kurum_ara(args: Dict[str, Any]) -> Any:
    src = _kurum(args)
    if not src.search:
        raise McpError("%s için arama yok." % src.key)
    merged = dict(args.get("params") or {})
    for k, v in args.items():
        if k not in ("kurum", "params") and v not in (None, ""):
            merged[k] = v
    out = src.search(merged)
    if isinstance(out, dict):
        out.setdefault("kurum", src.key)
        out.setdefault("kaynak", src.label)
        if not src.live:
            out.setdefault("unavailable", True)
    return out


def t_kurum_getir(args: Dict[str, Any]) -> Any:
    src = _kurum(args)
    if not src.get:
        raise McpError("%s için belge getirme yok (kaynak canlı değil)." % src.key)
    if src.key == "kvkk":
        args = _kvkk_adresi(args)
    out = src.get(args)
    if src.key == "epdk" and isinstance(out, dict) and len((out.get("text") or "").strip()) < 200:
        out = _epdk_arsivden(args, out)
    return out


def _epdk_arsivden(args: Dict[str, Any], canli: Dict[str, Any]) -> Dict[str, Any]:
    """Canlı belgeden metin çıkmadıysa (taranmış PDF, eski Word) metni yerel indeksten verir.

    Taranmış altı kararda canlı PDF yalnız kaşeyi ya da Resmî Gazete başlığını verir (200 karakterden
    kısa); indeksteki okunmuş metin daha uzunsa o gelir.

    Bu kararların metni tarama makinesinde okundu: .doc'lar Word dosyasından, taranmış PDF'ler
    OCR ile. OCR metni rakam ve özel isimde okuma hatası taşıyabilir; cevap bunu söyler.
    """
    from sources import epdk
    kimlik = str(args.get("id") or "").strip()
    cid = kimlik.split("id=", 1)[1] if "id=" in kimlik else kimlik.rsplit(":", 1)[-1]
    if not cid:
        return canli
    try:
        doc = index().db.execute(
            "SELECT ref, body, citation, meta FROM docs WHERE subject = 'epdk' "
            "AND (url = ? OR substr(ref, -?) = ?) LIMIT 1",
            ("%s/Detay/DownloadDocument?id=%s" % (epdk.BASE, cid), len(cid) + 1, ":" + cid)).fetchone()
    except Exception:  # noqa: BLE001 - indeks yoksa canlı hata kalır
        doc = None
    meta = json.loads(doc["meta"] or "{}") if doc else {}
    if not doc or not meta.get("metin") or len(doc["body"] or "") <= len(canli.get("text") or ""):
        return canli
    from textx import paginate
    out = paginate(doc["body"], args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update({"source_url": canli.get("source_url"), "citation": doc["citation"], "ref": doc["ref"],
                "metin_kaynagi": "yerel indeks (%s)" % meta["metin"]})
    if meta["metin"] == "ocr":
        out["uyari"] = ("Kaynak belge taranmış; metin OCR ile okundu, rakam ve özel isimlerde okuma hatası "
                        "olabilir. Alıntıdan önce belgeyle karşılaştırın: %s" % canli.get("source_url"))
    return out


def _kvkk_adresi(args: Dict[str, Any]) -> Dict[str, Any]:
    """Yalnız numarasıyla (8887) istenen KVKK kararına indeksteki adresi verir.

    Sayfa adresi karar numarasını da taşımak zorunda (/Icerik/8887/2026-1183);
    indekste yoksa kaynak kendi liste sayfalarına bakar.
    """
    kimlik = str(args.get("id") or "").strip()
    if not kimlik.isdigit():
        return args
    try:
        doc = index().get("kvkk:%s" % kimlik)
    except Exception:  # noqa: BLE001 - indeks yoksa kaynak kendi listesine bakar
        doc = None
    return dict(args, id=doc["url"]) if doc and doc.get("url") else args


def t_kurum_listesi(args: Dict[str, Any]) -> Any:
    out = []
    for s in KURUM:
        out.append({**s.describe(), "notes": s.notes,
                    "search_params": list((s.search_schema or {}).get("properties", {}).keys()),
                    "get_params": list((s.get_schema or {}).get("properties", {}).keys())})
    return {"total": len(out), "kurumlar": out,
            "note": "kurum_karari_ara(kurum=<key>, query=…, params={…}) — params kuruma özel filtreleri taşır."}


# --------------------------------------------------------------------------- #
# Local index / semantic                                                       #
# --------------------------------------------------------------------------- #
def t_semantik_ara(args: Dict[str, Any]) -> Any:
    q = (args.get("query") or "").strip()
    if not q:
        raise McpError("query gerekli.")
    idx = index()
    if idx.count() == 0:
        return {"query": q, "total": 0, "results": [],
                "error": "Yerel indeks boş. `python crawl.py --source kvkk,bddk,epdk,... --embed` ile doldurun. "
                         "Canlı kaynaklar (ictihat_ara, mevzuat_ara, kurum_karari_ara) bundan etkilenmez."}
    filters: Dict[str, Any] = {}
    kurum = (args.get("kurum") or "").strip().lower()
    if kurum:
        filters["subject"] = kurum
    if args.get("date_from"):
        filters["date_from"] = args["date_from"]
    if args.get("date_to"):
        filters["date_to"] = args["date_to"]
    out = idx.search(q, mode=args.get("mode") or "hybrid", limit=max(1, min(int(args.get("limit") or 10), 50)),
                     filters=filters, snippet_chars=int(args.get("snippet_chars") or 400))
    out["note"] = ("Yerel indeks yalnız taranmış (crawl) kaynakları kapsar; kapsam için status. "
                   "Tam belge: belge_getir(ref). Sonuçtaki citation alanı verbatim alıntılanır.")
    return out


def t_belge_getir(args: Dict[str, Any]) -> Any:
    ref = (args.get("ref") or "").strip()
    if not ref:
        raise McpError("ref gerekli (semantik_ara sonucundaki ref).")
    doc = index().get(ref)
    if not doc:
        raise McpError("İndekste yok: %s" % ref)
    from textx import paginate
    out = paginate(doc.pop("body", ""), args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update(doc)
    return out


def t_ictihat_semantik(args: Dict[str, Any]) -> Any:
    """Keyword-recall from Bedesten, then meaning-rank the fetched texts.

    Bedesten allows ~1 request / 3.5 s, and result rows carry no text, so each
    candidate costs a fetch. ``max_docs`` therefore defaults to 5 (≈20 s) and
    caps at 10. This is a precision tool for a shortlist, not a discovery tool.
    """
    q = (args.get("query") or "").strip()
    kw = (args.get("initial_keyword") or "").strip()
    if not q or not kw:
        raise McpError("query (doğal dil) ve initial_keyword (Bedesten anahtar kelimesi) gerekli.")
    max_docs = max(1, min(int(args.get("max_docs") or 5), 10))
    res = bedesten_ictihat.search({"query": kw, "courts": args.get("courts"), "chamber": args.get("chamber"),
                                   "date_from": args.get("date_from"), "date_to": args.get("date_to"),
                                   "page_size": 10})
    if res.get("error"):
        return res
    docs = []
    for row in (res.get("results") or [])[:max_docs]:
        g = bedesten_ictihat.get({"document_id": row["document_id"], "page_chars": 12000})
        docs.append({**row, "title": row.get("citation", ""), "body": g.get("text") or ""})
    ranked = retrieval.semantic_rerank(q, docs, fields=("title", "body"), limit=max_docs)
    results = ranked.get("results") if isinstance(ranked, dict) else ranked
    for r in results or []:
        body = r.pop("body", "")
        r["snippet"] = body[:500]
    method = ranked.get("method") if isinstance(ranked, dict) else "none"
    return {"query": q, "initial_keyword": kw, "candidates_total": res.get("total"),
            "fetched": len(docs), "results": results,
            "retrieval": {"method": method,
                          "semantic": "on" if method == "semantic" else "off",
                          **{k: v for k, v in (ranked.items() if isinstance(ranked, dict) else []) if k not in ("results", "method")}},
            "note": "Adaylar Bedesten anahtar kelime aramasının ilk sayfasından; anlamsal sıralama yalnız çekilen metinler üzerinde."}


# --------------------------------------------------------------------------- #
# Status and guide                                                             #
# --------------------------------------------------------------------------- #
def _triyaj_durum() -> Dict[str, Any]:
    """resmi_gazete_tara(konu=…) süzgecinin durumu ve ÖLÇÜLMÜŞ sınırı.

    status'ta yer alması gerekiyor: model dosyası eksikse konu süzgeci hata
    verir ve kullanıcı bunu ancak çağırınca öğrenir. Ayrıca duyarlılık
    rakamları burada durmalı — bir ön elemenin ne kadar kaçırdığı, onu
    kullanmaya karar vermeden önce bilinmesi gereken şeydir.
    """
    try:
        import triyaj
    except Exception as exc:  # noqa: BLE001
        return {"var": False, "neden": "triyaj modülü yüklenemedi: %s" % exc}
    return triyaj.kunye()


def t_status(args: Dict[str, Any]) -> Any:
    idx = index()
    by_kurum = {}
    try:
        for row in idx.db.execute("SELECT subject, COUNT(*) AS n, MIN(date) AS d0, MAX(date) AS d1 FROM docs GROUP BY subject"):
            by_kurum[row["subject"] or "?"] = {"docs": row["n"], "from": row["d0"], "to": row["d1"]}
    except Exception:  # noqa: BLE001
        pass
    out = {
        "server": "arthur-tr-hukuk-mcp", "version": __version__, "pypdf": HAS_PYPDF,
        "sources": [s.describe() for s in sources.load_all().values()],
        "sources_failed": sources.failed(),
        "local_index": {"path": INDEX_PATH, "documents": idx.count(), "vectorised": idx.vector_count(),
                        "by_kurum": by_kurum, "last_crawl": idx.get_state("last_crawl", "")},
        "semantic": retrieval.embeddings_status(),
        "triyaj": _triyaj_durum(),
        "rate_limits": {"bedesten": "10 istek / 30 sn (yerel kova: %s sn/istek)" % os.environ.get("BEDESTEN_RATE_REFILL_S", "3.5")},
        "note": "Yüklenememiş kaynak = ERİŞİLEMEZ, boş değil. status'u sonuçlar ince göründüğünde çağırın.",
    }
    return out


GUIDE = """arthur-tr-hukuk-mcp — hangi soru için hangi araç

1. MEVZUAT (kanun, KHK, CBK, yönetmelik, tebliğ)
   mevzuat_ara(query="Elektrik Piyasası", types=["KANUN"]) → mevzuat_id
   Madde okuma (tek çağrı): tr_mevzuat_madde_getir(number="6769", madde_no=["115","120","geçici 1"])  → başlık + metin + citation
   Madde ağacı: tr_mevzuat_icindekiler(number="6769", compact=true, heading_query="önalım")
   Konu araması (madde numarası araması DEĞİL): tr_mevzuat_icinde_ara(number="6769", query="önalım")
   Gerekçe: mevzuat_gerekce(gerekce_id)   (yalnız gerekce_id dolu kanunlarda)
   Sektörel düzenleme: types=["KKY","TEBLIGLER"] + query="Enerji Piyasası" / "Sermaye Piyasası" / "Bankacılık"
   Konu taraması: mevzuat_ara(types=["KANUN"], rg_date_from="2024-09-01", konu="icra", max_pages=3)
     Sorgu kelimesi gerekmez. Torba kanunlarda ("Bazı Kanunlarda Değişiklik…") değiştirilen kanun
     ADLARINA bakar: 7531 sayılı Kanun İİK'yı değiştirir ama başlığı bunu söylemez. Adları okunamayan
     torba kanun ELENMEZ, konu_kaynak="belirsiz" ile döner.
     SINIR: eğitimde görülmemiş 60 başlıkta vergi 4/4, icra 2/2, enerji 0/3. Enerji taramasında
     konu'ya güvenmeyin; query ile birlikte kullanın. Sayfa başı en çok 20 kayıt (Bedesten sınırı).

2. İÇTİHAT
   Yargıtay/Danıştay/BAM/yerel/KYB: ictihat_ara(query, courts, chamber, date_from) → ictihat_getir(document_id)
   AYM: aym_ara(query, kind="norm"|"bireysel") → aym_getir
   Görev uyuşmazlığı: uyusmazlik_ara
   Kavramsal soru + kısa liste: ictihat_semantik_ara(query, initial_keyword)

3. DÜZENLEYİCİ KURUM KARARLARI — kurum_karari_ara(kurum=…)
   rekabet · epdk · spk · bddk · kvkk · btk · gib · sigorta_tahkim
   (KİK, Sayıştay, TÜRKPATENT, İSTAÇ bu sunucuda YOK: resmi uçları güvenilir cevap vermiyor; bkz. status)
   Kuruma özel filtreler: kurum_listesi. Metin: kurum_karari_getir(kurum, id)
   SPK bülteni içinde: spk_bulten_icinde_ara(bulten="2026/27", query)
   Yayım teyidi ve RG künyesi: resmi_gazete_fihrist(date) / resmi_gazete_tara(query, date_from, date_to)
   Konu taraması: resmi_gazete_tara(konu="enerji"|"rekabet"|"vergi"|"icra", date_from, date_to)
     Yerel, ağsız, ücretsiz bir ön eleme. Başlıkta konu adı geçmese de yakalar —
     "Konkordato Gider Avansı Tarifesi" icradır ama 'icra' yazmaz. Etiketli gövdede
     konu adı, icra kalemlerinin yalnız %10'unda geçiyor; bu yüzden query yetmez.
     SINIR: ön elemedir, duyarlılık %92-100. YAYIM TEYİDİNDE KULLANMAYIN — orada
     eksiksizlik gerekir; query kullanın ya da esik=0 ile tam listeyi alın.

4. SEMANTİK / ARŞİV
   semantik_ara(query, kurum=?) — yerel indeks (crawl edilmiş kurum kararları, SPK bültenleri, Sigorta Tahkim dergileri)
   status → hangi kurum kaç belge, vektör var mı. Boş indeks ≠ karar yok.

ALINTI DİSİPLİNİ
   Her sonuçtaki `citation` alanını birebir kullanın; esas/karar numarası, tarih, RG künyesi ezberden yazılmaz.
   source_url olmayan belgeler (madde metni) için URL uydurmayın; belge adı + madde numarası ile atıf yapın.
   Özelge bağlayıcı değildir. Rekabet/EPDK/BDDK kararlarının RG'de yayımlandığını fihristle teyit edin.
   Madde atfı: yazılan her kanun maddesi (sözleşme ve dilekçe gövdesi dâhil) tr_mevzuat_madde_getir ile çekilir;
   başlık ve metin, maddeye yüklenen içerikle karşılaştırılır. Çekilemeyen madde ezberden yazılmaz; yerine
   "UYARI: veri çekilemedi, teyidiniz gerekli: https://www.mevzuat.gov.tr/" yazılır.
   Bedesten hız sınırı: 10 istek / 30 sn; aynı turda 5'ten fazla çağrı göndermeyin, bu bir tempo kuralıdır, doğrulanacak madde sayısına sınır değildir; 429 alınca birkaç saniye bekleyin.
"""


def t_rehber(args: Dict[str, Any]) -> Any:
    return {"guide": GUIDE}


# --------------------------------------------------------------------------- #
# Tool table                                                                   #
# --------------------------------------------------------------------------- #
def build_tools() -> List[Tool]:
    ic, mv, ay, uy, rg = bedesten_ictihat, bedesten_mevzuat, anayasa, uyusmazlik, resmi_gazete
    common_kurum_props = {
        "kurum": {"type": "string", "enum": KURUM_KEYS, "description": "Kurum anahtarı (kurum_listesi)"},
        "query": {"type": "string", "description": "Arama ifadesi (kuruma göre başlık / metin / konu)"},
        "page": {"type": "integer", "minimum": 1},
        "page_size": {"type": "integer"},
        "limit": {"type": "integer"},
        "date_from": {"type": "string", "description": "YYYY-MM-DD (kurum destekliyorsa)"},
        "date_to": {"type": "string"},
        "year": {"type": "integer", "description": "spk: bülten yılı; epdk/bddk: karar yılı"},
        "decision_type": {"type": "string", "description": "rekabet: birlesme_devralma|rekabet_ihlali|muafiyet_menfi_tespit|ozellestirme|diger"},
        "decision_no": {"type": "string"},
        "market": {"type": "string", "description": "epdk: elektrik|dogalgaz|petrol|lpg"},
        "issue": {"type": "integer", "description": "sigorta_tahkim: dergi sayısı"},
        "number": {"type": "integer", "description": "spk: bülten sayısı"},
        "params": {"type": "object", "additionalProperties": True,
                   "description": "Kuruma özel diğer filtreler (kurum_listesi.search_params)"},
    }
    tools = [
        Tool("ictihat_ara", "Yargıtay, Danıştay, BAM (istinaf), yerel hukuk mahkemesi ve KYB kararlarını Bedesten'de arar. "
             "Liste metin içermez; ictihat_getir ile okuyun. Hız sınırı 10 istek/30 sn.", ic.SEARCH_SCHEMA, _wrap(ic.search)),
        Tool("ictihat_getir", "Bedesten karar metnini getirir (sayfalı). document_id ictihat_ara'dan.", ic.GET_SCHEMA, _wrap(ic.get)),
        Tool("ictihat_semantik_ara", "Anahtar kelimeyle bulunan ilk kararları (≤10) çekip doğal dil sorusuna göre anlamsal sıralar. "
             "Yavaştır (~4 sn/karar); kısa liste doğrulama aracıdır.",
             {"type": "object", "properties": {
                 "query": {"type": "string", "description": "Doğal dil soru"},
                 "initial_keyword": {"type": "string", "description": "Bedesten'e gidecek anahtar kelime(ler)"},
                 "courts": {"type": "array", "items": {"type": "string", "enum": list(ic.COURT_TYPES)}},
                 "chamber": {"type": "string"}, "date_from": {"type": "string"}, "date_to": {"type": "string"},
                 "max_docs": {"type": "integer", "default": 5, "maximum": 10}},
              "required": ["query", "initial_keyword"]}, _wrap(t_ictihat_semantik)),
        Tool("aym_ara", "Anayasa Mahkemesi norm denetimi (kind=norm) veya bireysel başvuru (kind=bireysel) kararlarını arar.",
             ay.SEARCH_SCHEMA, _wrap(ay.search)),
        Tool("aym_getir", "AYM karar tam metni (sayfalı).", ay.GET_SCHEMA, _wrap(ay.get)),
        Tool("uyusmazlik_ara", "Uyuşmazlık Mahkemesi kararlarını arar (adli–idari görev uyuşmazlıkları).", uy.SEARCH_SCHEMA, _wrap(uy.search)),
        Tool("uyusmazlik_getir", "Uyuşmazlık Mahkemesi karar PDF metni.", uy.GET_SCHEMA, _wrap(uy.get)),
        Tool("mevzuat_ara", "Mevzuat arar: kanun, KHK, CB kararnamesi/kararı/yönetmeliği/genelgesi, tüzük, kurum yönetmeliği, tebliğ, mülga. "
             "Varsayılan başlıkta; search_in='fulltext' ile metinde. Sorgu kelimesi olmadan da listeler "
             "(types ve/veya rg_date_from/rg_date_to). konu=… yerel bir ön elemedir: başlıkta konu adı "
             "geçmeyen mevzuatı ve torba kanunları (değiştirilen kanun adlarından) yakalar; sınırı şemada yazar.",
             mv.SEARCH_SCHEMA, _wrap(mv.search)),
        Tool("mevzuat_getir", "Mevzuat tam metni (sayfalı).", mv.GET_SCHEMA, _wrap(mv.get)),
        Tool("mevzuat_icindekiler", "Mevzuatın madde ağacı (madde başlıkları, madde_id'ler). number ile de çağrılır; "
             "compact=true, madde_from/madde_to ve heading_query çıktıyı küçültür. Tek madde için tr_mevzuat_madde_getir yeter.",
             mv.TOC_SCHEMA, _wrap(mv.toc)),
        Tool("mevzuat_madde_getir", "Madde metni + başlık + hazır atıf TEK ÇAĞRIDA: number=\"6769\", madde_no=\"120\" "
             "(ya da en çok 10 maddelik liste; \"geçici 1\", \"ek 3\", \"169/a\"). Bir maddeye atıf yapmadan ÖNCE bununla "
             "doğrulayın; olmayan madde NOT_FOUND döner, komşu madde değil. Eski madde_id kullanımı geçerli.",
             mv.MADDE_SCHEMA, _wrap(mv.article)),
        Tool("mevzuat_gerekce", "Kanun gerekçesi (genel gerekçe, komisyon raporları, madde gerekçeleri).",
             {"type": "object", "properties": {"gerekce_id": {"type": "string"}, "page": {"type": "integer", "default": 1},
                                               "page_chars": {"type": "integer", "default": 8000}}, "required": ["gerekce_id"]},
             _wrap(mv.gerekce)),
        Tool("mevzuat_icinde_ara", "Bir mevzuatın maddelerinde anahtar kelime arar (konu araması; madde numarasıyla madde "
             "bulmak için değil). Her sonuç kendi madde_no ve başlığını taşır; başlığı eşleşen madde önce gelir.",
             mv.WITHIN_SCHEMA, _wrap(mv.search_within)),
        Tool("resmi_gazete_fihrist", "Bir günün Resmî Gazete fihristi (bölüm + başlık + link). Kurul kararlarının yayım teyidi.",
             rg.SOURCE.search_schema, _wrap(rg.fihrist)),
        Tool("resmi_gazete_getir", "Resmî Gazete belge metni (fihrist url'sinden).", rg.SOURCE.get_schema, _wrap(rg.get)),
        Tool("resmi_gazete_tara", "Tarih aralığında (≤60 gün) fihrist tarar; gün başına bir istek. "
             "query harfi harfine arar; konu ise yerel bir ön eleme ile başlıkta konu adı geçmeyen "
             "kalemleri de yakalar ('Katı Yakıtların Kontrolü Yönetmeliği' -> enerji). En az biri gerekir.",
             {"type": "object", "properties": {
                 "query": {"type": "string", "description": "Harfi harfine terim araması"},
                 "konu": {"type": "string", "enum": ["enerji", "rekabet", "vergi", "icra"],
                          "description": "Konu ön elemesi (yerel, ağsız, ücretsiz). ÖN ELEMEDİR: "
                                         "ölçülen duyarlılık enerji %97, rekabet %100, vergi %93, "
                                         "icra %92 — yayım teyidi için kullanmayın."},
                 "esik": {"type": "number", "description": "Konu eşiği (varsayılan 0.20, ölçülmüştür); "
                                                           "0 süzgeci kapatır, skorlar yine döner"},
                 "date_from": {"type": "string"}, "date_to": {"type": "string"}}}, _wrap(rg.scan)),
        Tool("kurum_karari_ara", "Düzenleyici kurum kararlarında arama — tek arayüz: " + ", ".join(KURUM_KEYS) +
             ". Kuruma özel filtreler için kurum_listesi.", {"type": "object", "properties": common_kurum_props,
                                                             "required": ["kurum"]}, _wrap(t_kurum_ara)),
        Tool("kurum_karari_getir", "Kurum kararı / belgesi tam metni (sayfalı). id kurum_karari_ara sonucundan.",
             {"type": "object", "properties": {"kurum": {"type": "string", "enum": KURUM_KEYS}, "id": {"type": "string"},
                                               "page": {"type": "integer", "default": 1},
                                               "page_chars": {"type": "integer", "default": 8000}},
              "required": ["kurum", "id"]}, _wrap(t_kurum_getir)),
        Tool("kurum_listesi", "Desteklenen kurumlar, her birinin kapsamı, canlı/yerel durumu ve arama parametreleri.",
             {"type": "object", "properties": {}}, _wrap(t_kurum_listesi)),
        Tool("spk_bulten_icinde_ara", "Bir SPK haftalık bülteni içinde arar (bölüm bazında). bulten='2026/27'.",
             spk.WITHIN_SCHEMA, _wrap(spk.search_within)),
        Tool("semantik_ara", "Yerel indekste hibrit arama (BM25 + trigram + vektör). Kurum kararları, SPK bültenleri, "
             "Sigorta Tahkim dergileri — crawl edilmiş kadarıyla (status).",
             {"type": "object", "properties": {
                 "query": {"type": "string"},
                 "kurum": {"type": "string", "enum": KURUM_KEYS, "description": "Tek kuruma daralt"},
                 "mode": {"type": "string", "enum": ["hybrid", "lexical", "semantic", "fuzzy"], "default": "hybrid"},
                 "limit": {"type": "integer", "default": 10, "maximum": 50},
                 "date_from": {"type": "string"}, "date_to": {"type": "string"},
                 "snippet_chars": {"type": "integer", "default": 400}},
              "required": ["query"]}, _wrap(t_semantik_ara)),
        Tool("belge_getir", "Yerel indeksteki belgenin tam metni (ref semantik_ara sonucundan).",
             {"type": "object", "properties": {"ref": {"type": "string"}, "page": {"type": "integer", "default": 1},
                                               "page_chars": {"type": "integer", "default": 8000}}, "required": ["ref"]},
             _wrap(t_belge_getir)),
        Tool("hukuk_arastirma_rehberi", "Hangi soru için hangi araç: mevzuat → içtihat → kurum kararı → RG teyidi akışı ve alıntı kuralları.",
             {"type": "object", "properties": {}}, _wrap(t_rehber)),
        Tool("status", "Kaynak sağlığı, yerel indeks doluluğu (kurum bazında), semantik arama durumu, hız sınırları.",
             {"type": "object", "properties": {}}, _wrap(t_status)),
    ]
    return tools


INSTRUCTIONS = """MADDE ATFI KURALI: Bir kanun maddesini (not, sözleşme, protokol, dilekçe veya karar gövdesi dâhil) yazmadan önce tr_mevzuat_madde_getir(number="6769", madde_no="120") ile metnini ve başlığını çekin; yanıttaki citation alanını birebir kullanın ve maddeye yüklediğiniz içeriğin (hakkın sahibi, şart, süre, sonuç) başlık ve metinle örtüştüğünü kontrol edin. Çekilemeyen maddeyi ezberden yazmayın; yerine açıkça "UYARI: veri çekilemedi, teyidiniz gerekli: https://www.mevzuat.gov.tr/" yazın.

arthur-tr-hukuk-mcp — Türk hukuku araştırma sunucusu (içtihat + mevzuat + 8 düzenleyici kurum + semantik arama).

ARAÇ AİLELERİ. `ictihat_*` Yargıtay/Danıştay/BAM/yerel/KYB · `aym_*` Anayasa Mahkemesi · `uyusmazlik_*` ·
`mevzuat_*` kanun–tebliğ, madde ağacı, gerekçe · `resmi_gazete_*` · `kurum_karari_*` (rekabet, epdk, spk, bddk,
kvkk, btk, gib, sigorta_tahkim) · `semantik_ara` yerel indeks · `status`.
KİK, Sayıştay, TÜRKPATENT ve İSTAÇ bilinçli olarak YOK: resmi uçları çalışmıyor (EKAP 500, Sayıştay WAF 418,
reCAPTCHA, DNS). Bu kurumlar sorulursa bunu söyleyin; sonuç uydurmayın.

İLK ÇAĞRI. Karmaşık soruda önce `hukuk_arastirma_rehberi`; sonuçlar ince göründüğünde `status`. Yüklenememiş
kaynak ERİŞİLEMEZ demektir, "karar yok" demek değildir. `unavailable: true` veya `upstream_blocked: true`
taşıyan yanıtı boş sonuç gibi yorumlamayın; bilginin ardından "UYARI: veri çekilemedi, teyidiniz gerekli: <bağlantı>" yazın.

TEYİT BAĞLANTISI. Araç o belge için `source_url` döndürdüyse o adres; döndürmediyse kaynağın resmî giriş sayfası:
mevzuat https://www.mevzuat.gov.tr/ · Resmî Gazete https://www.resmigazete.gov.tr/ · Yargıtay
https://karararama.yargitay.gov.tr/ · Danıştay https://karararama.danistay.gov.tr/ · BAM ve yerel mahkeme
https://emsal.uyap.gov.tr/ · AYM https://kararlarbilgibankasi.anayasa.gov.tr/ (norm denetimi
https://normkararlarbilgibankasi.anayasa.gov.tr/) · Uyuşmazlık https://www.uyusmazlik.gov.tr/ · Rekabet
https://www.rekabet.gov.tr/ · EPDK https://www.epdk.gov.tr/ · SPK https://www.spk.gov.tr/ · BDDK
https://www.bddk.org.tr/ · KVKK https://www.kvkk.gov.tr/ · BTK https://www.btk.gov.tr/ · GİB https://www.gib.gov.tr/ ·
Sigorta Tahkim https://www.sigortatahkim.org.tr/. Derin bağlantı tahminle kurulmaz; "[doğrulayın]" türü etiket kullanılmaz.

ALINTI. Her sonuçtaki `citation` alanı birebir kullanılır; esas/karar numarası, tarih ve RG künyesi ezberden
yazılmaz. `source_url` olmayan belgeler için URL uydurulmaz. Özelge bağlayıcı değildir. Karar listeleri metin
içermez: yorum yapmadan önce `*_getir` ile metni okuyun.

HIZ. Bedesten (içtihat + mevzuat) 10 istek / 30 sn; aynı turda 5'ten fazla çağrı göndermeyin, bu bir tempo kuralıdır, doğrulanacak madde sayısına sınır değildir; `retry: true`
gelirse birkaç saniye bekleyip yineleyin. ictihat_semantik_ara karar başına ~4 sn sürer.

SEMANTİK. `semantik_ara` yalnız yerel indekse (crawl edilmiş kurum kararları, SPK bültenleri, Sigorta Tahkim
dergileri) bakar ve `retrieval.semantic` alanıyla vektör kanalının açık olup olmadığını söyler;
"off" ise sonuçlar anahtar kelime eşleşmesidir.
"""


# Module-level tool table so an aggregator (arthurlegal-mcp) can import this
# file, adopt every tool under a `tr_` prefix and fold `status` into its own.
TOOLS = build_tools()
_t_status = t_status   # the aggregator looks for this name when composing its status


if __name__ == "__main__":
    tools = TOOLS
    sys.stderr.write("arthur-tr-hukuk-mcp %s: %d araç, %d kaynak (%d yüklenemedi), indeks %s\n" % (
        __version__, len(tools), len(sources.load_all()), len(sources.failed()), INDEX_PATH))
    run(tools, name="arthur-tr-hukuk-mcp", version=__version__, instructions=INSTRUCTIONS)
