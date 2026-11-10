# eqanun-api

Dependency-free Python client, CLI and MCP server for **Azerbaijan's official
legal-acts database** (e-qanun.az, Ministry of Justice). Usable as a research
tool by any MCP-capable assistant or Copilot Studio agent, or directly from
Python and the shell.

_Last verified against the live API: 2026-07-30. The full search → metadata →
full-text chain works; the core client uses only the Python standard library and
runs on a stock Python 3.9+._

## What it does

- **Search** acts by title or full text, filter by status (in force / cancelled
  / all) **and by act type**, paginated — `GET /getDetailSearch`.
- **Count** matches without pulling the result list — `GET /detailNameCount`
  (the count comes back in the `id` field).
- **Metadata** for any act by id (title, dates, type, status, `htmlUrl`) —
  `GET /framework/{id}`.
- **Full text** of any act as clean plain text — fetches the static HTML with the
  same-site `Referer` the host's hotlink guard requires, decodes the MS-Word legacy-codepage export
  correctly (the declared charset varies per document — windows-1251, -1252
  and -1254 all occur, so it is read from each document rather than assumed), and strips it to text. Raw HTML is also
  available from the Python client (`as_text=False`) and the CLI (`--html`).
- **Taxonomy** — act-type tree and top-level sections, whose ids are the values
  the type filter accepts.

The search endpoint is `GET /getDetailSearch`; the parameter set (`name`,
`title`, `text`, `exact`, `statusId`, `array`, `start`, `length`, …) was
determined by observing the official frontend's own requests. The separate
`e-qanun.ai` portal is Cloudflare-challenged with no public API and is **not** an
integration target.

## Scope — what the corpus actually contains

e-qanun.az holds **~59,500 documents** (59,482 by the sum of the `/all-type`
top-level counts, checked 2026-07-30), split across four sections: Normativ
hüquqi aktlar, Normativ xarakterli aktlar, Qeyri-normativ hüquqi aktlar, Digər.

The corpus is overwhelmingly legislative and executive material. The largest
categories are presidential orders (Sərəncamlar, 16,786), Cabinet decisions
(9,593), presidential decrees (Fərmanlar, 9,495), laws (7,148, including 27
codes), normative acts of central executive bodies (4,099), Central Election
Commission acts (3,894), Cabinet orders (3,665), Milli Məclis decisions (1,853),
local self-government acts (1,522), financial-regulator decisions (451),
constitutional laws (55) and the Constitution and referendum acts (9) — plus a
long tail of smaller categories (Tariff Council 170, local executive authorities
75, Judicial-Legal Council 36, international treaties 4, UN Security Council acts
4, and others).

It also carries a set of judicial documents **as act types in the same id
space**, so they are retrievable through the ordinary act chain and can be
isolated with the type filter:

| Act type | id | Count |
|---|---|---|
| Constitutional Court decisions | 73 | 464 |
| Supreme Court Plenum decisions | 87 | 114 |
| ECtHR judgments concerning Azerbaijan | 89 | 25 |

That is 603 documents, about 1.0% of the corpus. Confirm the current ids with
`list_types` — they are the values `types` / `--types` / `array` accept.

**This is not a case-law database.** There is no ordinary, appellate or cassation
court coverage, no docket / party / judge search, no citator or citation graph,
and no headnotes. What you get is the officially published text of those specific
decision types, retrieved the same way any other act is.

**In-force status** is reported from the publisher's own fields: `statusName`
(Qüvvədədir = in force, Ləğv olunmuşdur = repealed — that exact string is
what the API returns) plus the `statusId` search
filter, with `acceptDate` and `effectDate` where present. `effectDate` is often
null — notably on court decisions. The client surfaces these fields as published;
it performs no independent verification, reconstructs no amendment history, and
offers no point-in-time ("in force as of date X") retrieval. The full text
returned is whatever consolidated HTML the site currently serves, with no version
stamp.

## Requirements

Python 3.9+ and **no third-party dependencies** — the client, the CLI, and the
MCP server are all pure standard library. (The MCP protocol is implemented
directly in stdlib, so you do **not** need the `mcp` SDK or Python 3.10+.)

## Get it

```bash
git clone https://github.com/beerbottle90/arthurlegal-mcp
cd arthurlegal-mcp/eqanun-api
```

There is no `pip install` — run everything from the repo root. On Windows use
`python` or `py -3` wherever these examples say `python3`.

## Quick start — Python

```python
from eqanun import EqanunClient

c = EqanunClient()

hits = c.search("mülki məcəllə", scope="title", status="in_force", length=5)
print(hits["total"], "results")     # -> ~615 (live count; grows as acts are published)

act = c.get_act(46944)                       # Civil Code metadata
print(act["title"], act["statusName"])       # Azərbaycan Respublikasının Mülki Məcəlləsi Qüvvədədir

text = c.get_act_fulltext(46944)             # ~2M chars of clean text
html = c.get_act_fulltext(46944, as_text=False)   # raw MS-Word HTML, if you need the markup

# Restrict a search to Constitutional Court decisions (type id 73):
decisions = c.search("mülkiyyət", scope="text", status="all", types=[73], length=10)
```

`scope` is `"title"` or `"text"`; `status` is `"in_force"`, `"cancelled"`, or
`"all"`; `types` is a list of act-type ids from `list_types()` (omit for no type
filter).

## Quick start — CLI (no install, no deps)

```bash
python3 -m eqanun search "mülki məcəllə" --scope title --status in_force -n 10
python3 -m eqanun search "mülkiyyət" --scope text --status all --types 73 -n 10
python3 -m eqanun count "əmək məcəlləsi"
python3 -m eqanun get 46944
python3 -m eqanun fulltext 46944 --out civil-code.txt
python3 -m eqanun fulltext 46944 --html --out civil-code.html
python3 -m eqanun types
python3 -m eqanun sections
```

`count` has no type filter — the upstream count endpoint ignores it. For a
type-filtered count, run `search --types … -n 1` and read `total`.

## MCP server (no auth, no dependencies)

MCP (JSON-RPC 2.0) is implemented directly in the standard library
([`eqanun/mcp_server.py`](eqanun/mcp_server.py)) — it runs on stock Python 3.9
with **no `mcp` SDK and no pip install**. Exposes `search_acts` (with the `types`
filter), `count_acts`, `get_act`, `get_act_fulltext` (paginated by characters —
`offset` / `max_chars`, default 20,000, with `total_chars` and `next_offset` in
every response), `list_types`, `list_sections`.

Full text is cached for up to 8 acts (LRU) for the process lifetime, so paging
through a long act costs one upstream fetch rather than one per page. The MCP
`get_act_fulltext` tool always returns plain text; the raw-HTML option exists
only in the Python client and the CLI.

**No authentication.** The upstream e-qanun.az API is public and this server adds
no auth of its own — connect it straight from a client UI ("No authentication").

Local stdio — the usual desktop MCP client config:

```json
{
  "mcpServers": {
    "eqanun": {
      "command": "python3",
      "args": ["/absolute/path/to/eqanun-api/server.py"]
    }
  }
}
```

Or run it directly: `python3 server.py`.

Streamable HTTP, bound to loopback by default:

```bash
python3 server.py --transport http --port 8000
#   -> http://127.0.0.1:8000/mcp
```

Env fallbacks: `EQANUN_MCP_TRANSPORT`, `EQANUN_MCP_HOST`, `EQANUN_MCP_PORT`,
`EQANUN_USER_AGENT`. `EQANUN_TIMEOUT` (seconds per request, default 12) and
`EQANUN_RETRIES` (default 1) bound how long a dead upstream takes to be reported.

> Exposure note: the HTTP transport has **no authentication**, and it issues an
> `Mcp-Session-Id` on `initialize` without enforcing it on later requests. It
> binds `127.0.0.1` by default; `--host 0.0.0.0` is an explicit opt-in that lets
> anyone who can reach the port call the tools. Put it behind a reverse proxy
> with your own access control if it needs to be reachable from the internet.
>
> Browser requests are rejected by default: a request carrying an `Origin` header
> gets 403 unless that origin is listed in `EQANUN_MCP_ALLOWED_ORIGINS`
> (comma-separated, or `*` to allow all). This is the MCP spec's DNS-rebinding
> protection — without it, any web page you visit could drive a loopback-bound
> server through your browser. Non-browser MCP clients send no `Origin` and are
> unaffected. Request bodies are capped at 1 MB.

To connect from a cloud UI (Claude connectors / Copilot Studio custom MCP
connector) the server must be reachable from the internet — a cloud UI cannot
reach `localhost`. Use your own reverse proxy or tunnel, or the convenience
scripts below, then paste the `/mcp` URL into the connector and select **No
authentication**.

`run-public.sh` (macOS/Linux) and `run-public.ps1` (Windows) do this in one
command. They use a `cloudflared` already on your PATH if there is one. If there
is not, they will **download and run a third-party binary**, and they refuse to
do that unless you opt in with `EQANUN_ALLOW_DOWNLOAD=1` — because Cloudflare
publishes no checksum file for its releases, so an automatic download **cannot be
integrity-verified**. Installing cloudflared through your package manager
(`brew install cloudflared`, `winget install Cloudflare.cloudflared`,
[pkg.cloudflare.com](https://pkg.cloudflare.com/)) is the safer path, and then
the scripts just use it.

> While a tunnel is up, anyone with the URL can call this unauthenticated server,
> which means they can make **your** IP pull multi-megabyte documents from the
> Ministry of Justice host in your name. Stop the script when you are done.

For Copilot Studio / Power Platform there are ready-made connector specs and a
step-by-step runbook in [`copilot-studio/`](copilot-studio/) — an MCP connector
(`x-ms-agentic-protocol: mcp-streamable-1.0`) and a hosting-free direct REST
connector over `api.e-qanun.az`. The MCP connector ships with a placeholder host
(`REPLACE-ME.example.com`); set it to your own deployment URL before importing.
Note that the direct REST connector talks to the JSON API only and therefore
**cannot return act full text** — full text requires this server (or the Python
client), because it comes from the header-guarded static host.

Verify the server locally with a raw JSON-RPC call:

```bash
python3 server.py --transport http --host 127.0.0.1 --port 8000 &
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Load on e-qanun.az

Every request goes through one gate (`eqanun/_gate.py`), shared by all clients in
a process:

- **At most 30 calls a minute for all machines together.** With two machines that is
  one call every 4 s on each, one in flight at a time. `EQANUN_DAKIKA_AZAMI` can lower
  the budget but not raise it above 30.
- **No long queues.** A request that would wait more than 20 s is refused before it
  is sent (`EQANUN_KUYRUK_SN`).
- **Backing off, not knocking.**
  - 429 or 503 pauses all requests (Retry-After is honoured; the pause doubles up to
    15 minutes).
  - 401 or 403 pauses them for an hour.
  - Three timeouts or server errors in a row pause them for a minute, doubling up to
    15 minutes.
  - A timeout is never retried.
- **A daily cap** of 3,000 calls for all machines (`EQANUN_GUNLUK_AZAMI`).
- **A cache** in front of the gate: the same question within 6 hours costs the server
  nothing (32 MB, `EQANUN_ONBELLEK_MB`).

`server_status` reports the gate and the cache, and probes e-qanun.az only when the
gate is open. The machine count comes from `EQANUN_MAKINE_SAYISI`, else
`TKGM_MAKINE_SAYISI`, else 2 on Fly and 1 elsewhere.

## Verify

Offline unit tests — no network, safe to run in CI:

```bash
python3 -m unittest discover -s tests -v
```

Live end-to-end check against the real API (opt-in, because it fetches from the
official host):

```bash
python3 examples/smoke_test.py
```

Exercises search → count → get_act → get_act_fulltext → taxonomy and prints
PASS/FAIL.

## Layout

```text
.
├── README.md
├── LICENSE                 # MIT
├── requirements.txt
├── eqanun/                 # dependency-free core (Python 3.9+)
│   ├── __init__.py
│   ├── client.py           # EqanunClient: search, get_act, get_act_fulltext, ...
│   ├── _html.py            # charset-aware HTML → clean text
│   ├── mcp_server.py       # MCP (stdio + Streamable HTTP), pure stdlib, no auth
│   └── __main__.py         # `python3 -m eqanun` CLI
├── server.py               # MCP server entry point (python3 server.py [--transport http])
├── run-public.sh           # one-command: expose MCP over a free HTTPS tunnel (macOS/Linux)
├── run-public.ps1          # same, for Windows PowerShell
├── copilot-studio/         # Power Platform / Copilot Studio custom connectors
│   ├── mcp-connector.swagger.json   # MCP connector (x-ms-agentic-protocol)
│   ├── rest-connector.swagger.json  # direct REST connector (no hosting)
│   └── RUNBOOK.md                   # import + no-auth setup steps
├── tests/
│   └── test_offline.py     # unit tests, no network
└── examples/
    └── smoke_test.py       # live end-to-end check
```

## Governance / legal note

- e-qanun.az is the official Ministry of Justice database and its content is
  public legal acts. Read-only metadata and full-text retrieval for legal
  research is low-risk. If you intend to run this at scale or in production,
  prefer an official arrangement (a Swagger endpoint exists but is
  IP/referer-gated — request access and terms from justice.gov.az), and review
  [e-qanun.az](https://e-qanun.az/)'s own terms of use for yourself.
- Be a polite client: acts change rarely, so cache aggressively and keep a low
  crawl rate. This client identifies itself as `eqanun-api/<version>` rather than
  impersonating a browser; set `EQANUN_USER_AGENT` to name your own deployment
  and give the operator a way to reach you.
- **On the `Referer` header, plainly:** the static full-text host runs a hotlink
  guard, and this client sends a same-site `Referer` that steps past it. That is
  a deliberate choice, not an accident of implementation. The reasoning: the
  content is public law, no authentication or paywall is being defeated, the
  client identifies itself honestly and fetches single acts at a low rate, and
  the project ships no bulk harvester. If the operator objects, stop.
- Azerbaijani legislation via e-qanun is a **primary** source: cite with the
  status (`statusName`) and dates as returned by the API — remembering that these
  are the publisher's own labels and that `effectDate` is frequently absent.
- Always link or quote from the canonical `htmlUrl` returned by
  `GET /framework/{id}` rather than constructing URLs yourself.
- **No warranty, and nothing here is legal advice.** The software is provided
  as-is under the [MIT License](LICENSE); the risk assessment above is not a
  substitute for your own review of the source's terms.

## License

[MIT](LICENSE) — this covers the software in this repository only.

The legal acts it retrieves are published by the Ministry of Justice of the
Republic of Azerbaijan on e-qanun.az. They are subject to that publisher's own
terms; this project neither owns nor relicenses them, and redistributing the
corpus is out of scope (see the governance note above).
