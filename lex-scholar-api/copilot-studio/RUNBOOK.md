# Copilot Studio runbook — LexScholar MCP connector

How to give a Copilot Studio agent access to ten open-access legal
scholarship sources through **one** custom connector.

> **Import through the web UI only.** Do not use the Power Platform CLI (`pac`)
> or any scripted attach/publish flow for this environment — it breaks the
> Copilot Studio setup. Every step below is done in the browser.

## 1. Host the MCP server over public HTTPS

```powershell
.\run-public.ps1          # Windows
```
```bash
./run-public.sh           # macOS / Linux
```

Wait for the box showing `https://XXXX.trycloudflare.com`. Your MCP URL is that
host **+ `/mcp`**.

Quick-tunnel hostnames rotate on every run. For a pilot, use a **named
Cloudflare tunnel** or deploy `server.py --transport http` to a container/VM
behind stable HTTPS, then keep that hostname.

## 2. Put the host into the connector definition

Open `mcp-connector.swagger.json` and replace
`REPLACE-WITH-YOUR-PUBLIC-HTTPS-HOST` with the **hostname only**:

```json
"host": "xxxx.trycloudflare.com",
```

Not `https://xxxx…`, not `…/mcp`. The scheme lives in `schemes` and the path in
`paths` — including them here produces a connector that cannot call anything.

## 3. Import it (browser)

1. <https://make.powerapps.com> → **Custom connectors** → **+ New custom
   connector** → **Import an OpenAPI file**.
2. Name it `LexScholar MCP` and upload `mcp-connector.swagger.json`.
3. **General**: Host = your hostname · Base URL = `/` · Scheme = HTTPS.
4. **Security**: **No authentication** (every upstream is public and this server
   adds none).
5. **Definition**: a single `InvokeServer` operation appears — leave it exactly
   as is. MCP streamable uses one endpoint and the tool list is discovered at
   runtime.
6. **Create connector** → **Test** → **+ New connection** (no credentials asked).

## 4. Attach it to the agent

Copilot Studio → your agent → **Tools** → **+ Add a tool** → **Model Context
Protocol** → **LexScholar MCP** → **Select all**.

Expected tools: `search_legal_scholarship`, `compare_jurisdictions`, `get_scholarship_article`,
`get_scholarship_fulltext`, `resolve_doi`, `list_sources`.

Recommended tool description:

```text
Acik erisimli HUKUK LITERATURU (hakemli makale/doktrin) icin dokuz indeksi tek uctan arar: DOAJ (hakemli garantili), Law Review Commons (ABD hukuk dergileri; petrol-gaz, enerji, tahkim konu setleri), SciELO (Latin Amerika, tam metin), HAL (Fransa), Dialnet (Ispanya), OpenAIRE (AB), Crossref+Unpaywall (DOI/acik kopya), OpenAlex (bosluk yargi cevreleri). Router her soruya uygun 2-3 indeksi secer. compare_jurisdictions ile ayni soruyu birden cok ulkede karsilastir. KARSILASTIRMALI DOKTRIN kaynagidir: mevzuat veya ictihat DEGILDIR, yasal sonucun nihai dayanagi olamaz; birincil kaynagi TR Legal MCP Pro / e-qanun ve resmi kaynaklardan dogrula. peer_reviewed_only=true hakemli olmayanlari (preprint, ABD ogrenci editorlu law review) eler.
```

## 5. Verify

Ask the agent:

- *"Enerji yatirim tahkimi uzerine hakemli makale bul"* → `search_legal_scholarship` with
  `peer_reviewed_only`.
- *"Stabilizasyon klozu Fransa, Brezilya ve Endonezya'da nasil tartisiliyor?"* →
  `compare_jurisdictions`.

## Notes

- **No auth** is intentional. Consider IP-allowlisting the origin for production.
- **Attribution is an obligation, not a nicety.** Every record carries `license`
  and a ready `citation`. DOAJ signals `ai-train=no` (retrieve-and-cite fine,
  training not); Dialnet requires written consent to reproduce, so it is wired
  as discovery-only.
- **`OPENALEX_API_KEY`** (free) raises OpenAlex's metered budget ~100x. Without
  it the router simply holds OpenAlex back when the anonymous budget runs low.
- Law Review Commons is student-edited (`peer_reviewed=false`) — do not let the
  agent present it as peer-reviewed scholarship.
