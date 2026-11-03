"""Hybrid retrieval over a local SQLite index — standard library only.

Vendored into each ArthurLegal jurisdiction MCP server. Exists because several
official legal APIs return documents but cannot *search* them:

- NL Rechtspraak: 3.75M decisions, **no free-text parameter at all**
- IE Irish Statute Book / LU Legilux: perfect document URLs, **no search endpoint**
- PL Sejm: searches **titles only**, never the body

Three retrieval channels, fused with Reciprocal Rank Fusion:

1. ``lexical``   FTS5 + BM25, ``unicode61 remove_diacritics 2`` — exact legal
                 terms, article numbers, party names. Diacritic-insensitive, so a
                 Turkish-keyboard query still matches ``Kündigung``.
2. ``fuzzy``     FTS5 ``trigram`` — substring and misspelling tolerance
                 (``nergiew`` finds ``energiewet``). Only consulted when the
                 lexical channel found nothing, or found little and no
                 semantic channel stands in for it.
3. ``semantic``  Dense vectors, **only when an embeddings backend is configured**
                 (see below). Finds conceptually related text that shares no
                 keywords — the actual point of asking a question in Turkish
                 about a Dutch judgment.

How ``hybrid`` fuses them
-------------------------
The lexical channel is a ladder: every word, then every word as a prefix, then
any word. A question asked in plain words falls to the last rung, whose matches
share a word or two with the question and little else; ranked next to the
semantic channel they push the meaning matches down. So the lexical channel is
ranked only when every word matched; after an any-word match it only fills a
list the semantic channel left short. A query that is a document's ``ref`` (an
ECLI, a BOE id) puts that document first. Replayed on the channel lists of a
120-query known-item measurement over the six local indexes (2026-10-09):
questions in plain words found their document first 82% of the time instead of
19%, and keyword and number queries stayed at 100%.

The semantic scan reads the vectors in chunks and scores each chunk with numpy
when it is installed (16,545 vectors: 1.3 s -> 0.2 s on one machine); without
numpy the standard library gives the same ranking, more slowly.

Honest limitation
-----------------
The standard library cannot run a transformer. ``semantic`` therefore requires
an OpenAI-compatible embeddings endpoint supplied through the environment:

    EMBEDDINGS_URL=https://api.example.com/v1/embeddings
    EMBEDDINGS_MODEL=text-embedding-3-small     # optional
    EMBEDDINGS_API_KEY=...                      # optional
    EMBEDDINGS_DIM=1536                         # optional, for sanity checks

With no backend configured, ``mode="hybrid"`` silently degrades to
lexical+fuzzy and **says so** in the response's ``retrieval`` block. It never
pretends a lexical hit was a semantic one.

For cross-language work (Turkish question, Dutch corpus) the configured model
must itself be multilingual; a monolingual English model will score poorly and
the results block will still claim ``semantic: on``. That is the operator's
choice, not something this module can detect.
"""

from __future__ import annotations

import heapq
import itertools
import json
import math
import operator
import os
import re
import sqlite3
import struct
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import numpy as _np  # optional: speeds up the semantic scan, see the module docstring
except ImportError:  # pragma: no cover - the standard-library path is the reference
    _np = None

__all__ = ["Index", "rerank", "semantic_rerank", "embeddings_available",
           "embeddings_status"]

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS docs (
    id       INTEGER PRIMARY KEY,
    ref      TEXT UNIQUE NOT NULL,   -- stable source identifier (ECLI, ELI, BOE-ID...)
    title    TEXT NOT NULL DEFAULT '',
    body     TEXT NOT NULL DEFAULT '',
    url      TEXT NOT NULL DEFAULT '',
    lang     TEXT NOT NULL DEFAULT '',
    date     TEXT NOT NULL DEFAULT '',   -- ISO 8601, sorts lexically
    status   TEXT NOT NULL DEFAULT '',   -- in-force / repealed / publisher label
    court    TEXT NOT NULL DEFAULT '',   -- or issuing authority
    subject  TEXT NOT NULL DEFAULT '',
    citation TEXT NOT NULL DEFAULT '',   -- verbatim citation string, never built by the model
    meta     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS docs_date  ON docs(date);
CREATE INDEX IF NOT EXISTS docs_court ON docs(court);

CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    title, body, citation,
    content='docs', content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE VIRTUAL TABLE IF NOT EXISTS docs_tri USING fts5(
    title, body,
    content='docs', content_rowid='id',
    tokenize="trigram"
);

CREATE TABLE IF NOT EXISTS vecs (
    doc_id INTEGER PRIMARY KEY REFERENCES docs(id) ON DELETE CASCADE,
    dim    INTEGER NOT NULL,
    vec    BLOB NOT NULL,
    model  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""

# FTS5 query syntax characters we must not pass through from user input.
_FTS_UNSAFE = re.compile(r'["\'\(\)\*\^:{}\[\]]')


def _fts_query(raw: str, prefix: bool = False, join: str = "AND") -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Quoted phrases survive as phrases; bare words are joined by ``join``.
    Everything else is stripped, because an unescaped ``(`` or ``"`` is an FTS5
    syntax error, not a search for a bracket.

    ``prefix`` appends FTS5's ``*`` operator to bare words. That matters far more
    than it looks: Dutch, Finnish, German and Polish compound aggressively, so a
    search for ``kartel`` does not match ``kartelverbod`` under whole-word
    tokenisation, while ``kartel*`` does.
    """
    phrases = re.findall(r'"([^"]+)"', raw)
    rest = re.sub(r'"[^"]+"', " ", raw)
    words = [w for w in _FTS_UNSAFE.sub(" ", rest).split() if len(w) > 1]
    parts = []
    for p in phrases:
        p = p.replace('"', "")
        if not p.strip():
            continue
        # A quoted phrase needs the dotless-ı OR group too (see _phrase_variants).
        # Measured 2026-09-30 on 3,897 Rekabet texts: "karşı oy gerekçesi" matched
        # 26 decisions as typed and 480 once the capitalised heading was searched.
        variants = _phrase_variants(p)
        parts.append('"%s"' % p if len(variants) == 1 else
                     "(" + " OR ".join('"%s"' % v for v in variants) + ")")
    # A prefix token must sit outside the quotes: "kartel"* is the valid form.
    star = "*" if prefix else ""
    for w in words:
        variants = _dotless_variants(w)
        if len(variants) == 1:
            parts.append('"%s"%s' % (w, star))
        else:
            parts.append("(" + " OR ".join('"%s"%s' % (v, star) for v in variants) + ")")
    return (" %s " % join).join(parts)


def _dotless_variants(word: str):
    """Turkish dotless ı is not a diacritic to unicode61, so ``aydinlatma`` never
    matches ``aydınlatma`` (ş, ğ, ü, ö, ç all fold fine). Search the word as
    typed plus its all-ı and all-i spellings; that covers the keyboard-less
    query and the mixed-dot Turkish word in one OR group."""
    low = word.lower()
    if "i" not in low and "ı" not in low and "İ" not in word and "I" not in word:
        return [word]
    low = low.replace("İ".lower(), "i")
    return list(dict.fromkeys([word, low.replace("i", "ı"), low.replace("ı", "i")]))


def _fold(word: str) -> str:
    """What unicode61 remove_diacritics 2 makes of a word, closely enough to tell
    spellings apart: lower case, no combining marks, ı still distinct from i."""
    return "".join(ch for ch in unicodedata.normalize("NFD", word.lower()) if not unicodedata.combining(ch))


def _phrase_variants(phrase: str, cap: int = 16) -> List[str]:
    """Spellings of a quoted phrase that unicode61 tokenises differently.

    Each word has at most two searchable forms, its ı-spelling and its i-spelling.
    A heading in capitals ("KARŞI OY GEREKÇESİ") folds to "karsi … gerekcesi" and
    running text to "karsı … gerekcesi": the forms mix within one phrase, so no
    whole-phrase respelling reaches both. Every combination is searched up to
    ``cap`` (four words with an i or ı); a longer phrase falls back to the
    whole-phrase respellings of :func:`_dotless_variants`.
    """
    forms = []
    for w in phrase.split():
        seen: Dict[str, str] = {}
        for v in _dotless_variants(w):
            seen.setdefault(_fold(v), v)
        forms.append(list(seen.values()))
    total = 1
    for f in forms:
        total *= len(f)
    if total <= cap:
        return [" ".join(c) for c in itertools.product(*forms)]
    return _dotless_variants(phrase)


# --------------------------------------------------------------------------- #
# Embeddings backend (optional)                                                #
# --------------------------------------------------------------------------- #
# Semantic retrieval should be the normal state, not something an operator has to
# remember to switch on at launch. When EMBEDDINGS_URL is unset the servers fall
# back to a local Ollama, which is free, keyless and already the documented
# setup. Reachability still decides: an unreachable default reports "off" with
# the reason, exactly as an unset variable used to.
_DEFAULT_URL = "http://127.0.0.1:11434/v1/embeddings"
_DEFAULT_MODEL = "bge-m3"

# Liveness is cached so that status calls and per-query checks do not each pay
# for a network round trip.
_PROBE_TTL = 60.0
_probe_cache: Dict[str, Any] = {"at": 0.0, "ok": False, "error": ""}


def embeddings_url() -> str:
    return os.environ.get("EMBEDDINGS_URL") or _DEFAULT_URL


def embeddings_model() -> str:
    return os.environ.get("EMBEDDINGS_MODEL") or _DEFAULT_MODEL


_PROBE_FAIL_TTL = 10.0


def _probe(force: bool = False) -> bool:
    now = time.time()
    # A success is trusted for a minute; a failure only for ten seconds. A single
    # 429 or hiccup used to switch every search on the machine to keyword-only
    # for a full minute, which read as "semantic unavailable" to the model.
    ttl = _PROBE_TTL if _probe_cache["ok"] else _PROBE_FAIL_TTL
    if not force and now - _probe_cache["at"] < ttl:
        return bool(_probe_cache["ok"])
    try:
        _embed(["ping"], timeout=20)
        _probe_cache.update(at=now, ok=True, error="")
    except Exception as exc:  # noqa: BLE001 - unreachable is a reportable state
        _probe_cache.update(at=now, ok=False, error=_describe_failure(exc))
    return bool(_probe_cache["ok"])


def _describe_failure(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        # The status code alone hides the cause: Voyage answers 401 for a missing
        # or invalid key but 403 for a key it recognises and refuses (quota,
        # billing, revoked). Surface the provider's own message so `status`
        # explains the outage instead of restating the code.
        try:
            body = exc.read(600).decode("utf-8", "replace").strip()
        except Exception:  # noqa: BLE001
            body = ""
        return "HTTP %s %s%s" % (exc.code, exc.reason, (": " + body) if body else "")
    return "%s: %s" % (type(exc).__name__, exc)


def embeddings_available() -> bool:
    return _probe()


def _repair_hint() -> str:
    """What an operator should do about an unreachable embeddings backend.

    The old hint always said "ollama serve", which is wrong advice when the
    endpoint is a hosted provider: there the fix is the key or the account.
    """
    url = embeddings_url()
    if "127.0.0.1" in url or "localhost" in url:
        return "Start it with: ollama serve && ollama pull %s" % embeddings_model()
    return ("Check EMBEDDINGS_API_KEY and the provider account behind %s "
            "(key status, quota, billing)." % url)


def _scan_engine() -> str:
    return "numpy %s" % _np.__version__ if _np is not None else "python"


def embeddings_status(refresh: bool = True) -> Dict[str, Any]:
    """The semantic channel's state. ``refresh=False`` reports the last known
    state instead of pinging the backend, for answers that did not use it."""
    source = "env" if os.environ.get("EMBEDDINGS_URL") else "default (local Ollama)"
    up = _probe() if refresh or not _probe_cache["at"] else bool(_probe_cache["ok"])
    if not up:
        return {
            "semantic": "off",
            "endpoint": embeddings_url(),
            "endpoint_source": source,
            "reason": "Embeddings endpoint unreachable (%s) — hybrid search "
                      "degraded to lexical + fuzzy. Results are keyword matches, "
                      "not conceptual matches. %s"
                      % (_probe_cache["error"], _repair_hint()),
        }
    return {
        "semantic": "on",
        "model": embeddings_model(),
        "endpoint": embeddings_url(),
        "endpoint_source": source,
        "scan": _scan_engine() + (", resident %.0f MB" % (_resident_bytes[0] / 1e6) if _resident_bytes[0] else ""),
        "note": "Cross-language retrieval only works if this model is multilingual.",
    }


def _supports_input_type() -> bool:
    """Whether the configured backend understands Voyage's ``input_type``.

    Voyage prepends a different instruction for queries than for documents, which
    measurably helps asymmetric retrieval — a short question against long
    documents, which is exactly this workload. Ollama and OpenAI reject or ignore
    the field, so it is sent only where it means something. Override with
    EMBEDDINGS_INPUT_TYPE=0/1 if the auto-detection is ever wrong.
    """
    override = os.environ.get("EMBEDDINGS_INPUT_TYPE")
    if override is not None:
        return override.strip() not in ("", "0", "false", "no")
    return "voyageai.com" in embeddings_url()


# A query is a few words; anything longer is cut before it is sent. The provider bills by
# input size and a tool argument has no length limit of its own, so one call could
# otherwise send megabytes to be embedded. Documents are clipped where they are built.
QUERY_EMBED_CHARS = 2000


def _embed(texts: Sequence[str], timeout: int = 60,
           input_type: Optional[str] = None) -> List[List[float]]:
    """Call an OpenAI-compatible /v1/embeddings endpoint. Raises on failure."""
    if input_type == "query":
        texts = [str(t)[:QUERY_EMBED_CHARS] for t in texts]
    url = os.environ.get("EMBEDDINGS_URL") or _DEFAULT_URL
    payload: Dict[str, Any] = {"input": list(texts), "model": embeddings_model()}
    if input_type and _supports_input_type():
        payload["input_type"] = input_type
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    # A named agent rather than urllib's default: some providers sit behind bot
    # filters that treat "Python-urllib" from a datacenter address as abuse.
    req.add_header("User-Agent",
                   "arthurlegal-mcp/1.0 (+https://github.com/beerbottle90/arthurlegal-mcp)")
    key = os.environ.get("EMBEDDINGS_API_KEY")
    if key:
        req.add_header("Authorization", "Bearer %s" % key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Rate limits are momentary; one short wait and retry keeps a burst of
        # queries from reporting the semantic channel as unavailable.
        if exc.code != 429:
            raise
        time.sleep(1.5)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    items = data.get("data") or []
    out = [it["embedding"] for it in items]
    if len(out) != len(texts):
        raise RuntimeError(
            "embeddings backend returned %d vectors for %d inputs" % (len(out), len(texts))
        )
    return out


def _pack(vec: Sequence[float]) -> bytes:
    return struct.pack("<%df" % len(vec), *vec)


def _unpack(blob: bytes, dim: int) -> Tuple[float, ...]:
    return struct.unpack("<%df" % dim, blob)


def _normalise(vec: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _query_vector(query: str) -> Optional[List[float]]:
    """The query's unit vector, or None when the embeddings backend is down.

    The embedding call is its own liveness probe. Asking ``_probe`` first sent a
    "ping" ahead of every query that came after a minute of quiet, a second
    round trip to the backend on most real searches. A failure seen in the last
    ``_PROBE_FAIL_TTL`` seconds is trusted, as ``_probe`` trusts it.
    """
    now = time.time()
    if not _probe_cache["ok"] and _probe_cache["at"] and now - _probe_cache["at"] < _PROBE_FAIL_TTL:
        return None
    try:
        vec = _embed([query], input_type="query")[0]
    except Exception as exc:  # noqa: BLE001 - an unreachable backend degrades, it does not fail
        _probe_cache.update(at=now, ok=False, error=_describe_failure(exc))
        return None
    _probe_cache.update(at=now, ok=True, error="")
    return _normalise(vec)


# The cosine of two unit vectors without a Python loop per element: math.sumprod
# runs in C from Python 3.12; map + operator.mul is the next best thing before it.
_dot = getattr(math, "sumprod", None) or (lambda a, b: sum(map(operator.mul, a, b)))

# Vectors read per step of the semantic scan. Bounds what one query holds in
# memory (2,048 x 1,024 float32 = 8 MB) instead of every vector of the index.
_SCAN_CHUNK = 2048

# Resident vectors. With SEMANTIC_RESIDENT_MB set (and numpy installed) an index
# keeps a float16 copy of its vectors in memory and scores a query against it,
# instead of reading 4 KB per vector from SQLite on every query: the reading was
# most of the scan (Poland's 28,150 vectors: 247 of ~300 ms, 2026-10-10), and
# float16 left the top 10 unchanged on four real indexes. The budget is shared by
# every index in the process; an index that does not fit is scanned as before.
_RESIDENT_LOCK = threading.Lock()
_resident_bytes = [0]


def _resident_budget() -> int:
    try:
        return int(float(os.environ.get("SEMANTIC_RESIDENT_MB", "0") or 0) * 1e6)
    except ValueError:
        return 0


class _Resident:
    """One index's vectors for one model and dimension, as a float16 matrix."""

    def __init__(self, key: Tuple[str, int, int], ids: Any, matrix: Any) -> None:
        self.key, self.ids, self.matrix = key, ids, matrix
        self.nbytes = int(ids.nbytes + matrix.nbytes)

    def top(self, qvec: Sequence[float], k: int, allowed: Any = None) -> List[int]:
        q = _np.asarray(qvec, dtype=_np.float32)
        sims = _np.empty(len(self.ids), dtype=_np.float32)
        # Upcast a slice at a time: 2,048 rows x 1,024 float32 = 8 MB per query, not the matrix.
        for i in range(0, len(self.ids), _SCAN_CHUNK):
            sims[i:i + _SCAN_CHUNK] = self.matrix[i:i + _SCAN_CHUNK].astype(_np.float32) @ q
        rows = _np.arange(len(self.ids)) if allowed is None else _np.nonzero(_np.isin(self.ids, allowed))[0]
        if len(rows) > k + 64:
            rows = rows[_np.argpartition(-sims[rows], k + 63)[:k + 64]]
        # The scan's order: higher cosine first, then the higher doc id.
        order = _np.lexsort((-self.ids[rows], -sims[rows]))[:k]
        return [int(i) for i in self.ids[rows[order]]]


# --------------------------------------------------------------------------- #
# Index                                                                        #
# --------------------------------------------------------------------------- #
class Index:
    """A local SQLite corpus with hybrid retrieval.

    ``path`` defaults to ``$INDEX_PATH`` then ``./index.db``. Concurrent readers
    are fine (WAL); a single writer is assumed, which matches the crawl-then-serve
    lifecycle these servers use.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.environ.get("INDEX_PATH") or "index.db"
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        # Older index files predate the model column. Vectors written before it
        # existed carry an empty model and are treated as belonging to whatever
        # model is configured now -- they were produced by it, since there was
        # only ever one.
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(vecs)")}
        if "model" not in cols:
            self.db.execute("ALTER TABLE vecs ADD COLUMN model TEXT NOT NULL DEFAULT ''")
        self.db.commit()

    # -- writing ---------------------------------------------------------- #
    def upsert(self, doc: Dict[str, Any]) -> int:
        """Insert or update one document by its ``ref``. Returns the row id.

        Deliberately does **not** touch the FTS tables: they are external-content
        and get rebuilt wholesale by :meth:`reindex_fts` once a crawl finishes.
        Call that before serving, or searches will miss everything just written.
        """
        cols = ("ref", "title", "body", "url", "lang", "date", "status", "court", "subject", "citation")
        values = [str(doc.get(c) or "") for c in cols]
        meta = json.dumps(doc.get("meta") or {}, ensure_ascii=False)
        row = self.db.execute("SELECT id FROM docs WHERE ref = ?", (values[0],)).fetchone()
        if row:
            doc_id = row["id"]
            self.db.execute(
                "UPDATE docs SET title=?,body=?,url=?,lang=?,date=?,status=?,court=?,"
                "subject=?,citation=?,meta=? WHERE id=?",
                values[1:] + [meta, doc_id],
            )
            # The stored vector described the old body; drop it so embed_missing
            # recomputes one rather than leaving a stale embedding behind.
            self.db.execute("DELETE FROM vecs WHERE doc_id = ?", (doc_id,))
            self._drop_resident()
            return doc_id
        cur = self.db.execute(
            "INSERT INTO docs(ref,title,body,url,lang,date,status,court,subject,citation,meta) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            values + [meta],
        )
        return int(cur.lastrowid)

    def has_ref(self, ref: str) -> bool:
        """True when a document with this ``ref`` is already indexed."""
        return self.db.execute("SELECT 1 FROM docs WHERE ref = ? LIMIT 1", (ref,)).fetchone() is not None

    def has_prefix(self, prefix: str) -> bool:
        """True when any indexed ``ref`` starts with ``prefix`` (one bülten, one dergi sayısı)."""
        like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        return self.db.execute("SELECT 1 FROM docs WHERE ref LIKE ? ESCAPE '\\' LIMIT 1",
                               (like,)).fetchone() is not None

    def reindex_fts(self) -> None:
        """Rebuild both FTS tables from ``docs``.

        External-content FTS5 tables need explicit syncing. Rebuilding wholesale
        after a crawl is far simpler — and for these corpus sizes, faster — than
        maintaining per-row delete/insert triggers.
        """
        self.db.execute("INSERT INTO docs_fts(docs_fts) VALUES('rebuild')")
        self.db.execute("INSERT INTO docs_tri(docs_tri) VALUES('rebuild')")
        self.db.commit()

    def embed_missing(self, batch: int = 64, limit: Optional[int] = None) -> Dict[str, Any]:
        """Compute and store vectors for documents that lack one."""
        if not embeddings_available():
            return {"embedded": 0, **embeddings_status()}
        model = embeddings_model()
        # Only fill gaps for the CURRENT model. Switching models leaves the old
        # vectors in place but unused, so a switch degrades to "not embedded yet"
        # rather than to silently comparing vectors from two different spaces --
        # which would look like it worked, because bge-m3 and voyage-4-lite are
        # both 1024-dimensional.
        #
        # An empty `model` means unknown provenance -- NOT "matches whatever is
        # current". Accepting it as a match is how a Voyage run against an index
        # vectorised by bge-m3 found no gaps at all, left every vector in place,
        # and would have scored them against Voyage queries: the precise failure
        # this guard exists to prevent. Stamp legacy rows with the model that
        # actually produced them instead.
        sql = (
            "SELECT d.id, d.title, d.body FROM docs d "
            "LEFT JOIN vecs v ON v.doc_id = d.id AND v.model = ? "
            "WHERE v.doc_id IS NULL"
        )
        if limit:
            sql += " LIMIT %d" % int(limit)
        rows = self.db.execute(sql, (model,)).fetchall()
        done = 0
        for i in range(0, len(rows), batch):
            chunk = rows[i : i + batch]
            texts = [
                ((r["title"] or "") + "\n" + (r["body"] or ""))[:8000] for r in chunk
            ]
            vectors = _embed(texts, input_type="document")
            for row, vec in zip(chunk, vectors):
                unit = _normalise(vec)
                self.db.execute(
                    "INSERT OR REPLACE INTO vecs(doc_id, dim, vec, model) VALUES(?,?,?,?)",
                    (row["id"], len(unit), _pack(unit), model),
                )
            self.db.commit()
            done += len(chunk)
        if done:
            self._drop_resident()
        return {"embedded": done, **embeddings_status()}

    def set_state(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO state(k,v) VALUES(?,?)", (key, value))
        self.db.commit()

    def get_state(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT v FROM state WHERE k = ?", (key,)).fetchone()
        return row["v"] if row else default

    def count(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) AS n FROM docs").fetchone()["n"])

    def vector_count(self) -> int:
        """Documents carrying a vector for the CURRENTLY configured model.

        Reported separately from the document count because the two diverge in
        the state that is easiest to miss: an index that built but never
        embedded, or one embedded under a different model. Both leave the
        semantic channel reporting itself on while contributing nothing.
        """
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM vecs WHERE model = ? OR model = ''",
            (embeddings_model(),)).fetchone()
        return int(row["n"])

    # -- reading ---------------------------------------------------------- #
    def _where(self, filters: Dict[str, Any]) -> Tuple[str, List[Any]]:
        clauses, params = [], []
        for col in ("lang", "court", "status", "subject"):
            val = filters.get(col)
            if val:
                clauses.append("d.%s = ?" % col)
                params.append(val)
        if filters.get("date_from"):
            clauses.append("d.date >= ?")
            params.append(filters["date_from"])
        if filters.get("date_to"):
            clauses.append("d.date <= ?")
            params.append(filters["date_to"])
        return (" AND " + " AND ".join(clauses) if clauses else ""), params

    def _run_fts(self, expr: str, filters: Dict[str, Any], k: int) -> List[int]:
        if not expr:
            return []
        where, params = self._where(filters)
        sql = (
            "SELECT d.id FROM docs_fts f JOIN docs d ON d.id = f.rowid "
            "WHERE docs_fts MATCH ?" + where + " ORDER BY bm25(docs_fts, 4.0, 1.0, 2.0) LIMIT ?"
        )
        try:
            rows = self.db.execute(sql, [expr] + params + [k]).fetchall()
        except sqlite3.OperationalError:
            # A malformed MATCH expression is a bad query, not a server fault.
            return []
        return [r["id"] for r in rows]

    def _fts_exists(self, expr: str, filters: Dict[str, Any]) -> bool:
        if not expr:
            return False
        where, params = self._where(filters)
        try:
            return self.db.execute(
                "SELECT 1 FROM docs_fts f JOIN docs d ON d.id = f.rowid WHERE docs_fts MATCH ?"
                + where + " LIMIT 1", [expr] + params).fetchone() is not None
        except sqlite3.OperationalError:
            return False

    def _lexical_ladder(self, query: str, filters: Dict[str, Any], k: int,
                        rank_any_word: bool = True) -> Tuple[str, List[int]]:
        """Strict AND first, then progressively looser, stopping as soon as it bites.

        Legal corpora punish a single strategy: an exact multi-term AND is right
        when the user knows the terminology and empty when they are one compound
        boundary off. The ladder keeps precision when precision is available and
        degrades to recall only when the stricter rung returned nothing.

        Returns the rung that answered with its ids. The rung says what kind of
        query this was: in the 2026-10-09 measurement all 72 questions in plain
        words fell to "some words", and 47 of 48 keyword queries matched on
        "all words" or "all words as prefixes". With ``rank_any_word=False`` the
        last rung is only checked for a match, not ranked: on full-text Rekabet
        decisions the ranked any-word query alone took 180-320 ms.
        """
        for rung, expr in (("all words", _fts_query(query)),
                           ("all words as prefixes", _fts_query(query, prefix=True))):
            ids = self._run_fts(expr, filters, k)
            if ids:
                return rung, ids
        expr = _fts_query(query, prefix=True, join="OR")
        if not rank_any_word:
            return ("some words" if self._fts_exists(expr, filters) else "none"), []
        ids = self._run_fts(expr, filters, k)
        return ("some words" if ids else "none"), ids

    def _lexical(self, query: str, filters: Dict[str, Any], k: int) -> List[int]:
        return self._lexical_ladder(query, filters, k)[1]

    def _exact_ref(self, query: str, filters: Dict[str, Any]) -> Optional[int]:
        """The document whose ``ref`` the query is (an ECLI, a BOE id), if any.

        Meaning has no grip on an identifier: the semantic channel found none of
        three ECLIs asked for, and the keyword match it outvoted was the answer.
        """
        q = query.strip()
        if len(q) < 6 or any(ch.isspace() for ch in q):
            return None
        where, params = self._where(filters)
        row = self.db.execute(
            "SELECT d.id FROM docs d WHERE d.ref IN (?, ?, ?)" + where + " LIMIT 1",
            [q, q.upper(), q.lower()] + params,
        ).fetchone()
        return row["id"] if row else None

    def _fuzzy(self, query: str, filters: Dict[str, Any], k: int) -> List[int]:
        # trigram needs a contiguous string of >= 3 chars; use the longest word.
        words = [w for w in re.sub(r"[^\w\s]", " ", query, flags=re.UNICODE).split() if len(w) >= 3]
        if not words:
            return []
        needle = max(words, key=len)
        where, params = self._where(filters)
        sql = (
            "SELECT d.id FROM docs_tri f JOIN docs d ON d.id = f.rowid "
            "WHERE docs_tri MATCH ?" + where + " LIMIT ?"
        )
        try:
            rows = self.db.execute(sql, ['"%s"' % needle] + params + [k]).fetchall()
        except sqlite3.OperationalError:
            return []
        return [r["id"] for r in rows]

    def _drop_resident(self) -> None:
        cached = getattr(self, "_resident", None)
        if cached is not None:
            with _RESIDENT_LOCK:
                if getattr(self, "_resident", None) is cached:
                    self._resident = None
                    _resident_bytes[0] -= cached.nbytes

    def _resident_for(self, model: str, dim: int, scan_max: int) -> Optional["_Resident"]:
        """The resident copy of this index's vectors, (re)built if the file changed.

        ``PRAGMA data_version`` moves when another connection commits (the boot-time
        embedder is another process); this connection's own writes drop the copy.
        None when resident vectors are off, numpy is missing or the budget is spent.
        """
        budget = _resident_budget()
        if _np is None or budget <= 0:
            return None
        key = (model, dim, int(self.db.execute("PRAGMA data_version").fetchone()[0]))
        cached = getattr(self, "_resident", None)
        if cached is not None and cached.key == key:
            return cached
        with _RESIDENT_LOCK:
            cached = getattr(self, "_resident", None)
            if cached is not None and cached.key == key:
                return cached
            count = int(self.db.execute("SELECT COUNT(*) FROM vecs WHERE model = ? AND dim = ?",
                                        (model, dim)).fetchone()[0])
            freed = cached.nbytes if cached is not None else 0
            if not count or count > scan_max or _resident_bytes[0] - freed + count * (dim * 2 + 8) > budget:
                return None
            ids = _np.empty(count, dtype=_np.int64)
            matrix = _np.empty((count, dim), dtype=_np.float16)
            cursor = self.db.execute("SELECT doc_id, vec FROM vecs WHERE model = ? AND dim = ?", (model, dim))
            filled = 0
            while filled < count:
                chunk = cursor.fetchmany(_SCAN_CHUNK)[: count - filled]
                if not chunk:
                    break
                n = len(chunk)
                ids[filled:filled + n] = [r[0] for r in chunk]
                matrix[filled:filled + n] = _np.frombuffer(b"".join(r[1] for r in chunk),
                                                           dtype="<f4").reshape(n, dim)
                filled += n
            resident = _Resident(key, ids[:filled], matrix[:filled])
            self._resident = resident
            _resident_bytes[0] += resident.nbytes - freed
            return resident

    def _allowed_ids(self, filters: Dict[str, Any]) -> Any:
        where, params = self._where(filters)
        if not where:
            return None
        rows = self.db.execute("SELECT d.id FROM docs d WHERE 1 = 1" + where, params).fetchall()
        return _np.fromiter((r[0] for r in rows), dtype=_np.int64, count=len(rows))

    def _semantic(self, query: str, filters: Dict[str, Any], k: int, scan_max: int) -> List[int]:
        qvec = _query_vector(query)
        if qvec is None:
            return []
        resident = self._resident_for(embeddings_model(), len(qvec), scan_max)
        if resident is not None:
            return resident.top(qvec, k, self._allowed_ids(filters))
        where, params = self._where(filters)
        cursor = self.db.execute(
            "SELECT v.doc_id AS id, v.dim, v.vec FROM vecs v JOIN docs d ON d.id = v.doc_id "
            "WHERE v.model = ?" + where + " LIMIT ?",
            [embeddings_model()] + params + [scan_max],
        )
        dim = len(qvec)
        qarr = _np.asarray(qvec, dtype=_np.float32) if _np is not None else None
        scored: List[Tuple[float, int]] = []
        while True:
            chunk = cursor.fetchmany(_SCAN_CHUNK)
            if not chunk:
                break
            rows = [r for r in chunk if r["dim"] == dim]
            if not rows:
                continue
            # Both sides are unit vectors, so the dot product is the cosine.
            if qarr is not None:
                matrix = _np.frombuffer(b"".join(r["vec"] for r in rows), dtype="<f4")
                sims = (matrix.reshape(len(rows), dim) @ qarr).tolist()
            else:
                sims = [_dot(qvec, _unpack(r["vec"], dim)) for r in rows]
            scored.extend(zip(sims, [r["id"] for r in rows]))
        return [doc_id for _, doc_id in heapq.nlargest(k, scored)]

    def search(
        self,
        query: str,
        mode: str = "hybrid",
        limit: int = 20,
        filters: Optional[Dict[str, Any]] = None,
        snippet_chars: int = 320,
    ) -> Dict[str, Any]:
        """Retrieve documents. ``mode``: hybrid | lexical | semantic | fuzzy."""
        filters = filters or {}
        pool = max(limit * 5, 50)
        scan_max = int(os.environ.get("SEMANTIC_SCAN_MAX", "50000"))

        semantic = self._semantic(query, filters, pool, scan_max) if mode in ("hybrid", "semantic") else None
        by_meaning = bool(semantic)
        rung, lexical, filler, by_meaning_only = "", None, [], False
        if mode in ("hybrid", "lexical"):
            # An any-word match is ranked only when nothing else ranks; next to the
            # semantic channel it only fills a short list (module docstring).
            demoted = mode == "hybrid" and by_meaning
            rung, lexical = self._lexical_ladder(query, filters, pool,
                                                 rank_any_word=not demoted or len(semantic) < limit)
            if demoted and rung == "some words":
                filler, lexical, by_meaning_only = lexical, None, True

        # Insertion order breaks ties: a keyword match before a meaning match.
        channels: Dict[str, List[int]] = {}
        if lexical is not None:
            channels["lexical"] = lexical
        if semantic is not None:
            channels["semantic"] = semantic
        if mode == "fuzzy" or (mode == "hybrid" and lexical is not None and (
                not lexical or (not by_meaning and len(lexical) < limit))):
            channels["fuzzy"] = self._fuzzy(query, filters, pool)

        # Reciprocal Rank Fusion: rank-based, so channels with incomparable score
        # scales (BM25 is unbounded and negative, cosine is [-1,1]) combine safely.
        rrf_k = 60
        weights = {"lexical": 1.0, "semantic": 1.0, "fuzzy": 0.4}
        fused: Dict[int, float] = {}
        for channel, ids in channels.items():
            w = weights.get(channel, 1.0)
            for rank, doc_id in enumerate(ids):
                fused[doc_id] = fused.get(doc_id, 0.0) + w / (rrf_k + rank + 1)
        exact = self._exact_ref(query, filters) if mode in ("hybrid", "lexical") else None
        if exact is not None:
            fused[exact] = max(fused.values(), default=0.0) + 1.0

        ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
        ordered += [(doc_id, 0.0) for doc_id in filler if doc_id not in fused]
        ordered = ordered[:limit]
        results = []
        for doc_id, score in ordered:
            row = self.db.execute(
                "SELECT ref,title,url,lang,date,status,court,subject,citation,body,meta "
                "FROM docs WHERE id = ?",
                (doc_id,),
            ).fetchone()
            if row is None:
                continue
            body = row["body"] or ""
            results.append(
                {
                    "ref": row["ref"],
                    "title": row["title"],
                    "citation": row["citation"],
                    "url": row["url"],
                    "date": row["date"],
                    "status": row["status"],
                    "court": row["court"],
                    "lang": row["lang"],
                    "score": round(score, 6),
                    "snippet": body[:snippet_chars] + ("…" if len(body) > snippet_chars else ""),
                }
            )

        vectors = self.vector_count()
        retrieval = {
            "mode": mode,
            "channels_used": {k: len(v) for k, v in channels.items()},
            "indexed_documents": self.count(),
            "vectorised_documents": vectors,
            # Lexical-only answers report the last known state, not a fresh ping.
            **embeddings_status(refresh=mode in ("hybrid", "semantic")),
        }
        if rung:
            retrieval["keyword_match"] = rung
        if by_meaning_only:
            retrieval["ranking"] = ("by meaning: the keyword channel matched only some of the "
                                    "query's words" + ("; its matches follow the meaning matches"
                                                       if filler else ""))
        if exact is not None:
            retrieval["exact_ref"] = "a document's ref equals the query; it is listed first"
        if mode in ("hybrid", "semantic") and embeddings_available() and vectors == 0:
            retrieval["warning"] = (
                "Semantic search is configured but NO documents are vectorised "
                "for model '%s' — the semantic channel contributed nothing and "
                "these are keyword matches. Run crawl.py --embed (or embed_missing) "
                "to build vectors, e.g. after switching embedding models."
                % embeddings_model()
            )
        if mode in ("hybrid", "semantic") and not embeddings_available():
            retrieval["warning"] = (
                "Semantic channel unavailable — these are keyword matches. A "
                "conceptually related document that shares no keywords was NOT "
                "retrieved and may still exist."
            )
        return {"query": query, "total": len(results), "retrieval": retrieval, "results": results}

    def get(self, ref: str) -> Optional[Dict[str, Any]]:
        row = self.db.execute(
            "SELECT ref,title,body,url,lang,date,status,court,subject,citation,meta "
            "FROM docs WHERE ref = ?",
            (ref,),
        ).fetchone()
        if row is None:
            return None
        out = dict(row)
        try:
            out["meta"] = json.loads(out.get("meta") or "{}")
        except ValueError:
            out["meta"] = {}
        return out


# --------------------------------------------------------------------------- #
# Ephemeral reranking                                                          #
# --------------------------------------------------------------------------- #
def rerank(
    query: str,
    docs: Sequence[Dict[str, Any]],
    fields: Sequence[str] = ("title", "body"),
    limit: int = 0,
) -> List[Dict[str, Any]]:
    """Order an upstream result set by relevance to ``query``, in memory.

    For sources that *search* but do not *rank*. Austria's RIS is the clear case:
    ``Suchworte=Aktiengesetz`` returns 1,423 correct hits in alphabetical order,
    so the Aktiengesetz itself is nowhere near the top and the first page is
    worthless. Same query, reranked by BM25, puts it first.

    Builds a throwaway in-memory FTS5 index over the candidates, so it costs one
    pass over what the API already returned — no crawl, no persistence.

    Documents that the FTS query does not match keep their original order after
    the ones that do, rather than being dropped: an upstream hit is still a hit,
    and silently discarding it would hide results the source did return.
    """
    docs = list(docs)
    if not docs or not query.strip():
        return docs[: limit or len(docs)]

    db = sqlite3.connect(":memory:")
    db.execute(
        'CREATE VIRTUAL TABLE r USING fts5(txt, tokenize="unicode61 remove_diacritics 2")'
    )
    for i, doc in enumerate(docs):
        # enumerate, not docs.index(doc): two identical candidate dicts would
        # otherwise both map to the first one's rowid and one would be lost.
        text = " \n ".join(str(doc.get(f) or "") for f in fields)
        db.execute("INSERT INTO r(rowid, txt) VALUES(?,?)", (i + 1, text))

    ranked: List[Dict[str, Any]] = []
    seen = set()
    # Same precision-first ladder as the persistent index.
    for expr in (
        _fts_query(query),
        _fts_query(query, prefix=True),
        _fts_query(query, prefix=True, join="OR"),
    ):
        if not expr:
            continue
        try:
            rows = db.execute(
                "SELECT rowid, bm25(r) AS s FROM r WHERE r MATCH ? ORDER BY s", (expr,)
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        for rowid, score in rows:
            idx = rowid - 1
            if idx in seen or idx >= len(docs):
                continue
            seen.add(idx)
            item = dict(docs[idx])
            item["_rerank_score"] = round(-float(score), 6)
            ranked.append(item)
        if ranked:
            break

    # Unmatched candidates keep their upstream order, appended after the matches.
    for idx, doc in enumerate(docs):
        if idx not in seen:
            ranked.append(dict(doc))
    db.close()
    return ranked[: limit or len(ranked)]


def semantic_rerank(
    query: str,
    docs: Sequence[Dict[str, Any]],
    fields: Sequence[str] = ("title", "body"),
    limit: int = 0,
    max_embed: int = 200,
) -> Dict[str, Any]:
    """Rerank an upstream result set by meaning, with no index and no crawl.

    For **passthrough** servers — ones that forward a query to an upstream API
    and return what comes back. They have no local corpus to embed ahead of
    time, so the usual index-then-search flow does not apply. Here the candidate
    set is small and already in hand, so query and candidates are embedded in a
    single call and ranked by cosine. Nothing is stored.

    This is what makes semantic search possible for e-qanun, LexScholar,
    ResourceContracts and de-eli without changing how they fetch anything.

    The problem it solves is concrete. Searching e-qanun for the Civil Code
    ("Mülki Məcəllə") returns 623 acts, and the first six are all *amendment
    decrees* whose titles happen to contain the phrase — the Code itself is
    buried. The upstream searched correctly; it simply did not rank.

    Falls back to lexical :func:`rerank` when no embeddings backend is
    configured, and says which path it took in ``method`` so a caller can never
    mistake a keyword ordering for a conceptual one.
    """
    docs = list(docs)
    if not docs or not query.strip():
        return {"method": "none", "results": docs[: limit or len(docs)]}

    if not embeddings_available():
        return {
            "method": "lexical",
            "note": "Ranked by BM25 over the candidates. EMBEDDINGS_URL is not "
                    "set, so this is keyword overlap, not meaning.",
            "results": rerank(query, docs, fields=fields, limit=limit),
        }

    # Embedding cost is linear in the candidate count and these are live calls,
    # so cap it. Anything past the cap keeps its upstream order behind the
    # reranked head rather than being dropped.
    head, tail = docs[:max_embed], docs[max_embed:]
    texts = [" \n ".join(str(d.get(f) or "") for f in fields)[:4000] for d in head]
    try:
        # Query and candidates get different instructions on backends that
        # support it; the asymmetry is the point of input_type.
        qv_list = _embed([query], input_type="query")
        doc_vs = _embed(texts, input_type="document")
        vectors = qv_list + doc_vs
    except Exception as exc:  # noqa: BLE001 - degrade, but never silently
        return {
            "method": "lexical",
            "warning": "Embeddings backend failed (%s: %s) — fell back to BM25. "
                       "These are keyword matches." % (type(exc).__name__, exc),
            "results": rerank(query, docs, fields=fields, limit=limit),
        }

    qv = _normalise(vectors[0])
    scored = []
    for doc, vec in zip(head, vectors[1:]):
        unit = _normalise(vec)
        scored.append((sum(a * b for a, b in zip(qv, unit)), doc))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    out = []
    for score, doc in scored:
        item = dict(doc)
        # A real cosine, unlike the index path's RRF rank score.
        item["_similarity"] = round(float(score), 4)
        out.append(item)
    out.extend(dict(d) for d in tail)
    return {
        "method": "semantic",
        "model": embeddings_model(),
        "embedded": len(head),
        "note": "Ranked by cosine similarity. `_similarity` is a real score "
                "(0-1); cross-language matching depends on the model being "
                "multilingual.",
        "results": out[: limit or len(out)],
    }
