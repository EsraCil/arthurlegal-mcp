# ArthurLegal MCP: privacy policy

Effective: 29 October 2026

This policy covers the hosted ArthurLegal MCP endpoints:

- `https://arthurlegal-mcp.fly.dev/directory/mcp` (the profile listed in the Claude connector
  directory), and
- `https://arthurlegal-mcp.fly.dev/mcp` (the full endpoint used by the ArthurLegal packages).

The service is operated by DATS Web3 Solutions, Inc. Contact details are at the end.

## In short

- There is no account, no sign-in and no cookie.
- The service receives only what Claude (or another MCP client) sends when it calls a tool:
  the tool's name and its arguments, such as search words, a statute number or a document
  identifier. It does not receive your conversation, your files or your Claude account details.
- It uses those arguments to query official legal sources and returns the result. Except for the
  German tools described below, it writes no request logs and does not store your search text
  after answering. To avoid repeating requests to official sources, it keeps recently fetched
  public documents in memory under their official identifiers (such as a law and article number)
  until they expire or the server restarts.
- Do not put client names or other confidential facts into a request. Mask documents on your
  own computer first (for example with [Arthur Mask](https://github.com/beerbottle90/arthur-mask)).

## What the service receives

When a tool is called, the service receives the JSON-RPC request: the tool name and the
arguments Claude chose for it. Claude decides what to put in those arguments based on your
conversation, so whatever you ask Claude to search for can reach the service as search text.

## How it is used

1. **To query the official source the tool is for.** The service sends that source only the
   parameters it needs (search words, identifiers, dates), from the service's server, not from
   your device. The sources are listed below.
2. **To rank results.** Ranking runs in the server's memory. When semantic search or semantic
   ranking runs, the search text is sent to Voyage AI to compute an embedding (a list of numbers
   that represents its meaning). Voyage AI handles it under its own
   [privacy policy](https://www.voyageai.com/privacy).
3. **Nothing else.** No profiling, no advertising, no sale or sharing of request data, and no
   use of it to train any model.

Public documents fetched from official sources may be added to the service's own index of
public legal documents so that later searches are faster. Nothing you send is added to it.

## Interactive views

In Claude, some results are shown as interactive views (an article card, a decision list and
the ArthurLegal desk). The views are part of the service's response and run inside Claude, in
a sandbox. To start, each view loads one pinned open-source library (the MCP Apps client) from
the public CDN unpkg.com, so that CDN sees your device's connection data (such as its IP address)
under its own terms. A view sends nothing to the service on its own: it calls the same tools
Claude does, through Claude, and only when you act in it. A view keeps no data after it is
closed. Links in a view to official sources (such as mevzuat.gov.tr) open only when you click
them.

## What is stored, and for how long

| Data | Stored? |
| --- | --- |
| Free-text search words | Not stored by the service, except by the German tools (next row). |
| German tools (`de_`) | The German backend caches official responses on the server's disk, keyed by the request address and its parameters (which include the search words): up to 1 hour for searches, 24 hours for single documents, 7 days for reference lists. It also keeps an audit line per call: time, tool name, a SHA-256 hash of the input (not the text), result size and status. Both sit on the server's temporary disk; the service uses no persistent storage, so they are erased when the server is redeployed. |
| Official identifiers of fetched public documents (law and article numbers, decision ids) | Kept in memory as cache keys, with the public document, until the entry expires or the server restarts; not written to disk or to logs (German tools: previous row). |
| Parcel identifiers and parcel data (land-registry tools, `/mcp` only) | Kept in memory for up to 24 hours, to avoid repeat requests to TKGM and to enforce its rate limits; never written to disk or to logs. |
| Request logs | The service writes none, apart from the German tools' audit line (see that row). Its host, Fly.io (primary region: Amsterdam, the Netherlands), processes connection data such as IP addresses and timestamps to run its network, under the [Fly.io Privacy Statement](https://fly.io/legal/privacy-policy). |
| Index of public legal documents | Kept and refreshed; contains official publications only. |

## Who else receives data

| Recipient | What it receives | Why |
| --- | --- | --- |
| Official legal sources | Search words and identifiers for that source | To answer the tool call |
| Voyage AI | Search text, when semantic search or ranking runs | To compute embeddings |
| Fly.io | Connection data (IP address, time) | Hosting |
| unpkg.com | Your device's connection data, when an interactive view opens | To load the MCP Apps client library |

The official sources are: for Türkiye, the Ministry of Justice's court-decision and legislation
services, the Constitutional Court, the Court of Jurisdictional Disputes, the Official Gazette and
eight regulators (Competition Authority, EPDK, SPK, BDDK, KVKK, BTK, GİB, Insurance Arbitration
Commission); Rechtspraak and KOOP (Netherlands); the Sejm ELI API (Poland); RIS (Austria); the
Irish Statute Book; Finlex (Finland); the BOE (Spain); legislation.gov.uk (United Kingdom); the
EU Publications Office (CELLAR); e-Gov 法令API (Japan); e-qanun.az (Azerbaijan); NeuRIS,
rechtsprechung-im-internet.de, Open Legal Data and the Bundestag DIP (Germany); GLEIF (legal
entity identifiers); open-access scholarship indexes, with Crossref and Unpaywall for DOIs (the
`scholar_list_sources` tool lists every index); and ResourceContracts.org.

Only on `/mcp`, not on `/directory/mcp`: the land-registry (`tkgm_`) tools send parcel
identifiers or coordinates to TKGM Parsel Sorgu, and place names to OpenStreetMap Nominatim.

## Children

The service is a research tool for legal professionals and is not directed at children.

## Your choices

You can stop using the service at any time by removing the connector in Claude's settings.
Because the service keeps no request data, there is normally nothing to access, correct or
delete. If you think it holds data about you, contact us and we will check and answer.

## Changes

Changes to this policy are published in this file; its history on GitHub shows every change and
its date.

## Contact

ertug@datsproject.io · [GitHub issues](https://github.com/beerbottle90/arthurlegal-mcp/issues)
