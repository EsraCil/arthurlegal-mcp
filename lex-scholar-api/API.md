# Upstream API reference (verified)

Black-box map of the ten sources behind this server. Every endpoint, parameter
and count below was **verified live on 2026-07-24/25** from a normal client. No
authentication is used anywhere; where a "key" exists it is optional and noted.

---

## 1. DOAJ — `https://doaj.org/api`

The spine. Indexes **only peer-reviewed open-access journals**, and exposes the
actual process, which is what turns "peer reviewed" from an assumption into a
field.

| Endpoint | Notes |
|---|---|
| `GET /search/articles/{query}?page=&pageSize=` | pageSize max 100 |
| `GET /articles/{id}` | single article |
| `GET /search/journals/{query}` | `editorial.review_process` e.g. `["Double anonymous peer review"]` |

Query syntax is Elasticsearch. Law filter: **`bibjson.subject.term:law`**.

**Gotchas (both verified):**

- `subject:"Law"` returns **HTTP 200 with `total: 0`** — silently wrong field.
- the API **hard-caps at 1,000 records** per query; deeper harvesting needs the
  OAI-PMH set `TENDOkxhdw~~` (base64 `LCC:Law`, 153,444 records).
- DOAJ defaults to **OR** across terms, so `force majeure` matched "force" *or*
  "majeure". This client ANDs terms and preserves `"quoted phrases"`.
- **User-Agent trap:** DOAJ answers **403 to any UA containing a `+https://…`
  crawler URL** (the bot-declaration convention). Identical request with the
  `+URL` removed returns 200. This silently disabled the spine until found.

Counts: 268,222 law articles · 1,265 law journals. By country —
ID 191 · BR 90 · PL 66 · ES 58 · IR 42 · IT 37 · GB 37 · RU 31 · UA 24 · TR 20 ·
EG 20 · MX 15 · US 10 · RO 10 · NL 10 · DE 8 · FR 7 · CH 7 · IQ 6 · ZA 5 · KZ 2.
**AZ, IN, JP, AE = 0** — those need OpenAlex.

Domain volumes: competition law 807 · arbitration 1,634 · mining 607 ·
oil-and-gas 199 · force majeure 165 · petroleum 140 · energy law 89.

---

## 2. Law Review Commons — `https://lawreviewcommons.com/do/oai/`

OAI-PMH, 351,887 records, **67 subject sets**. Matters because no dedicated
open-access energy-law journal exists, so topical sets beat journal filtering.

```
?verb=ListRecords&metadataPrefix=oai_dc&set=publication:oil-gas-mineral-law
```

Verified set sizes: `oil-gas-mineral-law` 845 · `energy-utilities-law` 1,305 ·
`dispute-resolution-arbitration` 2,082 · `natural-resources-law` 8,738 ·
`international-trade-law` 2,281. Pages are 100 records with a `resumptionToken`.

- **Not peer reviewed** — US law reviews are student-edited.
- OAI-PMH has **no free-text search**; the set is the filter and matching is
  done client-side.
- the `viewcontent` PDF link answers **HTTP 202 with an empty body** (bepress
  bot interstitial), so full text is offered as a link, never promised.
- `robots.txt` disallows `/do/` for generic agents.

---

## 3. SciELO — `https://articlemeta.scielo.org/api/v1` (+ site for full text)

The only no-auth source in this server that yields **real body text**.

| Endpoint | Notes |
|---|---|
| `GET /journal/?collection=scl` | journal directory (~3 MB) |
| `GET /article/identifiers/?collection=&issn=&limit=&from=&until=` | `from=` works for incremental sync |
| `GET /article/?collection=&code={PID}&format=json` | full record + `citations` |
| `GET /collection/identifiers/` | 36 collections |

Law journals are matched by substring on **`v440`** (free text: `DERECHO`,
`DIREITO`, `DERECHO INTERNACIONAL` …) — there is no clean law code.

**Full text (verified):** the article payload carries a top-level **`fulltexts`**
map, e.g. `{"html": {"pt": "http://www.scielo.br/scielo.php?script=sci_arttext&pid=…"}}`
→ 200, 155,250 bytes. **Use that link.** The modern
`/j/{acron}/a/{id}/?format=xml` form needs SciELO's *internal document id*, not
the PID — building it from the PID **404s**. A direct internal-id URL does work
(verified 129,890 bytes with `<body>`, and `?format=pdf` → 461,536 bytes).

Other traps: the journal acronym is at **`title.v68`**, not on the article
record; `articlemeta … format=xmlrsps` returns `<front>`+`<back>` only (no body);
legacy `scielo.br/oai/scielo-oai.php` is **dead (404)**; `search.scielo.org`
returns **403 to all bots**, so discovery must run over ArticleMeta; `scielo.cl`
is Cloudflare-guarded while `scielo.br` is fine.

Law volume 26,003 across ~70 journals — Chile 7,421 · Mexico ~4,250 ·
Brazil 4,228 · Colombia 3,052 · Costa Rica 1,594 · Argentina 945 · Portugal 824.

---

## 4. HAL — `https://api.archives-ouvertes.fr/search/`

Solr. Best discipline facet of any source: **`domainAllCode_s:shs.droit`** →
**236,486** law documents, **26,422** open access. The intuitive `domain_s`
silently returns 0.

Useful fields: `docid, title_s, abstract_s, authFullName_s, journalTitle_s,
producedDateY_i, language_s, doiId_s, uri_s, fileMain_s, openAccess_bool,
peerReviewing_s`. `fileMain_s` is a real PDF.

**Trap:** forcing `sort=producedDateY_i desc` discards relevance ranking and
returns whatever is newest in the law domain, which looks like the query was
ignored. Sort only when browsing.

---

## 5. Dialnet — `https://dialnet.unirioja.es/oai/OAIHandler`

OAI-PMH. **`set=18` = "Ciencias jurídicas"** — a genuine native law facet, and
the largest Spanish-language law corpus measured here: **95,402 records**.

Discovery only, deliberately: records expose a `servlet/oaiart` landing page and
**no PDF** (100/100 sampled), and `dc:rights` **requires express written consent
for reproduction**. Cite, never redistribute. No free-text search → date-windowed
harvest with client-side matching.

---

## 5b. DergiPark — `https://dergipark.org.tr/api/public/oai/`

Turkish academic journals (TÜBİTAK ULAKBİM), reached through **DergiPark's own
published OAI-PMH interface** — no key, no CAPTCHA.

This deserves a note, because the widely-used third-party `literatur-mcp` gets
the same content by driving a stealth browser (Camoufox) through DergiPark's bot
protection with a **paid CapSolver key**. Measured 2026-07-25, that stack's
search hung past 25s with no response while its MCP handshake still answered in
0.2s — a broken CAPTCHA/scraping pipeline behind a healthy MCP layer. The
official endpoint answered the same session in **0.2–0.5s**. So this adapter
uses the front door; nothing here defeats a bot protection.

| Verb | Result |
|---|---|
| `Identify` | 200, `repositoryName: DergiPark`, protocol 2.0 |
| `ListMetadataFormats` | `oai_dc`, `oai_etdms`, `oai_marc`, `oai_mods` |
| `ListRecords` | 200, 100 records/page with `resumptionToken` |
| `ListSets` | 200 — but **caps at 100 sets with no `resumptionToken`** |

Records carry title, `dc:creator` (authors), **`dc:description` (abstract)**,
`dc:subject`, `dc:source` (journal), `dc:date`, `dc:language` and the article
URL. Header id looks like `oai:dergipark.org.tr:article/1002051`.

**Traps handled in the adapter:**

- **Rate limiting.** Roughly ten rapid requests earn a `429`; calls are spaced
  2 s.
- **`ListSets` truncation.** DergiPark hosts thousands of journals but exposes
  only 100 sets there, and the response ends at `</ListSets>` with no
  `resumptionToken` — the catalogue cannot be enumerated through OAI. A
  `setSpec` still works when absent from that page, so coverage is a
  **verified list of 19 law journals**, assembled from three independent routes
  and then checked one code at a time: the law sets DergiPark does expose,
  DOAJ's Turkish law-subject journals (homepage URL carries the slug) and
  OpenAlex source records. Harvesting `ListRecords` to discover journals is
  ineffective — 4,500 records surfaced only 9 distinct journals, because records
  cluster by journal. The site's own search is not an option either:
  `robots.txt` disallows `/search` for every agent.
- **No free-text search** (inherent to OAI-PMH), so the journal set is the
  filter and matching happens client-side — same shape as Dialnet and Law Review
  Commons.
- **Verify before adding a code.** A plausible slug is not enough: `ybhd` looks
  like a law code and is the Intensive Care Nursing journal, `ashd` likewise
  returns medical articles. Both were caught during verification and excluded.
- `earliestDatestamp` reports the current day, which is misleading — date-window
  queries do return older records (2015, 2020 and 2024 windows all did).

Verified law journals (19) — 15 law faculty journals: `auhfd`, `ahbvuhfd`,
`asbuhfd`, `andhd`, `duhfd`, `deuhfd`, `inuhfd`, `iuhfm`, `kouhfd`, `maruhad`,
`neuhfd`, `shd`, `suhfd`, `yuhfd`, `kahd`; plus 4 subject journals: `iuchkd`
(Ceza Hukuku ve Kriminoloji), `iuihid` (İdare Hukuku ve İlimleri),
`adaletdergisi`, `ihad` (İslam Hukuku Araştırmaları).

`peer_reviewed` is left `None`: most DergiPark journals are hakemli, but
`oai_dc` carries no review-process field, so it is not asserted.

## 6. OpenAIRE Graph — `https://api.openaire.eu/graph/v1`

`GET /researchProducts?search=&fos=0505%20law&type=publication&pageSize=&page=`
→ **582,820** law records. Explicit `isPeerReviewed`.

**Country filter (verified):** **`countryCode=AZ`** works, narrowing 44,275 → 4.
`country=` and `relCountryCode=` break the response instead of filtering.

---

## 7. Crossref — `https://api.crossref.org/works`

Polite pool via `mailto`. **No working discipline filter** —
`filter=category-name:Law` returns 0 and subject metadata is deprecated. Used
here strictly as a DOI/metadata resolver, never for law discovery.

## 8. Unpaywall — `https://api.unpaywall.org/v2/{doi}?email=`

Email is a parameter, not a key. 100k calls/day. DOI → best open copy; used to
upgrade hits from other sources that lack a PDF.

## 9. OpenAlex — `https://api.openalex.org` ⚠ metered

**`primary_topic.subfield.id:subfields/3308` = Law.** Counts: 2,244,618 law works
→ 1,364,657 `type:article` → 895,966 in journal venues → **362,231 open access**.

**Metered since Feb 2026** (verified, not assumed): anonymous calls still return
200 but carry `X-RateLimit-Limit: 1000` with `X-RateLimit-Limit-USD: 0.1`/day,
and a list request costs 10 credits → roughly **100 anonymous searches/day**
(observed `X-RateLimit-Remaining: 138`). A free `OPENALEX_API_KEY` raises it
~100x. The `mailto` polite pool was retired.

Reserve it for what only it can do: **gap jurisdictions** (AZ has 0 DOAJ law
journals but **193 OA law works** here) and citation traversal. Abstracts arrive
as an inverted index and must be rebuilt.

---

## Endpoint status matrix

| Source | Auth | Law filter | Full text | Status |
|---|---|---|---|---|
| DOAJ | none | ✅ native | abstract+link | ✅ verified |
| Law Review Commons | none | ✅ 67 sets | ⚠ 202 interstitial | ✅ verified |
| SciELO | none | ⚠ substring | ✅ body text | ✅ verified |
| HAL | none | ✅ native | ✅ PDF/TEI | ✅ verified |
| Dialnet | none | ✅ native set | ❌ by policy | ✅ verified |
| DergiPark | none | ⚠ curated setSpecs | abstract+link | ✅ verified |
| OpenAIRE | none | ✅ fos | link | ✅ verified |
| Crossref | none | ❌ broken | link | ✅ verified |
| Unpaywall | email param | — | locator | ✅ verified |
| OpenAlex | ⚠ metered | ✅ native | link | ✅ verified |

## Rejected during recon (verified failures)

CORE — `fullText` is literally `"Not available for public API users."`, and
anonymous bursts 429 on the second call · Semantic Scholar — 429 on the *first*
anonymous call (shared global pool) · BASE — IP whitelist required · AJOL — 403 ·
Cairn — DataDome · SSRN / CNKI / Redalyc / AustLII / Scilit / HeinOnline — no
public API · **HuggingFace** — 2,553 legal datasets swept, **no law-review
corpus exists**; the closest proxy is OpenAlex-derived, abstracts only, mixed
non-commercial licensing.

**Already covered elsewhere:** Switzerland — the OpenCaseLaw.ch MCP already
indexes 25,425 Swiss open-access scholarship records with full text.
