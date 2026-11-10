# Attribution

arthur-tr-hukuk-mcp (formerly ArthurLegalTR) stands on work by others. What was taken, from where, and what
was done with it:

## Said Sürücü — `saidsurucu/yargi-mcp`, `saidsurucu/mevzuat-mcp` (MIT)

The upstream *knowledge* in this server — which endpoint each Turkish court
and regulator actually answers on, what headers Bedesten expects, the 79
chamber codes, the GİB özelge body shape, the
Sigorta Tahkim journal file-name rules, the AYM Kararlar Bilgi Bankası
envelope — was established in those two repositories and is reused here.

What is *not* reused: the FastMCP/httpx/pydantic/markitdown stack, the OAuth
and billing layers, and the paid search keys (Brave for KVKK, Tavily for BDDK
and Sigorta Tahkim, OpenRouter for embeddings). Every adapter in `sources/`
is a fresh standard-library implementation; where it follows the reference
closely the module docstring says so.

The MIT licence text of both projects is reproduced below as required.

```
MIT License

Copyright (c) 2025 saidsurucu

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Related projects by the same author that were surveyed but not reused:
`ihale-mcp` (EKAP tenders — out of scope), `markapatent-mcp` (TÜRKPATENT via
a paid CAPTCHA solver — deliberately not reproduced), `yargi-cli`,
`mevzuat-cli`, `yargi-pro-gemma-local` (a local-LLM launcher, not a data
source).

## `beerbottle90/arthurlegal-mcp`, `beerbottle90/nl-rechtspraak-mcp` (MIT)

`mcpcore.py` (dependency-free MCP JSON-RPC server, stdio + Streamable HTTP) and
`retrieval.py` (SQLite FTS5 + trigram + vector hybrid retrieval with RRF) are
vendored from the ArthurLegal jurisdiction servers. `retrieval.py` gained
dotless-ı query expansion (`_dotless_variants`) here; this copy is now the canonical
one, and ten other arthurlegal-mcp backends carry it unchanged.

## Data sources

All content is retrieved live from, or indexed from, official Turkish public
sources: Adalet Bakanlığı (Bedesten / mevzuat.adalet.gov.tr), Anayasa
Mahkemesi, Uyuşmazlık Mahkemesi, Resmî Gazete, Rekabet Kurumu, EPDK, SPK,
BDDK, KVKK, BTK, GİB, Sigorta Tahkim Komisyonu.
Their terms of use apply. Nothing here is legal advice.
