# at-ris-mcp

**Austria 🇦🇹 legal research over the Model Context Protocol.**
data.bka.gv.at/ris/api/v2.6 — Federal Chancellery OGD

No authentication. No dependencies — pure Python standard library, so it runs on
a stock Python 3.9+ with nothing to install.

## Why this server exists

RIS searches its corpus correctly but **does not rank**: `Suchworte=Aktiengesetz` returns 1,423 genuine hits in alphabetical order, so the Aktiengesetz is not on the first page. This server reranks each page by meaning before answering (BM25 when no embeddings endpoint is configured). No crawl needed.

## Tools

- `search_legislation`
- `search_caselaw`
- `fetch_document`
- `server_status`

Every search response carries `total_upstream` (RIS's own hit count), `returned`
and a `ranking` block whose `method` says how the page was ordered — so a thin
result set is never mistaken for a settled question.

## Search

There is no local index and no crawl: every search goes to RIS, and the page it
returns is reranked here.

| `ranking.method` | What it is | When |
|---|---|---|
| `semantic` | Cosine similarity over an OpenAI-compatible embeddings endpoint | an endpoint is configured |
| `lexical` | BM25 over the page's candidates | no endpoint, or the endpoint failed (`ranking.note` says which) |

**Semantic ranking is opt-in and honest about it.** The standard library cannot
run a transformer, so it needs an external endpoint.

The verified local setup — free, no API key (tested 2026-08-30):

```sh
ollama pull bge-m3                    # 1.2 GB, 100+ languages, 1024 dims
export EMBEDDINGS_URL=http://127.0.0.1:11434/v1/embeddings
export EMBEDDINGS_MODEL=bge-m3
```

Ollama's `/v1/embeddings` is OpenAI-compatible, so the server talks to it
unchanged. Any other OpenAI-compatible endpoint works the same way:

```
EMBEDDINGS_URL=https://api.example.com/v1/embeddings
EMBEDDINGS_MODEL=...                         # optional
EMBEDDINGS_API_KEY=...                       # optional
```

With nothing configured, the page is ranked by BM25 and `ranking.method` **says
so** in every response. It never presents a keyword match as a conceptual one.
For cross-language work — a Turkish question against a Austria corpus — the
configured model must itself be multilingual; that is the operator's choice and
this server cannot verify it.

## Upstream quirks this server handles

- No relevance ranking — results come back alphabetically.
- `Kurztitel` is SILENTLY IGNORED: passing it returns all 441,066 documents.
- API v2.5 was retired and now 404s; this client uses v2.6.
- Page sizes are 10, 20, 50 or 100 only; `limit` returns fewer results.
- A rejected request comes back as an error object inside HTTP 200 — raised
  here, never shown as zero hits.
- Much of the OGH corpus is Rechtssätze — legal propositions, not judgments.
- A result's `url` (Dokument.wxe, eli/) is a web page that answers 503 to
  automated clients; `fetch_document` reads the same document from its
  /Dokumente/ file and returns extracted text, not page source.

## Run it

```sh
# stdio, for a desktop MCP client
python server.py

# Streamable HTTP, for a remote connector
python server.py --transport http --port 8000
# -> http://127.0.0.1:8000/mcp
```

### Connecting from claude.ai

Settings → Connectors → *Add custom connector* → the `/mcp` URL → auth **None**.

> ⚠️ This server has **no authentication**. Binding it to `0.0.0.0` exposes every
> tool to anyone who can reach the port. That is why `--host` defaults to
> loopback and must be widened deliberately.

## Docker / Railway

```sh
docker build -t at-ris-mcp .
docker run -p 8000:8000 -e PORT=8000 at-ris-mcp
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
| `CRAWL_ARGS` | What to index on first boot (default ``) |
| `EMBEDDINGS_URL` | Turns the semantic channel on |
| `EMBEDDINGS_MODEL` | Must be multilingual for cross-language search |
| `EMBEDDINGS_API_KEY` | Bearer token, if the endpoint needs one |

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
