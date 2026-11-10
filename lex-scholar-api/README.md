# lex-scholar-api

Dependency-free Python client + **single-endpoint MCP server** federating ten
open-access **legal scholarship** sources (eight search indexes plus two DOI resolvers), for the ArthurLegal assistants (Claude
Projects and Copilot Studio).

> Status (2026-07-25): **working, 20/20 live tests passing.** One `/mcp`
> endpoint, ten upstream indexes, no authentication anywhere, pure Python
> standard library on a stock Python 3.9+. Built to the same template as the
> sibling [`eqanun-api`](../eqanun-api) and
> [`resourcecontracts-api`](../resourcecontracts-api).

## Why this exists

Peer-reviewed legal scholarship is scattered across national indexes that do not
talk to each other: Brazilian doctrine is in SciELO, French in HAL, Spanish in
Dialnet, Indonesian and Iranian in DOAJ, US law reviews in Law Review Commons.
Asking one comparative-law question meant eight different searches.

This server makes it one call — and, critically, **does not fan out blindly**.
A deterministic router reads the question and picks the 2-3 indexes that can
actually answer it.

## Two design commitments

**One endpoint, many sources.** `source` is a tool parameter, not a deployment.
Adding another index later changes nothing for the agent or the connector.

**Multi-jurisdiction by construction.** `compare_jurisdictions` runs one question
across several countries and returns the answers *grouped*, each routed to its
own native index — the contrast is the deliverable, not a merged ranking.

## Sources (all verified live, no auth)

| Source | Law filter | Peer review | Full text | Volume (law) |
|---|---|---|---|---|
| **DOAJ** — the spine | `bibjson.subject.term:law` | **guaranteed** (`review_process`) | abstract + link | 268,222 articles / 1,265 journals |
| **Law Review Commons** | 67 OAI subject sets | ✗ student-edited | PDF link | 351,887 (oil-gas 845, energy 1,305, arbitration 2,082) |
| **SciELO** | `v440` ⊃ DIREITO/DERECHO | curated | **real body text** | 26,003 articles, 36 collections |
| **HAL** (FR) | `domainAllCode_s:shs.droit` | `peerReviewing_s` flag | PDF + TEI | 236,486 / 26,422 OA |
| **Dialnet** (ES) | `set=18` Ciencias jurídicas | mixed | ✗ discovery only | 95,402 |
| **DergiPark** (TR) | 19 verified law-journal setSpecs | unknown (mostly hakemli) | abstract + link | 19 Turkish law journals |
| **OpenAIRE** | `fos=0505 law` | `isPeerReviewed` flag | link | 582,820 |
| **Crossref** | — | — | — | DOI resolver |
| **Unpaywall** | — | — | locator | DOI → open copy |
| **OpenAlex** ⚠ metered | `subfields/3308` | inferred | link | 2,244,618 / 362,231 OA |

`peer_reviewed` is **tri-state** on every record: `true` (source guarantees it),
`false` (known not to be — preprints, US student-edited law reviews), `null`
(unknown). Never assume; `peer_reviewed_only=true` keeps only `true`.

## Install / requirements

None. Pure standard library, Python 3.9+. Optionally set `OPENALEX_API_KEY` to
raise OpenAlex's metered budget ~100x — everything works without it, and the
router simply holds OpenAlex back when its anonymous budget runs low.

## Library

```python
from lexscholar import LexScholarClient

c = LexScholarClient()

# Federated search — the router picks the sources
res = c.search("force majeure", peer_reviewed_only=True, limit=10)
print(res["sources_queried"], res["routing_reasons"])

# One question, several jurisdictions, grouped answers
cmp = c.compare_jurisdictions("stabilization clause", ["FR", "BR", "ID", "TR"])

# Real body text (SciELO), paginated
ft = c.get_fulltext("scielo", "S1806-64452007000100004", collection="scl")

c.resolve_doi("10.1093/jiel/jgaa002")
c.list_sources()
```

## CLI

```bash
python3 -m lexscholar search "force majeure" --peer-reviewed --brief -n 10
python3 -m lexscholar search "arbitraje de inversiones" --jurisdiction ES
python3 -m lexscholar compare "stabilization clause" FR BR ID TR
python3 -m lexscholar fulltext scielo S1806-64452007000100004 --out article.txt
python3 -m lexscholar doi 10.1093/jiel/jgaa002
python3 -m lexscholar sources
```

## MCP server

```bash
python3 server.py                                              # stdio
python3 server.py --transport http --host 0.0.0.0 --port 8000  # http://<host>:8000/mcp
```

Env fallbacks: `LEXSCHOLAR_MCP_TRANSPORT`, `LEXSCHOLAR_MCP_HOST`, `LEXSCHOLAR_MCP_PORT`.

### Tools

| Tool | Purpose |
|---|---|
| `search_legal_scholarship` | routed federated search; reports `sources_queried`, `sources_skipped`, `routing_reasons` |
| `compare_jurisdictions` | one question across N countries, grouped per jurisdiction |
| `get_scholarship_article` | full normalized metadata by source + id |
| `get_scholarship_fulltext` | body text, paginated; says so when only a PDF link exists |
| `resolve_doi` | Crossref metadata + Unpaywall open copy |
| `list_sources` | capability cards incl. live OpenAlex budget |

### Public HTTPS in one command

```bash
./run-public.sh          # macOS / Linux
```
```powershell
.\run-public.ps1         # Windows (PowerShell has no && and cannot run .sh)
```

Prints a `https://<random>.trycloudflare.com/mcp` URL. Quick-tunnel hostnames
rotate every run; use a named Cloudflare tunnel for a stable URL.

## How routing works

| Signal | Route |
|---|---|
| DOI in the query | Crossref + Unpaywall only — discovery skipped |
| `jurisdiction=` | that country's native index (BR→SciELO, FR→HAL, ES→Dialnet, AZ→OpenAlex) |
| query language | es/pt→SciELO+Dialnet, fr→HAL, de→OpenAIRE, **tr→DergiPark+DOAJ**, ru→DOAJ+OpenAlex |
| energy/arbitration vocabulary | + Law Review Commons (suppressed if the query is scoped to a non-US country) |
| `peer_reviewed_only=true` | drops preprint and student-edited sources |
| OpenAlex budget low | OpenAlex skipped before it 429s, with the reason reported |
| always | DOAJ anchors — except where it demonstrably has nothing (AZ, IN, JP, AE) |

Override with `source="doaj,hal"` or `source="all"`.

## Smoke test

```bash
python3 examples/smoke_test.py
```

20 assertions across routing, federation, peer-review filtering, record shape,
multi-jurisdiction comparison, full text, DOI resolution and capability cards.

## Verification highlights (2026-07-25)

- `search_legal_scholarship("stabilization clause", peer_reviewed_only=true)` → top hit
  *"The Stabilization Clause of the Baku-Tbilisi-Ceyhan Pipeline"*, 0.4s, DOAJ only.
- `compare_jurisdictions("arbitration", [FR,BR,ID,AZ])` → all four answered, each
  via its own index (`hal`, `scielo`, `doaj`, `openalex`).
- SciELO full text → 57,021 chars of clean article body.
- Five upstreams in parallel = 0.96s wall clock.

## Governance

Read-only research over public open-access content. Two obligations travel with
the data and are surfaced on every record via `license` and `citation`:

- **DOAJ** `robots.txt` signals `ai-train=no`. The API/OAI are separate
  documented machine interfaces and the metadata is CC0, so retrieve-and-cite is
  consistent with that signal — **model training is not**.
- **Dialnet** `dc:rights` requires express written consent for reproduction, so
  it is wired as **discovery only**: citations yes, redistribution no.

Law Review Commons is US student-edited (`peer_reviewed=false`) — authoritative
practice literature, but never to be presented as peer-reviewed scholarship.
Cite the `citation` field; it carries source attribution by construction.
