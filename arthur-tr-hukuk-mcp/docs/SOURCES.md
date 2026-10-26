# Sources — what each adapter talks to, and what it cannot do

Verified live on 2026-09-05 unless stated. "Live" = queried upstream at tool
time; "Local" = crawled into `data/index.db` for `semantik_ara`.

| key | Kurum | Upstream | Method | Live | Local | Citation shape |
|---|---|---|---|---|---|---|
| `ictihat` | Yargıtay, Danıştay, BAM, yerel hukuk, KYB | `bedesten.adalet.gov.tr/emsal-karar` | JSON POST, base64 HTML/PDF | ✅ | — | `Yargıtay 9. Hukuk Dairesi, E. 2023/1, K. 2024/2, 01.02.2024` |
| `aym` | Anayasa Mahkemesi (norm + bireysel) | `*kararlarbilgibankasi.anayasa.gov.tr/api/core/public/search` | JSON POST | ✅ | — | `AYM, E. 2007/63, K. 2009/152, 2009-11-05 (RG 2010-04-15/27553)` |
| `uyusmazlik` | Uyuşmazlık Mahkemesi | `kararlar.uyusmazlik.gov.tr` | ASP.NET postback, PDF | ✅ | — | `Uyuşmazlık Mahkemesi, E. 2026/348, K. 2026/356, 15/06/2026` |
| `mevzuat` | 12 mevzuat türü, madde ağacı, gerekçe | `bedesten.adalet.gov.tr/mevzuat` | JSON POST | ✅ | — | `<Ad> (Kanun No. 6446, RG 30.03.2013/28603)` |
| `resmi_gazete` | Resmî Gazete fihrist | `resmigazete.gov.tr/eskiler/yyyy/mm/yyyymmdd.htm` | HTML (cp1254) | ✅ | — | `RG 05.09.2026, S. 33361` |
| `rekabet` | Rekabet Kurulu | `rekabet.gov.tr/tr/Kararlar`, `/Karar?kararId=` → PDF | HTML list + PDF | ✅ | ✅ | `Rekabet Kurulu, 27.11.2025 tarih ve 25-44/1086-615 sayılı karar` |
| `epdk` | EPDK Kurul kararları | `/Detay/Icerik/3-0-39-3/son-kurul-kararlari/elektrik` (+dogalgaz, petrol, lpg, denetim) → `POST /Detay/GetFastAccessList {fId}` → `/Detay/DownloadDocument?id=` | HTML tree + JSON + PDF/DOCX | ✅ | ✅ | `EPDK, 18.06.2026 tarihli ve 14681 sayılı Kurul Kararı (RG 20.06.2026/33286)` |
| `spk` | SPK haftalık bülten | `/spk-bultenleri/<yyyy>-yili-spk-bultenleri?s=N` → `spk.gov.tr/data/<id>/<yyyy>-<n>.pdf` | HTML list + PDF | ✅ | ✅ | `SPK Bülteni 2026/27, A. DUYURU VE İLKE KARARLARI` |
| `bddk` | BDDK Kurul kararları | `/Mevzuat/Liste/55` (RG'de yayımlanan), `/56` (yayımlanmayan) → `/Mevzuat/DokumanGetir/<id>` | HTML list + PDF | ✅ (title) | ✅ | `BDDK, 06.08.2026 tarih ve 11548 sayılı Kurul Kararı` |
| `kvkk` | KVK Kurulu karar özetleri | `/Icerik/5406/kurul-karar-ozetleri?page=N` (36 pages) → `/Icerik/<id>/<yyyy>-<no>` | HTML | ✅ (recent pages) | ✅ | `KVK Kurulu, 08/08/2024 tarih ve 2024/1361 sayılı karar özeti` |
| `btk` | BTK Kurul kararları | `btk.tr/api/content/board-decisions` | JSON GET + PDF | ✅ | ✅ | `BTK, 03.08.2026 tarih ve 2026/İK-THD/186 sayılı Kurul Kararı` |
| `gib` | GİB özelgeleri | `gib.gov.tr/api/gibportal/mevzuat/ozelge/list` | JSON POST (HTML inline) | ✅ | — | `GİB, 31.12.2025 tarih ve 62030549-125[6-2024]-1742807 sayılı özelge` |
| `sigorta_tahkim` | Sigorta Tahkim Komisyonu | `sigortatahkim.org/content/CmsFiles/karardrgs<N>.pdf` (1–66) | PDF, regex split | ✅ (per issue) | ✅ | `Sigorta Tahkim Komisyonu, 15.06.2026 Tarih ve K-2026/343230 Sayılı Hakem Kararı (Hakem Karar Dergisi S. 66)` |

## Details worth knowing

**The local index is not equally deep.** Measured 2026-09-20: 13,313 of 19,498 documents (BDDK, BTK, Rekabet) were
indexed from their listings, so their body was the title and `semantik_ara` searched titles, not decisions.
`crawl.py --backfill-text` added full text for BDDK (964/964) and BTK (1,897/1,904; seven are scanned PDFs).
"idari para cezası" occurs in the text of 419 BTK decisions and in the title of only 9 — for the other 411 the question
could not be asked before, because BTK's live search is title-only too. (Counted with Turkish-aware case folding:
SQLite's `lower()` leaves "İ" alone and reports no title at all.) EPDK has text for about half of its decisions (the
rest have no extractable document); KVKK, SPK and Sigorta Tahkim always had full text.

**Rekabet has full text since 2026-09-30.** It was left title-only at first (its live search already matches inside the
PDFs, and ten thousand long PDFs bloat the baked image). But the live search is word-based (an unquoted multi-word query
is not a phrase search, see `sources/rekabet.py`), and questions about the reasoning need ranked retrieval. The index now
carries the text of 10,433 of 10,445 decisions (twelve PDFs yield no text and keep their title;
`--backfill-text --source rekabet`, 7.1 h at one request per second). The baked index grew from 284 MB to 1,353 MB.
Measured on a stratified sample of 101 decisions (period × decision type):

| query | top 10, title-only | top 10, full text |
|---|---:|---:|
| six-word phrase from the reasoning | 2 | 95 (first: 79) |
| three rare words from the reasoning | 0 | 91 |
| decision number | 101 (first: 94) | 101 (first: 96) |
| first six words of the title | 96 (first: 79) | 92 (first: 62) |

The last row is the price: other decisions now name the same undertakings. Six of the nine misses are generic openings
("Rekabet Kurulunun … tarihli … sayılı kararı uyarınca") that mostly missed before too. Rekabet vectors are still title
vectors, restored after the backfill (the workstation has no Voyage key).

Quoted phrases are searched in both ı/i spellings of each word. The dissent heading "KARŞI OY GEREKÇESİ" folds to
"karsi" under unicode61, running text to "karsı": "karşı oy gerekçesi" matched 52 decisions as typed and 733 with the
heading. Spanish and Dutch indexes return the same counts as before.

**The shipped index is packed** (`crawl.py --pack`). SQLite stores a row's columns in order and `body` sits in the middle
of the schema, so reading `subject` or `date` walks the row's whole overflow chain. With Rekabet's text in place a
kurum-filtered search read twice the data (median 170 MB instead of 82) and `status` six times as much (378 MB, 302 ms).
Packing rebuilds `docs` with `body` last (same columns, rows, ids and vectors, checked by fingerprint) and brings both
back to the title-only level or below (82 MB; 44 ms). The code addresses columns by name, and so does FTS5's
external-content read: only the byte layout changes.

**Bedesten** (ictihat + mevzuat). One IP gets ~10 requests per 30 s; the shared
token bucket spaces requests 3.5 s apart and honours `Retry-After`. Result
rows carry no text — `ictihat_getir` per decision. Search syntax: bare words
AND, `"phrase"`, `+must`, `-not`, `AND/OR/NOT`, no wildcards. Mevzuat search
defaults to the title; `search_in="fulltext"` sends `phrase` + `basliktaAra=false`.
Mevzuat page size is capped at **20** by the API (`Kayıt sayısı 20'den fazla olamaz`, HTTP 400 —
verified 2026-09-20; the schema used to say 50). The same holds for case law: `kararTarihiStart` without `kararTarihiEnd` is ignored (52,993 vs 612 for
"işe iade" from 2025), so `ictihat_ara` sends both bounds too.
A one-sided RG date range (`resmiGazeteTarihiStart` without `…End`, or the reverse) is silently
ignored upstream — 917 laws instead of 6 — so the adapter always sends both bounds.
Without a query the API lists by type and RG date
range, which is what `mevzuat_ara(konu=…)` scans.
Date filters are converted to the UTC boundaries Turkey's UTC+3 implies.

**EPDK**. No search API; the portal search needs reCAPTCHA v3. The Kurul
kararları trees (67 categories for elektrik, 18–19 for the others, 3 for
denetim) are fetched category by category and cached for six hours. Documents
come as PDF or DOCX; DOCX is unpacked from `word/document.xml` without a
library. The old guesses `3-0-0-94` / `3-0-0-122` in earlier ArthurLegal notes
are *yönetmelikler* and *YEKDEM* pages, not decisions.

**SPK**. Decisions are only in the weekly bülten PDF. Sections split on
`A. …`, `B. …` headings; the crawler stores one document per section with the
bülten date parsed from the cover.

**KVKK**. Listing pages are server-rendered with `page=N`; each card links
`/Icerik/<id>/<yyyy>-<no>`. Live search scans the newest pages only; the full
archive is a crawl.

**BDDK**. Both lists are single pages (~1,000 and ~900 links) with the date and
decision number inside the title `(dd.mm.yyyy - nnnn) …`. Live search is on
titles; `--fetch-text` crawls the PDFs.

**Sigorta Tahkim**. Issue 4 is `karardergisisayi4.pdf`, 57–61 are
`revizekd<N>.pdf`, the rest `karardrgs<N>.pdf`. Decisions start with
`dd.mm.yyyy Tarih ve K-yyyy/n Sayılı (İtiraz) Hakem (Heyeti) Kararı`.

## Removed on 2026-09-06 (official endpoint does not answer reliably)

| Kurum | What happened | Evidence |
|---|---|---|
| KİK (EKAP v2) | signed JSON API answers HTTP 500 with both the 2026-05 and 2026-09 header schemes | `POST /b_ihalearaclari/api/KurulKararlari/GetKurulKararlari` → 500 "Sunucu hatası oluştu" |
| Sayıştay | WAF answers 418 to every DataTables POST, from Fly and from a workstation | `POST /KararlarTemyiz/DataTablesList` → 418 |
| TÜRKPATENT | no decision database; research portal needs a reCAPTCHA v3 token per call | `/yidk-kararlari` 404 shell, `/api/research` 500 |
| İSTAÇ | `istac.org.tr` does not resolve (Fly, workstation) and does not answer by IP | `getaddrinfo failed` / connect timeout |

The adapters live in git history (`git show db9a7ea^:ArthurLegalTR/sources/<name>.py`). Re-adding one requires a
live search **and** fetch to pass from the deployed machine, not from a browser.

## Not included (and why)

- Reklam Kurulu (Ticaret Bakanlığı): site returned 500 on every probe.
- KGK (Kamu Gözetimi Kurumu): decision list is loaded client-side; not reverse-engineered yet.
- SEDDK: no public decision listing found.
- EPDK licence registries (`apigateway.epdk.gov.tr/*LisansiSorgula`): Swagger-documented, useful, but a
  registry rather than decisions — a natural next adapter.
