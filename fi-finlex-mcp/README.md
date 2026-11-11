# fi-finlex-mcp

**Finland 🇫🇮 legal research over the Model Context Protocol.**
opendata.finlex.fi — Akoma Ntoso, Ministry of Justice

No authentication. No dependencies — pure Python standard library, so it runs on
a stock Python 3.9+ with nothing to install.

## Why this server exists

A clean Akoma Ntoso REST API with no full-text search, three non-obvious traps, and two equally authoritative languages. This server unpacks the format, indexes both languages, and refuses to let a caller invent a version identifier.

## Tools

- `search_acts`
- `browse_year`
- `get_act`
- `recent_changes`
- `server_status`

Every search response carries a `retrieval` block reporting which channels ran,
how many documents are indexed, and whether semantic search is actually on — so
a thin result set is never mistaken for a settled question.

## Search

Three retrieval channels, fused with Reciprocal Rank Fusion:

| Channel | What it is | Always available |
|---|---|---|
| `lexical` | SQLite FTS5 + BM25, diacritic-insensitive, with a strict-AND → prefix → OR ladder | yes |
| `fuzzy` | FTS5 trigram, for substrings and misspellings | yes |
| `semantic` | Dense vectors over an OpenAI-compatible embeddings endpoint | **only if configured** |

**Semantic search is opt-in and honest about it.** The standard library cannot
run a transformer, so dense retrieval needs an external endpoint.

The verified local setup — free, no API key (tested 2026-08-30):

```sh
ollama pull bge-m3                    # 1.2 GB, 100+ languages, 1024 dims
export EMBEDDINGS_URL=http://127.0.0.1:11434/v1/embeddings
export EMBEDDINGS_MODEL=bge-m3
python crawl.py ... --embed           # vectorise the index
```

Ollama's `/v1/embeddings` is OpenAI-compatible, so the server talks to it
unchanged. Any other OpenAI-compatible endpoint works the same way:

```
EMBEDDINGS_URL=https://api.example.com/v1/embeddings
EMBEDDINGS_MODEL=...                         # optional
EMBEDDINGS_API_KEY=...                       # optional
```

Measured cross-language separation with `bge-m3` (Turkish query, multilingual
candidate pool): conceptually related documents averaged **0.70** cosine,
unrelated ones **0.41** — a 0.29 gap. A Turkish query for climate and carbon
targets returned the Strategic Gas Reserve, Air Pollution and Environment Acts
under `semantic`, and pure noise under `lexical`, because no Turkish word
appears in an English statute.

With nothing configured, `mode="hybrid"` degrades to lexical + fuzzy and **says
so** in every response. It never presents a keyword match as a conceptual one.
For cross-language work — a Turkish question against a Finland corpus — the
configured model must itself be multilingual; that is the operator's choice and
this server cannot verify it.

## Upstream quirks this server handles

- `main.akn` is a ZIP package containing main.xml, not raw XML — parsing it directly fails with a misleading 'not well-formed' error.
- `/list` is a 5-item change feed, not the corpus.
- The root URL 403s while every /finlex/avoindata/v1/... path works.
- `{lang@version}` is mandatory on main.akn and must never be invented.

## Run it

```sh
# stdio, for a desktop MCP client
python server.py

# Streamable HTTP, for a remote connector
python server.py --transport http --port 8000
# -> http://127.0.0.1:8000/mcp
```

### Build the search index first

```sh
python crawl.py --from 2020 --to 2026
```

Direct fetches and browsing work without it; keyword search does not.

### Connecting from claude.ai

Settings → Connectors → *Add custom connector* → the `/mcp` URL → auth **None**.

> ⚠️ This server has **no authentication**. Binding it to `0.0.0.0` exposes every
> tool to anyone who can reach the port. That is why `--host` defaults to
> loopback and must be widened deliberately.

## Docker / Railway

```sh
docker build -t fi-finlex-mcp .
docker run -p 8000:8000 -e PORT=8000 fi-finlex-mcp
```

`start.sh` launches the server immediately and crawls in the background when
`CRAWL_ARGS` is set and no index exists yet, so the platform health check at
`/health` passes straight away.

## Deploying to Smithery

This repo ships `smithery.yaml`, so it can be deployed as a hosted container:

1. Push the repo to GitHub (done).
2. On [smithery.ai](https://smithery.ai), add the server from this GitHub repo.
3. Deploy. Smithery builds `Dockerfile` and serves Streamable HTTP on `/mcp`.

Optional configuration, all of it safe to leave empty:

| Variable | Effect |
|---|---|
| `CRAWL_ARGS` | What to index on first boot (default `--from 2020 --to 2026`) |
| `EMBEDDINGS_URL` | Turns the semantic channel on |
| `EMBEDDINGS_MODEL` | Must be multilingual for cross-language search |
| `EMBEDDINGS_API_KEY` | Bearer token, if the endpoint needs one |
| `FI_SEMANTIC_MIN_Z` | How far (standard deviations, default 3.4) a meaning-only match must stand above the query's similarity to the whole index; below it the result is marked `likely_unrelated`, never removed |

> Container filesystems are usually ephemeral. The index rebuilds on a cold
> start, which is why the crawl runs in the background and the server answers
> from the first second — direct fetches and browsing never need an index.

## Citation discipline

Every result carries a `citation` field built from what the source returned.
**Copy it verbatim.** Do not assemble an ECLI, a Dz.U. number, a BOE id or an
ELI by hand — malformed identifiers are rejected rather than guessed at.

## Licence

MIT. The underlying legal data belongs to its publisher and carries that
publisher's own terms.

---

Part of the [ArthurLegal](https://github.com/beerbottle90/ArthurLegal)
multi-jurisdiction legal assistant. Built in the spirit of
[saidsurucu/yargi-mcp](https://github.com/saidsurucu/yargi-mcp).
