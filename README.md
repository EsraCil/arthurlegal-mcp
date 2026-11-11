# ArthurLegal MCP

**Twelve jurisdictions of legal research, plus five other sources, behind one MCP endpoint.**

Türkiye - Netherlands - Poland - Austria - Ireland - Finland - Spain - United
Kingdom - European Union - Japan - Azerbaijan - Germany - legal scholarship -
signed resource contracts - GLEIF entity identity - Turkish land registry (TKGM) -
Turkish fee calculator cards.

132 tools, one hosted endpoint, no authentication:

    https://arthurlegal-mcp.fly.dev/mcp

The same server also answers at `/directory/mcp` with the 107 read-only tools listed in the
Claude connector directory: everything except the land-registry `tkgm_` and fee calculator `hesap_` tools, the
Official Gazette annex page reader `tr_resmi_gazete_ek_sayfa` and the desk hand-over tools `al_masada_basla` /
`al_masaya_yaz`. See
[Claude connector directory](#claude-connector-directory).

Anyone with the URL can call every tool. Self-hosting? Deploy your own copy (see
[Indexes](#indexes)) and use `https://<your-app>.fly.dev/mcp` instead.

## Use it directly

This is the endpoint the ArthurLegal assistant packages use. You can also connect
it to any MCP client on its own (Streamable HTTP transport).

**Claude (claude.ai or Claude Desktop):** Settings -> Connectors -> Add custom
connector. Name: `ArthurLegal MCP`, URL: `https://arthurlegal-mcp.fly.dev/mcp`.
Leave the OAuth fields empty.

**Claude Code:**

    claude mcp add --transport http arthurlegal https://arthurlegal-mcp.fly.dev/mcp

**Clients that take a JSON config with remote servers** (Cursor, VS Code and others):

```json
{ "mcpServers": { "arthurlegal": { "url": "https://arthurlegal-mcp.fly.dev/mcp" } } }
```

**Clients that only launch local (stdio) servers:** bridge with
`npx -y mcp-remote https://arthurlegal-mcp.fly.dev/mcp` as the command.

**Check it is up:**

    curl -s -X POST https://arthurlegal-mcp.fly.dev/mcp       -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream'       -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}'

Then call the `status` tool from your client to see which jurisdictions are loaded.

The hosted endpoint is provided as is, without an uptime guarantee. Your search
queries reach this server and the official upstream sources it queries, and are
sent to the embeddings provider when semantic search runs. Do not put client names
or other confidential facts into queries; mask documents locally first (for
example with [ArthurMask](https://github.com/beerbottle90/arthur-mask)).

Türkiye (`tr_`) is the largest backend: Yargıtay/Danıştay/BAM case law, AYM,
Uyuşmazlık, all legislation types, Resmî Gazete, and eight regulators (EPDK,
Rekabet, SPK, BDDK, KVKK, BTK, GİB, Sigorta Tahkim) behind one
`tr_kurum_karari_ara` interface, plus a locally indexed
archive for `tr_semantik_ara`. Source: the `arthur-tr-hukuk-mcp/` folder (formerly `ArthurLegalTR/`).

The archive holds the decisions' text, not just their titles: 19,498 documents, every one vectorised.
EPDK's decisions before 2016 were old Word files or scans; their text was read on the crawl
workstation (Word files directly, scans with OCR), and when a live document yields no text
`tr_kurum_karari_getir` answers from the archive, with a warning where the text came from OCR.
Dates are checked against each decision's own date line, so a date filter sees every Rekabet
decision (10,445; before 2026-10-12 most of them carried no date and fell outside any range).

`tr_resmi_gazete_tara`, `tr_resmi_gazete_fihrist` and `tr_mevzuat_ara` also take a `konu` filter
(`enerji`, `rekabet`, `vergi`, `icra`): a local, network-free topic triage over titles that finds
items whose titles never mention the topic (in the labelled gazette corpus the topic word appears
in only 10% of `icra` items). In legislation it also reads the names of the laws an omnibus act
amends -- "Bazi Kanunlarda Degisiklik Yapilmasina Dair Kanun" says nothing, Law 7531 amends the
Enforcement and Bankruptcy Code -- and an omnibus act whose names cannot be read is never dropped.
It is a pre-filter, not a guarantee, and it was measured separately per source: gazette
out-of-fold sensitivity 92-100% per topic; on 60 unseen legislation titles vergi 4/4, icra 2/2,
enerji 0/3. Every response says how many items it dropped, `esik=0` disables it, and `status`
reports the model and both measurements. Do not use it for publication verification.

Official Gazette annexes (tariffs, schedules, forms) are often a separate PDF of scanned pages.
`tr_resmi_gazete_getir` lists a page's annex links as `ekler` and, for a PDF, the pages without a
text layer as `taranmis_sayfalar`. `tr_resmi_gazete_ek_sayfa(url, sayfa)` returns one page: its
text, or the page as an MCP image block that the model reads itself. A JPEG scan goes out as the
PDF's own bytes with no extra package; other scans (JBIG2, CCITT) are rendered with `pypdfium2`,
which the hosted image installs (both `Dockerfile` and `Dockerfile.code`); a local run without it
answers those pages with an UYARI line. The tool is on `/mcp` only, not in the directory profile.

## Claude connector directory

The directory listing points at a narrower profile of the same server:

    https://arthurlegal-mcp.fly.dev/directory/mcp

- **107 tools, all read-only.** Every tool carries a `title` and `readOnlyHint: true`
  (`tool_titles.py`), so Claude can run them without asking each time. Nothing on this
  endpoint writes, deletes or sends anything on your behalf.
- **No land-registry or fee calculator tools.** The `tkgm_` and `hesap_` tools stay on `/mcp` only; the desk shows
  ArthurTapu as available in the ArthurLegal install.
- **The same interactive views** as `/mcp`: the article card, the decision list and the desk
  (see [Interactive views](#interactive-views-mcp-apps)).
- **Instructions open with an English description of the tools** instead of the citation rule
  that opens `/mcp`'s; each jurisdiction's own notes then follow as on `/mcp`, in their own
  language and with their own usage rules (the Turkish notes without that citation rule). The
  facts are the same as on `/mcp`.
- **No account and no authentication.** Add it from the directory, or as a custom connector
  with the URL above.

Example prompts:

1. "Quote Article 21 of the Turkish Labour Law (Law 4857) with its citation."
2. "Search Turkish Court of Cassation decisions on reinstatement (işe iade) and open the most
   recent one."
3. "Compare the definition of personal data in the Turkish Personal Data Protection Law (6698,
   Article 3) with section 3 of the UK Data Protection Act 2018."
4. "Which Act governs arbitration in Ireland? Quote the section on the arbitrator's
   jurisdiction."
5. "Which EU regulation lays down harmonised rules on artificial intelligence, and is it in
   force? Give its CELEX number."

Privacy: [PRIVACY.md](PRIVACY.md). Support: ertug@datsproject.io or
[open an issue](https://github.com/beerbottle90/arthurlegal-mcp/issues).

## Turkish land-registry parcel tools (`tkgm_`)

On `/mcp` only (not on `/directory/mcp`), `tkgm_` tools fetch single parcels live from TKGM's Parsel Sorgu -- by province, district and
neighbourhood with block and parcel, by coordinates, or by place name -- and work with them:
plane-projected area and edge lengths, a to-scale A4 SVG sketch, an OSM map, format conversion
(DXF/KML/CSV), route-corridor intersection, 2026 title-deed fee and revolving-fund tariff lookups
(every figure stored with a verbatim source quote), and a bridge from the parcel to the statutes
and case law to check with the `tr_` tools. Live lookups pass one rate-limited gate: at most 30
TKGM requests a minute for all users and machines together, one request in flight per machine, at
least 4 s between starts, at most 3,000 a day; it backs off on 429/503 and stops on 403 (see
`tkgm-mcp/docs/MANIFESTO.md`). The first live call answers with an approval card, so start with
`tkgm_baslangic`. Parsel Sorgu exports vertices rounded to 5 decimals (~1.1 m), so the tools report
the resulting area uncertainty instead of presenting a rounding artefact as a cadastral
discrepancy. On this hosted endpoint file paths are refused (parcel text is passed in `icerik`)
and the tools that write files, fetch elevation data or read title records are disabled; run
`tkgm-mcp/server.py` locally over stdio for those. Source: the `tkgm-mcp/` folder.

## Turkish fee calculator cards (`hesap_`)

On `/mcp` only, five tools compute Turkish legal costs in code rather than in the model: the
attorney minimum fee under the 2026 Attorney Minimum Fee Tariff (Official Gazette 03.11.2026, No.
33419), court fees under Law No. 492 tariff (1) with the 2026 amounts (Official Gazette 31.12.2025,
No. 33124), the civil procedure cost advance (Official Gazette 28.10.2025, No. 33061), an interest
breakdown period by period (statutory interest and commercial default interest under Law No. 3095, with
the 32 central bank rate communiques since 2005 each read from its Official Gazette issue), and gross
severance and notice pay (Law No. 1475 art. 14, Law No. 4857 art. 17; the severance ceiling comes from
the labour ministry's table, since the Official Gazette does not publish it). Every
amount sits in `hesap-mcp/veri/` with a verbatim source quote; every result line carries its legal
basis. Rules the official text does not settle are shown outside the total, marked for checking.
No files. The only network access is a freshness check: about once a day the tools read the Official
Gazette's daily index pages (at most 20 page requests a day; no request data is sent). If a new tariff publication appears, the total is
withheld and the result points to it; if the index cannot be read, the total is marked as unverified. A daily
GitHub job runs the same check and opens an issue for each new publication. Source: the `hesap-mcp/` folder.

## Tool naming

Every research tool carries its jurisdiction or source as a prefix -- `tr_` `nl_` `pl_` `at_` `ie_` `fi_`
`es_` `uk_` `eu_` `jp_` `gleif_` `tkgm_` `hesap_` `az_` `de_` `scholar_` `contracts_`. `status` and the desk
tools (`al_masasi`, `al_masada_basla`, `al_masaya_yaz`) belong to the endpoint itself. Across the underlying servers
`get_act` means six different things and `search_legislation` four, so the
prefix is what keeps a Spanish question from being answered with Finnish law.

Every tool also carries a readable `title` and a safety hint (`tool_titles.py`): research tools
are `readOnlyHint: true`; the few land-registry tools that write a file when run locally are
marked as not read-only and not destructive.

`status` reports every jurisdiction at once: which backends loaded, how many
documents each has indexed, how many are vectorised, and whether semantic
search is live. A backend that fails to load is announced there rather than
quietly returning nothing -- "no results" and "not searched" are different
answers. The German backend runs as a separate process, so `status` also probes
it live (`de_live`); Azerbaijan's probe reports whether e-qanun.az answers now.
On the hosted endpoint it also names the build that answered (`build`: image tag and
region); the machine id and memory are left out of the public answer (`STATUS_DETAIL=1`
brings them back for an operator).

## Interactive views (MCP Apps)

In Claude (web, desktop and the iOS/Android apps) results come with ArthurLegal views. The tools'
JSON is unchanged, so clients without MCP Apps read exactly what they read before.

On `/mcp` every tool opens the same view, the desk (`ui://arthurlegal/masa.html`): Claude's full-screen
pane shows the newest view, so a tool with a card of its own would replace the desk. Once the desk is
open, each call becomes a live step on it (what was searched, how many results; the article or decision
list opens under the step). In a chat without the desk the same view draws the old card for the two
tools below and a one-line step for the rest. `/directory/mcp` keeps the two cards as before.

- `tr_mevzuat_madde_getir`: an article card with the law and Official Gazette details, each
  article's heading (with its parent headings for codes such as TBK and TMK), the text, the
  citation to quote with a copy button, a link to the official text and a verified / partly
  verified / not verified badge. An article that could not be fetched carries the `UYARI` line.
- `tr_ictihat_ara`: a decision list with chamber, docket numbers and date; each decision's text
  opens inside the card and can be handed to Claude for a summary.
- `al_masasi`: ArthurLegal Masası, a full-screen desk with legislation and citation check,
  case law, source status and the other ArthurLegal modules in one panel. On this endpoint the
  modules that run on the lawyer's computer (memory, masking, UYAP) are shown as desktop-only;
  on `/directory/mcp` land registry is shown that way too. The desk keeps its history (searches, fetched articles, answers) for 90
  days in its own storage on the device, across chats and updates; nothing is written to the server.
- `al_masada_basla` (`/mcp` only): puts the question on the desk as soon as Claude starts, before the
  research; the steps and the answer then join it.
- `al_masaya_yaz` (`/mcp` only): while the desk is full screen the chat transcript sits behind it and
  the desk cannot read it, so Claude hands its answer over with this tool (question, steps, Markdown
  answer, sources). The host reopens the desk with the tool's input and the answer streams in as it is
  written; the desk then checks the answer's statutory citations against the official text. The server
  stores nothing and echoes nothing back. The order (start, research, hand over; "Cevap masada." in
  the chat) is part of this endpoint's instructions, near the top. Neither tool is on `/directory/mcp`,
  whose descriptions give no instructions; there the answer stays in the chat. The MCP screen lists connected servers and their
  status (Claude reports them when it opens the desk), not their tools.

The views are in `arayuz/` (served as `ui://arthurlegal/...`). Light theme by default, dark and
automatic on request; the device's system font; the only origin the sandbox allows is unpkg, for
the pinned MCP Apps client (`@modelcontextprotocol/ext-apps`), which the browser loads only if its
sha384 fingerprint matches the one in the view's import map.

## Germany (`de_`)

Two sources stand behind the `de_` prefix:

- **de-eli** (pinned `de-eli-mcp==0.5.4`, a separate FastMCP process proxied over
  HTTP): NeuRIS legislation and case law (`/v1/rechtsprechung`, seven federal courts
  since 2010, full text), rechtsprechung-im-internet.de, the Bundestag DIP and Open
  Legal Data. The aggregator reranks its search results with the same embeddings as
  every other jurisdiction, rebuilds decision citations from NeuRIS's German field
  names, returns `de_recent_changes` newest first and compacts DIP results.
- **de-gii-mcp** (`de_norm_getir`, `de_gesetz_ara`): the consolidated text of one
  norm from gesetze-im-internet.de. NeuRIS is still in its test phase and lacks the
  core codes -- on 2026-10-09 `/v1/legislation?abbreviation=` returned 0 for BGB, HGB,
  StGB, ZPO, StPO, AO, UrhG, GWB and InsO -- so `de_search` points there when an
  abbreviation is missing.

## Indexes

Six jurisdictions are searched from a local SQLite index (FTS5 + vectors); the
rest are queried upstream and reranked in memory. The indexes are built on a
workstation and baked into the image at `baked/<jurisdiction>.db`:

    python crawl.py --embed          # in each jurisdiction directory
    # copy each finished index to baked/<directory>.db, e.g. baked/es-boe-mcp.db
    flyctl deploy                    # full image (Dockerfile); start.sh seeds from baked/

Crawling is a maintenance task, not something a booting container does. Baking
the databases in means a machine is never healthy-but-empty, and the corpus can
never drift away from the code that was tested against it. `start.sh` still
falls back to crawling when no baked index is present, and says which it used:
`indexes present: N/6`.

What the baked indexes hold and when they were last crawled, from the live `status` of
2026-11-10. These rows go stale: the current values are in `status` (`last_crawl`,
`index_coverage`, `local_index`), and this table is updated with every refresh. The exact
command line of a past run was not recorded; the command below is the `crawl.py` call that
gives the scope `status` reports, for the Turkish index the refresh that adds new decisions
(run in the index's directory; add `--embed` to compute vectors where a key is set, the
Turkish index is vectorised on the machine at boot).

| Index | Command | Scope | Documents | Last crawl |
|---|---|---|---|---|
| `arthur-tr-hukuk-mcp` | `sh tazele.sh` (new decisions per regulator, Rekabet text, `--pack`) | Rekabet, EPDK, BTK, SPK, BDDK, KVKK, Sigorta Tahkim; ranges per regulator in `local_index.by_kurum` | 19,498 | 2026-10-10 |
| `nl-rechtspraak-mcp` | `python crawl.py --from 2026-10-05 --to 2026-10-25` | decisions 2026-10-05..2026-10-25, summaries | 7,050 | 2026-10-26 |
| `pl-sejm-mcp` | `python crawl.py --publisher DU --from 2015 --to 2026` | Dziennik Ustaw 2015-2026: titles, type, issuer, keywords | 28,150 | 2026-10-10 |
| `ie-statutebook-mcp` | `python crawl.py --from 2015 --to 2026` | Acts 2015-2026, as enacted, full text | 503 | 2026-10-10 |
| `fi-finlex-mcp` | `python crawl.py --type statute-consolidated --from 2025 --to 2026` | consolidated statutes 2025-2026, Finnish and Swedish | 250 | 2026-10-26 |
| `es-boe-mcp` | `python crawl.py --max 0`, then `python crawl.py --backfill-text` | consolidated legislation: metadata and the first 8,000 characters of the text in force | 12,439 | 2026-10-26 |

### Index updates

Regulator decisions are append-only: a decision is unique by its `ref`, does not change once
published, and new ones are added. The one exception is Rekabet, which lists a decision by its
title first and publishes the reasoned text later (the body is updated once). So the Turkish
index is refreshed without a full image:

- [`.github/workflows/kurum-taramasi.yml`](.github/workflows/kurum-taramasi.yml) runs on Mondays
  and Thursdays at 03:30 UTC (most boards meet on Thursdays; Monday leaves about four days for
  publication and finishes before the weekly audit at 06:00 UTC), or by hand (`kuru`: report
  only). There is no baked index on GitHub's machines; a small registry on the `veri/ek` branch
  (`arthur-tr-hukuk-mcp/ek/kayit.json.gz`: every known ref, and the Rekabet decisions still
  without text) stands in for it. [`arthur-tr-hukuk-mcp/ek_tarama.py`](arthur-tr-hukuk-mcp/ek_tarama.py)
  runs the same `crawl.py --only-new` path (a ref it does not know is new, whatever its decision
  date: the lists are in publication order) and the Rekabet text backfill, with page limits sized
  for two runs a week and at least one second between two requests to the same host. A regulator
  that fails does not stop the others. The new and corrected documents go into one file, e.g.
  `arthur-tr-hukuk-mcp/ek/2026-11-13.jsonl.gz` (gzipped JSON lines, written once); the job
  commits it and the registry to `veri/ek` (never to `master`) and starts the deploy. The job
  that parses the regulators' pages and PDFs has read-only permissions; writing and deploying
  are separate jobs.
- The deploy ([`fly-dagitim.yml`](.github/workflows/fly-dagitim.yml)) brings only
  `<backend>/ek/*.jsonl.gz` and the registry from `veri/ek` into the build, as regular files,
  checked by format ([`.github/dagitim/ek_getir.py`](.github/dagitim/ek_getir.py)), and labels
  the image `gh-<commit>-ek-<data commit>`. No branch: it deploys without updates. A branch it
  cannot read, or a file that fails the check: the deploy stops, so the index never silently
  falls back. The live check then also requires that no local index lost documents.
- At boot `start.sh` applies the files on top of the copied index with [`indeks_ek.py`](indeks_ek.py),
  in the background, before filling missing vectors. Applying is idempotent and incremental (103
  Turkish documents: 0.5 s; rebuilding the search tables instead took 72 s and a 932 MB WAL). A
  document whose title or body changed loses its vector and gets a new one in the same pass; one
  whose date, citation or other metadata alone was corrected keeps it. A broken, truncated or
  foreign file is skipped and logged. `status` shows `index_updates` (files, documents, latest).
- The run's summary and an annotation titled `kurum-tarama-ozet` carry it as JSON: new decisions,
  newest decision date, requests and errors per regulator.

The format fits every local index (they share `retrieval.Index`):
`python indeks_ek.py uret --taban <live base> --yeni <refreshed copy> --ad pl-sejm-mcp --cikti <dir>`
turns a workstation refresh of Poland, Ireland or another index into an update file, and
`python indeks_ek.py karsilastir <base + updates> <refreshed copy>` checks that the two are the
same. Only the Turkish regulators are crawled on a schedule.

A full image is needed only when the base is rebuilt (or the Python base image changes): apply
the branch's updates to a copy of the base, refresh it (`tazele.sh`), bake it, regenerate the
registry from it (`python ek_tarama.py kayit`), and reset `veri/ek` to the registry alone.
Updates accumulate until then (Turkish decisions: a few MB of text a month).

### Code-only deploys

A full image carries the indexes, so every full deploy uploads about 2.3 GB of build
context. Most releases change only code. [`Dockerfile.code`](Dockerfile.code) starts
from the last full image in the app's registry and copies the code over it, so the
upload is a few megabytes and a deploy takes minutes:

    git worktree add --detach ../deploy origin/master   # clean checkout: no baked/
    cd ../deploy
    flyctl deploy . --dockerfile Dockerfile.code --depot=false --image-label <label>

Run it from a clean checkout: `*.db` files are ignored by git, so a fresh worktree has
no `baked/` and nothing large is sent. `--depot=false` uses Fly's own builder, which
pulls the base image from the app's registry. `Dockerfile.code` also runs the same
pinned `pip install` line as `Dockerfile` (`tests/test_paketler.py` keeps them equal):
a new or changed Python package is fetched by the builder, so it ships with a
code-only deploy too. Build a full image (`Dockerfile`) only when the indexes or the
base Python image change, then use its tag from then on:
`--build-arg BASE=registry.fly.io/arthurlegal-mcp:<full-image-tag>`.
Files deleted from the code since the base image stay in the image until the next
full build.

### Automatic deploys

[`.github/workflows/fly-dagitim.yml`](.github/workflows/fly-dagitim.yml) runs the
code-only deploy above on GitHub's machines: when the tests pass on a push to
`master`, it deploys that commit (only if it is still the tip of `master`, so an
older commit never lands over a newer one), then checks the live server with
[`.github/dagitim/canli_denetim.py`](.github/dagitim/canli_denetim.py): every running
machine is on the new image, the tool counts did not drop, the desk page matches the
commit, the calculation cards answer. If a check fails it redeploys the previous
image and opens an issue. If Fly's builder does not start, the build is tried once
more and then made on the GitHub machine itself
([`.github/dagitim/dagit.sh`](.github/dagitim/dagit.sh)); a failure after the build
is not retried. It needs the repository secret `FLY_API_TOKEN`, a deploy
token scoped to this app (`flyctl tokens create deploy -a arthurlegal-mcp`). A
manual run (Actions, "Fly dağıtımı", Run workflow) redeploys `master`; tick
"Araç sayısı bilerek azaldı" when a release removes tools on purpose. Full images
are still built by hand.

`hybrid`, the default search mode, ranks keyword and meaning matches together,
but not blindly. When the keyword channel found every word of the query, both
count; when it matched only some of the words -- the usual case for a question
asked in plain words -- the ranking is by meaning and the keyword matches only
fill a short list. A query that is a document's identifier (an ECLI, a BOE id)
lists that document first. Each response says which happened
(`retrieval.keyword_match`, `retrieval.ranking`, `retrieval.exact_ref`). The rule
comes from a 120-query known-item measurement over the six local indexes
(2026-10-09). `mode: "lexical"` and `mode: "semantic"` still give either channel
alone. With numpy installed (the image has it) the semantic scan is about six
times faster; without it the ranking is the same.

## Configuration

| Name | Kind | Purpose |
|---|---|---|
| `EMBEDDINGS_API_KEY` | secret | Voyage AI key. Required for semantic search. |
| `EMBEDDINGS_URL` | env | `https://api.voyageai.com/v1/embeddings` |
| `EMBEDDINGS_MODEL` | env | `voyage-4-lite` -- multilingual, 1024-dim |
| `DE_ELI_URL` | env | German backend; `off` to disable |
| `EQANUN_PROXY` | env | Azerbaijan: e-qanun.az is reached through `az-relay` (Frankfurt, private network only), because it does not answer from Amsterdam |
| `SEMANTIC_RESIDENT_MB` | env | memory budget for vectors kept as float16 (all indexes together; `0` or unset = read from SQLite per query). Hosted: `160` on 1 GB machines (meaning searches ~30-50% faster); at 512 MB keep `0`, the SQLite page cache needs that memory more (with 141 MB of vectors, Turkish and Polish searches slowed from ~1.2 s to 7-8 s) |
| `EPDK_ISITMA` | env | EPDK decision lists fetched once in the background after boot, one request every 4 s (~131 per machine, at most 30 a minute for two machines), so the first `tr_kurum_karari_ara(kurum="epdk")` does not wait ~40 s; a list past its 6 h lifetime is served while it is refreshed the same way. Unset: on only under `MCP_TRANSPORT=http` (the hosted image); `0` off, `1` on |

A document holds exactly one vector (`vecs.doc_id` is the primary key), so
changing `EMBEDDINGS_MODEL` does not add a second vector -- the next embedding
run overwrites the old one. Until it does, queries find no vectors for the new
model and semantic search reports itself off. That is deliberate: bge-m3 and
voyage-4-lite are both 1024-dimensional, so mixing them would raise nothing and
rank by distances computed across two unrelated vector spaces.

Without a key the server still answers, on BM25 and fuzzy matching, and every
response says `semantic: "off"` rather than passing a keyword match off as a
conceptual one.

## Denetim logu (yalnız test uygulaması)

`AUDIT_LOG=1` ile açılan ağ geçidi, modelin "Madde kontrolü: … okundu" beyanını bağımsız bir
kayıtla karşılaştırmak için her araç çağrısını tek satırlık bir JSON kaydı olarak HER ZAMAN
standart hata (stderr) akışına yazar: fly logs stdout ile stderr'i birlikte toplar, stdio'da
protokol kanalı (stdout) temiz kalır. Kayıt satırları `{"olay":` ile başlar; tüketiciler bu öneke
göre süzer (stderr'de sunucunun başka satırları da vardır). Ayrıntı: `denetim.py`.

- `olay: "cagri"`: `ts`, `session_id`, `request_id`, `tool`, `params`, `resolved`
  (`tr_mevzuat_madde_getir`: kanun/madde/ok/mulga/durum; `durum` arka ucun ham madde durumu,
  `mulga` yalnız `durum == "repealed"`; `tr_mevzuat_getir`: kanun/mevzuat_id), `pages` (sayfa,
  toplam, ilk-son madde), `mulga`, `status` (ok, partial, error, rate_limited, upstream_blocked,
  unavailable), `duration_ms`, `sure_siniri_asildi` (≥ 100 sn: Claude.ai çağrıyı keser),
  `backend_version`, `returned` (bütün araçlarda modele giden metnin sha256'sı ve ilk 300
  karakteri), `kimlikler` (sonuç listelerindeki kayıtların `document_id`, `karar_id`, `ref`,
  `esas_no`, `karar_no`, `citation` gibi kimlik alanları, en çok 100; başlık ve metin yok).
- `olay: "iptal"`: `notifications/cancelled`; çağrıyla `session_id` + `request_id` ile eşleşir.
- Maskeleme yok: `params` arama metni dahil argümanların olduğu gibi kopyasıdır. Gizlenen her
  alan ölçülemeyen bir davranış olurdu ("aradım, bulamadım" beyanı, arama sonucundan atıf).
  İstemcinin `session_id`, `request_id`, `reason` metinleri en çok 200 karakter yazılır.
- Dönen metnin tamamı değil sha256 + ilk 300 karakteri yazılır: metni kesin tanımlar, aynı
  parametrelerle yeniden getirilebilir, log satırını şişirmez. Kaynak sonradan değişirse eski
  metin geri gelmez; fark yalnız özetten görünür.
- Yalnız açıkken `initialize` yanıtı `Mcp-Session-Id` başlığı taşır (kimlik doğrulanmaz,
  bilinmeyen kimlik reddedilmez). Kapalıyken başlık yoktur, yanıtlar bayt bayt aynıdır.

**Herkese açık uygulamada kapalı kalmalı.** Yalnız ayrı test uygulamasında ve yalnız sentetik
sorularla (atıf sınav kümesi): sunucu kimlik doğrulamasız, adrese gerçek dosya bilgisiyle bağlanan
olursa o bilgi de kayda girer. `AUDIT_LOG` tanımsız ya da `1` dışında bir değerse kayıt kapalıdır.

Canlı uygulamada açılmaz: `FLY_APP_NAME` `fly.toml`'daki ad (`arthurlegal-mcp`) iken `AUDIT_LOG=1`
yok sayılır, stderr'e tek satır uyarı düşer ve sunucu kayıtsız çalışmayı sürdürür. Açıkken
`/directory/mcp` sunulmaz, yalnız `/mcp` kalır: test uygulamasında dizin trafiği yoktur ve o yolun
araçları sarılmadığından çağrıları kayda girmez, ölçümü yanıltırdı. [PRIVACY.md](PRIVACY.md)'deki
"writes no request logs" beyanıyla çelişmez: o beyan canlı uç noktalar (`arthurlegal-mcp.fly.dev`)
içindir ve kayıt orada hiç açılmaz.

Test uygulaması [`fly.denetim.toml`](fly.denetim.toml) ile kurulur. Dosya `fly.toml`'ın kopyasıdır,
yalnız dört satırı farklıdır: ad, boştayken durma (iki satır) ve TKGM makine sayısı. Ayrılmadığını
`tests/test_denetim.py` sınar. Uygulama canlıyla aynı Fly organizasyonunda açılır; böylece
`Dockerfile.code` canlının taban imajını kullanır, `az-relay` iç ağdan erişilir.

    fly apps create arthurlegal-mcp-denetim
    fly secrets set AUDIT_LOG=1 EMBEDDINGS_API_KEY=<voyage-anahtarı> -a arthurlegal-mcp-denetim
    flyctl deploy . -c fly.denetim.toml --dockerfile Dockerfile.code --depot=false

Doğrulama: loglarda `denetim logu AÇIK` satırı görünür, `/directory/mcp` 404 döner. Makine boştayken
durduğundan ölçümden önce `fly machine start -a arthurlegal-mcp-denetim` ile kaldırılır; yoksa ilk
çağrı başarısız sayılır. `fly logs` yalnız yakın geçmişi gösterir, ölçüm süresince kayıtlar dosyaya
alınır:

    fly logs -a arthurlegal-mcp-denetim | grep '{"olay":' > denetim-kaydi.jsonl

---

Source: [github.com/beerbottle90/arthurlegal-mcp](https://github.com/beerbottle90/arthurlegal-mcp) · License: [MIT](LICENSE); each backend folder carries its own copy.
