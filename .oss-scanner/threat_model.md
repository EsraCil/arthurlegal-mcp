# Threat model: ArthurLegal MCP

## What this project does and where untrusted input enters

A read-only legal research server for lawyers. One Streamable-HTTP MCP endpoint (`server.py` + `mcpcore.py`,
deployed on Fly.io at `https://arthurlegal-mcp.fly.dev/mcp`, plus a `/directory/mcp` profile without the `tkgm_`
and `hesap_` tools, `tr_resmi_gazete_ek_sayfa` and the desk hand-over tools) aggregates 132 tools over 12
jurisdictions and five other sources (LEI, scholarship, contracts, land registry, fee cards). Each backend folder (`arthur-tr-hukuk-mcp`,
`nl-rechtspraak-mcp`, `eu-cellar-mcp`, `tkgm-mcp`, …) fetches public court, legislation and regulator websites.

The endpoint has no authentication by design: anyone on the internet can call any tool. Treat all of these as
untrusted:

- **Every tool argument.** Callers are Claude clients, but the model chooses arguments and can be steered by
  prompt injection in documents it reads, so assume an attacker controls them. Identifiers, URLs, search text,
  page numbers, list arguments, SPARQL text (`eu_sparql`), KML/GeoJSON (`tkgm_parsel_oku`).
- **Upstream responses.** HTML, JSON, XML, PDF, DOCX, ZIP from about 30 government and academic sites. A site can
  be compromised or impersonated; parsing must not crash the process or run code.
- **Model-written text rendered in the desk.** `al_masaya_yaz` stores an answer that `arayuz/` renders inside the
  Claude host's MCP App iframe.

Sensitive data: lawyers' search text and the matter facts it contains, the desk's questions and answers, parcel
locations, `EMBEDDINGS_API_KEY` (Voyage), and the Fly organisation's private network (`*.internal`, `fdaa::/16`).

## Components that matter most / least

- **Most:** the gateway (`server.py`, `mcpcore.py`), the shared HTTP client `arthur-tr-hukuk-mcp/net.py` (every
  Turkish source fetches through it; it restricts each source to its own domain, caps response and decompressed
  size), any code that turns a tool argument into a URL, host or file path (for example `tr_resmi_gazete_ek_sayfa`,
  which takes a URL and accepts only `https://www.resmigazete.gov.tr/`), decompression and archive handling,
  rendering of untrusted scanned PDF pages with native code (`pypdfium2`/PDFium in
  `arthur-tr-hukuk-mcp/sources/resmi_gazete.py`), TLS settings, and the desk/card HTML in `arayuz/` and `tkgm-mcp/ui/`.
- **Medium:** per-country backends (`*-mcp/`, `lex-scholar-api`, `eqanun-api`, `resourcecontracts-api`), the local
  semantic index (`retrieval.py`, SQLite FTS5 + vectors), `tkgm-mcp` file output (stdio/local mode only; under
  HTTP it writes no files), `hesap-mcp/tazelik.py` (reads the Official Gazette's daily index pages about once a
  day; no caller input is sent).
- **Least:** `tests/`, `varlik/` (static images), `denetim.py` (audit logging used only on a separate test app),
  `arthur-tr-hukuk-mcp/ocr.py` (needs Tesseract, not installed in the deployment).
- **Out of scope:** the upstream websites themselves, `de-eli-mcp` (a pip package run on 127.0.0.1:8790), Voyage,
  the Fly platform, the Claude host's sandbox, unpkg.

## How to exercise it

- `python server.py` (stdio) or `python server.py --transport http --port 8080` (POST `/mcp`, GET `/health`).
- Gateway tests: `python -m unittest discover -s tests -p "test_*.py"` (one file per process; see
  `.github/workflows/testler.yml`). Backend tests: `cd <backend> && python -m unittest discover -s tests`.
- `arthur-tr-hukuk-mcp/tests/test_net_guvenlik.py` shows how to drive `net.Http` against a local server.
- The six prebuilt indexes (`baked/`, ~2 GB) are not in the repository; search tools fall back to live sources or
  report the index as unavailable. Tests do not need them.

## How we rate severity

- **Critical:** code execution on the server; reading environment secrets; one caller reading another caller's
  queries, answers or desk content.
- **High:** a tool argument that makes the server contact a host other than the tool's own upstream (SSRF), or
  read local files; a single request that exhausts memory or takes the service down; disabling TLS verification
  for an upstream from outside; script injection into the desk or cards.
- **Medium:** query text or answers stored or logged beyond what `PRIVACY.md` states; one caller exhausting a
  shared upstream budget (rate limits, Voyage quota) for everyone; missing Origin/Host checks when run locally.
- **Low:** information in `status` output, container hygiene, unpinned versions.

## Anything to leave alone

- No authentication and no CORS headers on `/mcp` and `/directory/mcp`: intended, the data is public law.
- `eu_sparql` is a deliberate passthrough to the EU Publications Office endpoint; report only escapes from it.
- TLS is verified for every upstream. `HTTP_TLS_FALLBACK=1` (unset in deployment) lets an operator fall back to
  unverified TLS for a source's own domain while its certificate is broken; report only paths that disable it
  without that setting.
- SQL in `retrieval.py` and `_where` inserts fixed column names, not input; XML from government sites parsed with
  ElementTree is expected.
