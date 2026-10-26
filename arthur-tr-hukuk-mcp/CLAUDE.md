# CLAUDE.md — arthur-tr-hukuk-mcp (eski adı ArthurLegalTR)

Turkish legal research MCP server. Standard library only (+ optional `pypdf`).

## Run / test

```bash
python server.py                                  # stdio
python server.py --transport http --port 8080     # POST /mcp, GET /health
python tests/test_offline.py                      # 51 offline tests, no network
python crawl.py --source kvkk,bddk --embed        # build data/index.db
```

## Layout

- `server.py` — tool table + instructions. Every tool is a thin `_wrap()` over an adapter function.
- `sources/<kurum>.py` — one upstream each; exposes `SOURCE = Source(key, label, kind, notes, search, get, crawl, …)`.
  Add a regulator = add a module + its name to `sources.MODULES`. Nothing else changes.
- `net.py` — urllib client: cookies, token bucket, charset fallback, IRI quoting, TLS cipher override.
- `textx.py` — HTML/PDF → text, Turkish lower/fold, pagination, excerpts.
- `retrieval.py` — vendored FTS5 + trigram + vector index (RRF). `_dotless_variants` is the only local change.
- `mcpcore.py` — vendored JSON-RPC MCP core. Do not edit; upstream is arthurlegal-mcp.
- `crawl.py` — builds the local index; stamps `subject = <kurum key>` so `semantik_ara` can filter.

## Rules that are not style

- **Citations come from the record.** Every result carries `citation`; adapters build it from upstream
  fields, never from inference. Do not "improve" a citation by guessing a missing number.
- **Unavailable ≠ empty.** A failed upstream returns `error` / `upstream_blocked` / `unavailable`. Never
  convert that into an empty `results` list.
- **Bedesten rate limit is real** (10 req / 30 s per IP). `bedesten_ictihat.BUCKET` is shared with mevzuat.
  Don't add parallel fan-out over Bedesten.
- **No paid keys, no half-working sources.** KVKK/BDDK/Sigorta Tahkim are parsed directly. A source whose
  official endpoint does not answer reliably is REMOVED, not shipped with a stub (see below).
- **Windows console is cp1254.** Run tests with `PYTHONIOENCODING=utf-8`.

## Removed sources (2026-09-06) — do not re-add without a working live test

- **KİK** — EKAP v2 signs requests (`X-Ekap-Sec-1..6`, AES-CBC with `environment.r8fact`); with the key from
  the reference implementation the API answers HTTP 500. `r8fact` is not a string literal in any shell
  chunk or module-federation remote. Needs a browser capture of one signed request.
- **Sayıştay** — WAF answers 418 to every DataTables POST, browsers included (also from Fly's IP).
- **TÜRKPATENT** — no public decision database; research portal is reCAPTCHA-gated.
- **İSTAÇ** — istac.org.tr did not resolve from Fly nor from the workstation (nor by forced IP) on 2026-09-06.
  Rules PDFs are static; re-add when the host is back (old adapter in git history: `git show db9a7ea^:ArthurLegalTR/sources/istac.py`).

## Aggregator integration

`server.py` exposes module-level `TOOLS` and `_t_status`; arthurlegal-mcp loads this directory first so its
`retrieval.py` (dotless-ı) is the cached copy for every backend.
