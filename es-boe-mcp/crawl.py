"""Build the local search index for es-boe-mcp.

    python crawl.py                    # full metadata crawl (~20 requests)
    python crawl.py --embed            # ...then compute vectors, if EMBEDDINGS_URL is set
    python crawl.py --backfill-text    # add the head of each act's text (one request per act)
    python crawl.py --repair-status    # offline: re-derive status from the stored text head

What gets indexed, and why only this
------------------------------------
Title, official number, issuing department, rango, dates and ámbito — **not**
the consolidated body text, and **not** ``materias``. BOE exposes its subject
tags only on the per-act detail endpoint, so indexing them would cost one
request per act (12,376 of them) rather than the ~20 this crawl takes. The
subject signal therefore comes from the title, which for Spanish legislation is
descriptive by convention ("Ley 17/2006, de control ambiental integrado").

That is a deliberate trade, not an oversight. A single consolidated act runs to
roughly a million characters (the Ley de Sociedades de Capital is 960,963), so
indexing bodies for the whole corpus would mean tens of gigabytes for a server
whose job is to *find the act*. Spanish legislation is looked up by name,
number and subject; once the act is identified, ``get_act_text`` fetches the
authoritative text on demand.

The consequence is stated in every search response so it cannot be mistaken:
this searches metadata, so a phrase buried in article 348 bis will not be found
by searching for it.

The head of the text is the exception (``--backfill-text``, 2026-10-10): the
first 8,000 characters of the text in force, which are the preamble and the
opening articles, the part that says in plain words what the act regulates. With
titles alone, a question asked in plain words found its act in the top 10 three
times in four (an independent 120-question set, 2026-10-10). The head costs one
request per act, a second apart, and adds ~100 MB of text instead of tens of
gigabytes. It sits after ``TEXT_MARK`` in the body, and a metadata re-crawl keeps it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time

import boe
from boe import BoeClient, BoeError
from retrieval import Index, embeddings_available

PAGE = 1000  # verified working ceiling for the list endpoint
# Where the metadata lines end and the head of the consolidated text begins.
TEXT_MARK = "\n\n[texto consolidado, inicio]\n"
HEAD_CHARS = 8000

# --------------------------------------------------------------------------- #
# Status: BOE's list flag, corrected by the act's own text                     #
# --------------------------------------------------------------------------- #
# The list endpoint flags an act only as 'vigencia agotada' or not. 158 acts it
# left unflagged (index of 2026-10-10) open their consolidated text with BOE's
# own note "Norma derogada, ..." (LO 15/1999 among them). Only an UNQUALIFIED repeal or annulment, already in
# effect, overrides the flag: "Norma derogada[, con efectos de <fecha>,] por ...".
# Anything that narrows it -- "salvo", "excepto", "en cuanto se oponga", "en el
# ámbito de", "sin perjuicio de", a future date, a condition -- means part of the
# act may still be in force, so it stays 'vigente' and the text says the rest.
DEROGADA, ANULADA = "derogada", "anulada"
_MESES = {m: i for i, m in enumerate(
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split(), 1)}
_MESES["setiembre"] = 9
_NOTA_TOTAL = re.compile(
    r"norma\s+(derogada|anulada)\s*[.,]?\s*"
    r"(?:con\s+efectos?\s+(?:de|desde(?:\s+el)?)\s+(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})\s*,?\s*)?"
    r"(?=por\b|seg[uú]n\s+establece\b)", re.I)
_SALVEDAD = re.compile(
    r"salvo|except|excepci|en\s+cuanto|en\s+lo\s+que|en\s+todo\s+lo|referente|relativo|parcial|"
    r"sin\s+perjuicio|a\s+partir\s+de|alcance|no\s+obstante|seguir|continuar|permanecer|mantien|"
    r"mantendr|transitori|vigente|en\s+vigor|[uú]nicamente|solamente|s[oó]lo\b|[aá]mbito|"
    r"para\s+(?:las?|los?)\b", re.I)


def act_status(status: str, head: str, today: str = "") -> str:
    """The status to index: 'vigente' becomes 'derogada' / 'anulada' when the head of
    the consolidated text opens with an unqualified repeal or annulment in effect by
    ``today`` (ISO date, default now). Other labels ('vigencia agotada') are BOE's own
    and are kept. Idempotent: 'derogada' / 'anulada' are re-derived from the head."""
    if status not in ("vigente", DEROGADA, ANULADA):
        return status
    first = (head or "").strip().split("\n", 1)[0]
    m = _NOTA_TOTAL.match(first)
    if not m or _SALVEDAD.search(first[m.end():]):
        return "vigente"
    if m.group(2):
        mes = _MESES.get(m.group(3).lower())
        if not mes:
            return "vigente"
        if "%s-%02d-%02d" % (m.group(4), mes, int(m.group(2))) > (today or time.strftime("%Y-%m-%d")):
            return "vigente"            # repealed with effect from a later date: in force until then
    return m.group(1).lower()


def _head(body: str) -> str:
    return (body or "").split(TEXT_MARK, 1)[1] if TEXT_MARK in (body or "") else ""


_COLS = ("title", "body", "url", "lang", "date", "status", "court", "subject", "citation")


def _unchanged(old, doc) -> bool:
    """Would ``upsert`` rewrite the row with what it already holds? Then it is skipped:
    an upsert drops the vector, and a re-crawl of 12,376 acts would re-embed them all."""
    return (all(str(doc.get(c) or "") == (old[c] or "") for c in _COLS)
            and json.loads(old["meta"] or "{}") == json.loads(json.dumps(doc.get("meta") or {})))


def crawl(index: Index, max_items: int = 0, pause: float = 0.3) -> int:
    client = BoeClient()
    offset, seen, unchanged = 0, 0, 0
    while True:
        try:
            batch = client.list_consolidated(limit=PAGE, offset=offset)
        except BoeError as exc:
            sys.stderr.write("stopped at offset %d: %s\n" % (offset, exc))
            break
        if not batch:
            break
        for item in batch:
            old = index.db.execute("SELECT %s, meta FROM docs WHERE ref = ?" % ", ".join(_COLS),
                                   (item["id"],)).fetchone()
            head, text_chars = "", None
            if old is not None and TEXT_MARK in (old["body"] or ""):
                head = TEXT_MARK + old["body"].split(TEXT_MARK, 1)[1]
                text_chars = json.loads(old["meta"] or "{}").get("text_chars")
            doc = {
                "ref": item["id"],
                "title": item["title"],
                # The searchable surface: everything that identifies the act.
                "body": "\n".join(
                    x for x in (
                        item.get("numero_oficial"),
                        item.get("rango"),
                        item.get("departamento"),
                        item.get("ambito"),
                    ) if x
                ) + head,
                "url": item["url"],
                "lang": "es",
                "date": item.get("date") or item.get("published") or "",
                "court": item.get("departamento") or "",
                "subject": item.get("ambito") or "",
                # BOE's own repeal signal, corrected by the head of the text
                # (act_status), indexed so search can filter on it.
                "status": act_status(item.get("status") or "", _head(head)),
                "citation": "%s (%s)" % (item["title"], item["id"]),
                "meta": {
                    "rango": item.get("rango"),
                    "numero_oficial": item.get("numero_oficial"),
                    "published": item.get("published"),
                    "fecha_vigencia": item.get("fecha_vigencia"),
                    "estado_consolidacion": item.get("estado_consolidacion"),
                    "eli": item.get("eli"),
                    **({"text_chars": text_chars} if text_chars is not None else {}),
                },
            }
            seen += 1
            if old is not None and _unchanged(old, doc):
                unchanged += 1
                continue
            index.upsert(doc)
        index.db.commit()
        sys.stderr.write("indexed %d (unchanged %d)\r" % (seen, unchanged))
        sys.stderr.flush()
        if max_items and seen >= max_items:
            break
        if len(batch) < PAGE:
            break
        offset += PAGE
        time.sleep(pause)  # be a polite client of a public service
    index.reindex_fts()
    index.set_state("last_crawl", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    index.set_state("corpus", "BOE legislación consolidada (metadata)")
    sys.stderr.write("\nindexed %d documents (%d unchanged, vectors kept)\n" % (seen, unchanged))
    return seen


def text_head(client: BoeClient, boe_id: str, max_chars: int = HEAD_CHARS) -> str:
    """The first ``max_chars`` of the text in force today, in one request.

    ``BoeClient.get_text`` asks for the metadata first; a crawl over every act
    does not need it, so this reads ``/texto`` alone with the same version rule.
    """
    texto = client._part(boe_id, "texto").find("texto")
    if texto is None:
        return ""
    day = boe._compact_date("")
    blocks, size = [], 0
    for bloque in texto.findall("bloque"):
        version, _ = boe._version_on(bloque, day)
        if version is None:
            continue
        lines = boe._lines(version)
        if lines:
            blocks.append("\n".join(lines))
            size += len(blocks[-1]) + 2
            if size >= max_chars:
                break
    return "\n\n".join(blocks)[:max_chars]


def backfill_text(index: Index, limit: int = 0, pace: float = 1.0,
                  max_chars: int = HEAD_CHARS, log=None) -> dict:
    """Append the head of the consolidated text to every act that lacks one.

    Restartable: an act is done once its meta carries ``text_chars`` (0 when
    BOE has no text for it). A changed body drops its vector; embed afterwards.
    """
    log = log or (lambda m: sys.stderr.write(m + "\n"))
    client = BoeClient()
    rows = [r for r in index.db.execute("SELECT id, ref, body, status, meta FROM docs ORDER BY id").fetchall()
            if "text_chars" not in json.loads(r["meta"] or "{}")]
    if limit:
        rows = rows[:limit]
    filled = empty = failed = 0
    t0 = time.time()
    for i, r in enumerate(rows, 1):
        if i > 1:
            time.sleep(pace)
        meta = json.loads(r["meta"] or "{}")
        try:
            head = text_head(client, r["ref"], max_chars)
        except BoeError as exc:
            failed += 1
            log("%s: %s" % (r["ref"], exc))
            continue                      # no text_chars: the next run tries again
        meta["text_chars"] = len(head)
        if head:
            body = (r["body"] or "").split(TEXT_MARK, 1)[0] + TEXT_MARK + head
            index.db.execute("UPDATE docs SET body = ?, status = ?, meta = ? WHERE id = ?",
                             (body, act_status(r["status"] or "", head),
                              json.dumps(meta, ensure_ascii=False), r["id"]))
            index.db.execute("DELETE FROM vecs WHERE doc_id = ?", (r["id"],))
            filled += 1
        else:
            index.db.execute("UPDATE docs SET meta = ? WHERE id = ?",
                             (json.dumps(meta, ensure_ascii=False), r["id"]))
            empty += 1
        if i % 50 == 0:
            index.db.commit()
            log("%d/%d (text %d, none %d, failed %d, %.0fs)" % (i, len(rows), filled, empty, failed,
                                                                 time.time() - t0))
    index.db.commit()
    index.reindex_fts()
    index.set_state("corpus", "BOE legislación consolidada (metadata + first %d characters of the "
                              "text in force)" % max_chars)
    return {"candidates": len(rows), "text": filled, "no_text": empty, "failed": failed}


def repair_status(index: Index, today: str = "") -> dict:
    """Offline: re-derive every act's status from the head already stored (act_status).

    Writes the status column only -- no upsert, so bodies and vectors are kept --
    and returns the counts of each change, e.g. {"vigente -> derogada": 23}.
    """
    changes: dict = {}
    for r in index.db.execute("SELECT id, body, status FROM docs").fetchall():
        new = act_status(r["status"] or "", _head(r["body"]), today)
        if new != (r["status"] or ""):
            index.db.execute("UPDATE docs SET status = ? WHERE id = ?", (new, r["id"]))
            key = "%s -> %s" % (r["status"], new)
            changes[key] = changes.get(key, 0) + 1
    index.db.commit()
    return changes


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the es-boe-mcp index")
    ap.add_argument("--max", type=int, default=0, help="stop after N acts (0 = all)")
    ap.add_argument("--embed", action="store_true", help="compute vectors after crawling")
    ap.add_argument("--index", default=None, help="index path (default $INDEX_PATH or index.db)")
    ap.add_argument("--backfill-text", action="store_true",
                    help="no list crawl: add the head of each act's consolidated text")
    ap.add_argument("--limit", type=int, default=0, help="--backfill-text: at most N acts (0 = all)")
    ap.add_argument("--pace", type=float, default=1.0,
                    help="--backfill-text: seconds between requests to BOE (default 1)")
    ap.add_argument("--repair-status", action="store_true",
                    help="no requests: re-derive status from the stored text head (status column only, "
                         "vectors kept)")
    args = ap.parse_args()

    index = Index(args.index)
    if args.repair_status:
        sys.stderr.write("status changes: %s\n" % (repair_status(index) or "none"))
    elif args.backfill_text:
        sys.stderr.write("%s\n" % backfill_text(index, limit=args.limit, pace=args.pace))
    else:
        crawl(index, max_items=args.max)
    if args.embed:
        if not embeddings_available():
            sys.stderr.write("EMBEDDINGS_URL not set — skipping vectors.\n")
        else:
            sys.stderr.write("%s\n" % index.embed_missing())


if __name__ == "__main__":
    main()
