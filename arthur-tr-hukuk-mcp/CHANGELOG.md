# Changelog

## Yayımlanmamış

- `mevzuat_madde_getir` (F-196): TBK m.428 getirilemiyordu; Bedesten metninde başlık "MADDE 428 İşyerinin…" diye
  tiresiz. Tiresiz başlık artık yalnız güvenli durumda kabul edilir: satır başında büyük harfli MADDE, ardından
  büyük harf ya da "(", numara iki komşu başlığın arasında (komşu yoksa metnin ilk satırı). "Madde 12 uyarınca…"
  başlık sayılmaz. TBK m.464'te tireden önce yumuşak tire (U+00AD) vardı; o da tanınır. Tam metin modunda m.427
  artık 428'in metnini almaz; tam metinde başlığı bulunamayan madde maddenin kendi düğümünden denenir. Fıkra
  araması "(8)" yanında "8." biçimini de tanır (İYUK m.45/8): yalnız maddede "(1)" biçimi yoksa ve gövde doğrudan
  "1." ile başlıyorsa; "Mücbir sebepler: 1. …" gibi bent listeleri fıkra sayılmaz. 12 kanunun tam metninde
  (5.985 başlık) eklenen başlık yalnız TBK 428 ve 464, kaybolan yok.
- EPDK: 3.745 kararın 2.019'unun gövdesi yalnız liste kaydıydı (konu + kategori). 2016 öncesinin ~%97'si böyleydi;
  belgeler eski Word (.doc) ya da taranmış PDF olduğu için metin çıkmıyordu. 2.007'sine metin eklendi: 1.722 Word
  dosyası (`textx.doc_to_text`, olefile), 281 taranmış belge OCR'la (`ocr.py`: pypdfium2 + Tesseract 5, Türkçe
  tessdata_best, 300 dpi; 11'i Word'e resim olarak konmuş sayfa), 4 arşiv. Kalan 12: elektronik tablo, portal sayfası,
  bir RTF, bir indirilemeyen belge. Okuma biçimi `meta.metin`'de. `crawl.py --backfill-text --source epdk --ocr`
  aynı işi yapar. Canlı belgeden metin çıkmazsa `kurum_karari_getir` metni indeksten verir (sunucuda olefile ve
  OCR yok); taranmış belgede OCR uyarısıyla. Portalın kendi sayfası (menü) artık karar metni sayılmaz. Ölçüm:
  bağımsız 16 soruluk set (Sonnet yazdı, soruları sonuçları görmeden, metindeki içerikten): hybrid ilk 10 %12 -> %81,
  ilk sıra %0 -> %38, MRR 0,02 -> 0,56. Bedel: ilk ölçüm setinin 12 anlam sorusundan 3'ünde hedef geriledi (ilk 10
  %92 -> %83): aynı konudaki eski kararlar artık metinleriyle bulunuyor; ikisinde öne geçenler aynı kuralın önceki
  sürümleri. İkinci set değişmedi. Veri değişikliği: dağıtıma giden dizinde.
- Tarihler: kurum çözümleyicileri yalnız iki haneli gün ve ayı tanıyordu; "28.6.2005" boş kalıyordu. 11.10.2026'da
  tarihi boş olan: Rekabet'te 10.445 kararın 8.529'u, Sigorta Tahkim'de 77, BDDK'da 17 karar. Tarih süzgeci onları
  görmüyordu: `date_from` verilince düşüyor, yalnız `date_to` verilince yılına bakılmadan giriyordu. Ortak
  `textx.iso_tarih` tek haneyi ve eğik çizgiyi tanır; BDDK başlığında numaradan sonra boşluk ve eğik çizgili tarih
  de çözülür. Rekabet'in liste sayfası 41 kararda kararın kendi "Karar Tarihi" satırından farklı tarih yazıyor
  (biri 17.8.2029; karar 17.08.2023). `rekabet.karar_tarihi` karar sayısının yılıyla (23-39/… → 2023) denetler.
  37 atıf karar metnindeki tarihle yeniden yazıldı, liste tarihi `meta.liste_tarihi`'nde. `crawl.py --tarih-onar`
  mevcut indeksi vektörlere dokunmadan onarır. Ölçüm (107 kararlık örnek, başlıkla arama, yıl aralığı süzgeci):
  süzgeçle bulunan %0 → %100, üst sınırın dışındayken yanlışlıkla gelen %100 → %0; iki ölçüm setinin 120 sonuç
  listesi birebir aynı. Tarihsiz kalan: EPDK 8, KVKK 1 (kaynakta tarih yok). Veri değişikliği: dağıtıma giden dizinde.
- `retrieval.py`: `SEMANTIC_RESIDENT_MB` ile vektörler bellekte float16 tutulur (bütün indeksler için ortak bütçe;
  yoksa eskisi gibi SQLite'tan okunur). Sorgu başına 4 KB'lık vektör okumak taramanın çoğuydu. v21 indekslerinde,
  iki ölçüm setinin 480 sonuç listesinin 474'ü birebir aynı (kalan 6'sında ilk 3 aynı, fark puanı eşit alt sıralarda).
  Sunucu tarafı hybrid medyanı (yerel): PL 536 → 204 ms, ES 238 → 100, NL 144 → 57, TR 395 → 292. Altı indeks
  ~141 MB. Başka bir bağlantı yazınca (`PRAGMA data_version`) ya da bu bağlantı vektör yazınca kopya yenilenir.
  Barındırılan sunucuda kapalı: 512 MB'lık makinede 141 MB vektörle boş bellek 77-117 MB'a indi, SQLite dosyaları
  işletim sistemi önbelleğinden düştü ve TR ile PL aramaları ~1,2 sn'den 7-8 sn'ye çıktı (kelime araması dahil).
  Açmak için ≥1 GB bellek.
- KVKK: karar metni yanlış adresten isteniyordu (`/Icerik/<id>/x`). Site bu adresi `/error`'a yönlendiriyor, o sayfanın
  menüsü 291 kararın hepsine gövde olarak yazılmıştı (4.672 karakter, hepsi aynı); `kurum_karari_getir(kurum="kvkk")`
  de karar yerine menüyü döndürüyordu. Adres artık karar numarasını taşır (`/Icerik/8887/2026-1183`; arama sonucundaki
  `id` de böyle). Yalnız numara verilirse adres önce yerel indeksten, sonra son liste sayfalarından bulunur. Metin
  yalnız karar bölümünden (`news__detail-article`) okunur. Karar bölümü olmayan sayfa metin sayılmaz, açık hata döner.
  Taramada başlık kararın konu özeti olur ("… hakkında (KVKK Kurul Kararı 2026/1183)"). Karar tarihi ve atıf
  sayfadan tamamlanır. İstek sınırı saniyede bir.
- Rekabet: vektörler artık başlık + metnin ilk 8.000 karakterinden (öteki kurumlar gibi). 30.09'da tam metin eklenirken
  başlık vektörleri geri konmuştu (saklanan vektör, başlığın tek başına vektörüyle 0,993, başlık + metinle 0,774
  benzerdi). Başlıkta çoğu zaman yalnız taraf adları vardı; içerikle sorulan soru ıskalıyordu. 10.10.2026 ölçüm
  setinde (12 anlam sorusu, Rekabet + EPDK + BTK + BDDK) hybrid ilk 10 %83 -> %92, MRR 0,57 -> 0,61; ilk 50'de bile
  olmayan bir karar 1. sıraya çıktı, bir usul kararı (sözlü savunmaya katılma talebi) 1. sıradan 4.'ye indi; kelime
  soruları değişmedi. Veri değişikliği: dağıtıma giden dizinde.
- `retrieval.py` (bütün arka uçların paylaştığı kopya, artık 11 dizinde birebir aynı; `tests/test_retrieval.py`
  denetler). 09.10.2026 ölçümü (6 yerel indeks, 120 bilinen-belge sorusu): varsayılan `hybrid`, düz cümleyle sorulan sorularda `semantic`ten kötüydü (hedef 1. sırada %19'a karşı %79).
  Sebep: kelime merdiveni bu soruların 72'sinde de "kelimelerden biri" basamağına düşüp 50 gürültü aday getiriyordu.
  Artık kelime kanalı yalnız bütün kelimeler (ya da önekleri) eşleştiğinde sıralamaya girer; aksi hâlde anlam
  sonuçlarının arkasına dolgu olarak eklenir. Anlam kanalı yoksa (anahtar, vektör) eski davranış sürer. Sorgu bir
  belgenin `ref`'i ise (ECLI, BOE kimliği) o belge 1. sıraya konur. Aynı kanal listeleriyle yeniden oynatma: düz cümle
  sorularında 1. sıra %19 → %82, ilk 10 %83 → %94; kelime ve numara sorularında %96 → %100. Yanıtta
  `retrieval.keyword_match`, `ranking`, `exact_ref`.
- Anlam taraması parça parça okur (2.048 vektör) ve numpy kuruluysa onunla puanlar: 16.545 vektörde 1.304 → 217 ms
  (aynı makine; numpy'siz 3.12'de `math.sumprod` ile 682 ms). Sıralama değişmez. Sorgunun embedding çağrısı canlılık
  denetimi yerine geçer: bir dakikalık sessizlikten sonraki her aramada ayrıca "ping" atılmaz; yalnız kelime araması
  hiç ping atmaz.
- Ortak kopyadan alınanlar: sağlayıcının hata mesajı `status`'ta görünür (Voyage 401/403 ayrımı), onarım ipucu
  sağlayıcıya göre, istekte adlandırılmış `User-Agent`.
- İçtihat araması (`ictihat_ara`): Bedesten çıplak kelimeleri VEYA ile birleştiriyor (kaynak notu "VE" diyordu).
  09.10.2026 ölçümü, Yargıtay + Danıştay: "kira tespit davası" tırnaksız 2.514.700 karar (ilk sonuç mera davası),
  her kelime zorunlu (+) 53.830, tırnaklı 2.127 (3. Hukuk Dairesi kira kararları). Tırnak ve işleç içermeyen çok
  kelimeli sorgu artık önce tam ifade, sonuç yoksa her kelime zorunlu, o da yoksa olduğu gibi aranır; ilk sonuç veren
  basamakta durur. Yanıtta `uygulanan_sorgu`, `arama_modu`, `denenenler`. `kelime_modu` (ifade | hepsi | herhangi) ile
  değiştirilir. Her basamak bir Bedesten isteğidir (yerel kova 3,5 sn/istek).
- Rekabet: Kurum'un PdfText araması kelime bazlıdır; tırnaksız çok kelimeli sorgu ifade aramaz ve `total` şişer
  (29.09.2026 ölçümü: 'karşı oy' 2.737, 'farklı gerekçe' 5.222 karar; tırnaklı "karşı oy gerekçesi" 61). Kaynak notu
  ve `query` açıklaması bunu söyler; tırnaksız çok kelimeli sorgunun yanıt notuna DİKKAT satırı eklenir ("UYARI"
  önekini kullanmaz: o, çekilemeyen veriye ayrılmıştır).
- Rekabet: yerel indeks tam metinli (30.09.2026): 10.445 kararın 10.433'ü (`crawl.py --backfill-text --source rekabet`,
  saniyede bir istek, 7,1 saat; metni çıkmayan 12 PDF başlıkla kalır). Tabakalı 101 kararlık örnekte gerekçeden altı
  kelimelik ifade kararını ilk 10'da 95/101 buluyor (önce 2), üç nadir kelime 91/101 (önce 0), karar sayısı 101/101
  (değişmedi). Bedeli: başlığın ilk altı kelimesi 96 yerine 92/101 (birinci sırada 79 yerine 62), çünkü aynı
  teşebbüsleri anan başka kararlar öne geçiyor; dokuz ıskanın altısı "Rekabet Kurulunun … sayılı kararı uyarınca" gibi
  genel açılışlar. Rekabet vektörleri başlık vektörü olarak kaldı. Kaynak notu artık indeksin tam metinli olduğunu
  söyler; `tazele.sh` yeni kararlara metin ekler ve dizini paketler.
- `crawl.py --pack`: dağıtım paketi. `docs` gövde sonda yeniden kurulur (sütunlar, satırlar, id'ler ve vektörler aynı;
  parmak iziyle denetlenir), FTS baştan kurulup birleştirilir, VACUUM. Gövde ortadayken kurum süzgeçli arama her adayın
  bütün metnini okuyordu: Rekabet metniyle okunan veri 82 → 170 MB (medyan), `status` 52 → 302 ms; paketli dizinde
  82 MB ve 44 ms.
- Tırnaklı ifade her kelimenin ı/i yazımıyla aranır (en çok 16 bileşim; fazlasında bütün ifadenin üç yazımı). Büyük
  harfli başlık "KARŞI OY GEREKÇESİ" unicode61'de "karsi", akan metin "karsı" olur: "karşı oy gerekçesi" yazıldığı gibi
  52 kararda, başlıkla birlikte 733 kararda geçiyor. İspanyolca ve Felemenkçe dizinlerde sonuç sayıları değişmedi.

## 0.5.1 — 2026-09-27

- Talimat: çekilemeyen madde ve erişilemeyen kaynak için "doğrulanmadı" işareti yerine açıkça `UYARI: veri çekilemedi, teyidiniz gerekli: <bağlantı>` (madde için https://www.mevzuat.gov.tr/). ArthurLegal paketlerindeki (1.10.1) canlı veri uyarısıyla aynı biçim; bağlantı aracın `source_url`'si ya da resmî giriş sayfasıdır, uydurulmaz.
- Talimata TEYİT BAĞLANTISI listesi: mevzuat, Resmî Gazete, Yargıtay, Danıştay, BAM ve yerel mahkeme, AYM, Uyuşmazlık Mahkemesi ve sekiz kurumun resmî giriş sayfaları.
- Madde getirilemediğinde araç mesajı (`DOGRULANMADI`) ve `ok=false` notu aynı uyarı satırını söyler.

## Ad — 2026-09-23

- Depo ve klasör adı `ArthurLegalTR` → `arthur-tr-hukuk-mcp`: ArthurLegal paket deposuyla karışıyordu.
  İşlev değişmedi, araç adları (`tr_`) aynı; `status`taki `server` alanı ve talimat başlığı yeni adı taşır.

## 0.5.0 — 2026-09-22

SMK m.120 olayı (protokole "ŞİRKET'in önalım hakkı" yazıldı; m.120 çalışanın önalım hakkıdır) üzerine:

- `mevzuat_madde_getir` tek çağrıda madde okur: `number="6769", madde_no="120"` (ya da en çok 10 maddelik liste;
  `geçici 1`, `ek 3`, `169/a`, `12/3`). Her kalem başlık, metin, durum (mülga, iptal, başka kanuna işlenmiş) ve yalnız
  kayıt alanlarından kurulan `citation` taşır: `SINAİ MÜLKİYET KANUNU (Kanun No. 6769, RG 10.01.2017/29944) m. 120`.
  Olmayan madde `NOT_FOUND` döner, komşu madde asla dönmez; aynı numaralı birden çok kayıt `AMBIGUOUS_LAW`.
  Eski `madde_id` kullanımı aynı anahtarlarla çalışır. Ağaçta düğümü olmayan geçici/ek maddeler barındıran düğümden
  ya da tam metinden kesilir; SMK m.166-184 gibi aralık düğümleri çözülür.
- `mevzuat_icinde_ara`: madde başlığı artık bir ÖNCEKİ maddenin sonuna eklenmiyor ("önalım" araması 119'u
  120'nin başlığıyla eşliyordu); sonuçlar madde_no ve başlık taşır, başlığı eşleşen madde önce gelir; 169/a,
  mükerrer ve "MADDE 166 ila 184" başlıkları doğru bölünür; dipnot ve değişiklik tabloları son maddeye yapışmaz.
- `mevzuat_icindekiler`: `number`, `compact`, `madde_from`/`madde_to`, `heading_query` (hepsi isteğe bağlı).
- Atıf: RG tarihi eksik kayıtta RG sayısı korunur (`RG sayı 25134`), `citation_missing` listesi; mükerrer RG.
- Önbellek: TTL'li, bayt sınırlı, eşzamanlı yüklemede tek istek; boş metin önbelleğe alınmaz.
- `textx.html_to_text`: kaynak satır kırılmaları ve satır içi etiketlerin böldüğü kelimeler birleştirilir.
- Talimat: "MADDE ATFI KURALI" hem bu sunucunun hem birleştiricinin talimatının EN BAŞINDA (istemciler uzun
  talimatı ~2.000 karakterde kesiyordu); hız kuralı "tempo kuralıdır, doğrulanacak madde sayısına sınır değildir".
- Testler: `tests/test_madde.py` (canlı Bedesten yanıtlarından kısaltılmış fikstürle SMK m.120 regresyonu).

## 0.4.0 — 2026-09-20

- `mevzuat_ara` takes `konu` (`enerji` · `rekabet` · `vergi` · `icra`), `esik` and `max_pages`, and no
  longer needs a query: Bedesten lists by `types` and/or RG date range without one (verified live).
  That turns "which laws amended the İİK in the last two years" into a question that can be asked.
  - **Omnibus laws.** "Bazı Kanunlarda Değişiklik Yapılmasına Dair Kanun" says nothing about what it
    amends; Law 7531 amends the İİK and the HMK. When the title does not pass, the names of the
    amended laws are read from the first page of the text and scored. An omnibus law whose names
    cannot be read (request cap of 5 per call, fetch error, "İlgili Kanunlara işlenmiştir") is
    **never dropped** — it comes back with `konu_kaynak: "belirsiz"` and is counted in `konu_belirsiz`.
  - **Measured separately, not copied from the gazette numbers.** 187 Bedesten titles, 6 types,
    RG 2024-09…2026-09 (`arthurlegal-1.9.1-jev-edition/olc_mevzuat.py`). 127 of them are training
    examples of the gazette model, so that slice measures format transfer only: 26/26 positives
    kept, 3 false positives. On the 60 unseen titles: **vergi 4/4, icra 2/2, enerji 0/3** (two are
    the gazette golden set's known traps — solid fuels, street-lighting deductions — one is new:
    wind power monitoring), 2 false positives; without the omnibus pass vergi 3/4, icra 1/2. The
    schema, the guide, every response and `status` repeat these numbers. Do not rely on `konu`
    for energy in legislation; combine it with `query`.
- Fixed: **`ictihat_ara` / `ictihat_semantik_ara` with only `date_from` (or only `date_to`) were
  unfiltered.** Bedesten ignores a one-sided `kararTarihi` range without saying so: "işe iade" with
  `date_from=2025-01-01` returned 52,993 decisions, the same as with no date; with both bounds, 612
  (verified live 2026-09-20). `ictihat_semantik_ara` only takes `date_from`, so its date filter never
  worked. The adapter now fills the missing bound.
- Fixed: a one-sided RG date range was **silently ignored** by Bedesten — `rg_date_from="2024-09-01"`
  alone returned all 917 laws instead of 6 (verified live 2026-09-20; the same with `rg_date_to`
  alone). The adapter now fills the missing bound. Every earlier `mevzuat_ara` call that passed one
  bound got unfiltered results without being told.
- Fixed: `mevzuat_ara` allowed `page_size` up to 50, but Bedesten answers HTTP 400 above **20**
  ("Kayıt sayısı 20'den fazla olamaz"). Clamped; schema and docs corrected.
- `crawl.py --only-new` and `tazele.sh`: a refresh mode that skips refs already in the index
  *before downloading them*. `upsert` replaces a body and drops its vector, so re-running the EPDK
  crawl (first done with `--fetch-text`) would have overwritten 3,745 full texts with listing stubs.
  First refresh, 2026-09-20: +94 documents (rekabet 77, spk 14, bddk 2, epdk 1), 0 overwritten,
  19,404 vectors intact.
- `crawl.py --backfill-text`: BDDK, BTK and Rekabet were first indexed from their listings, so 13,313 of 19,498
  archive documents had a body equal to their title and `semantik_ara` searched titles, not decisions. The backfill
  fetches full text for exactly those rows (resumable; the vector of a changed row is dropped). 2026-09-20: BDDK
  964/964, BTK 1,897/1,904. "idari para cezası" occurs in the text of 419 BTK decisions and in the title of 9; "dolaylı pay sahipliği" in the text
  of 60 BDDK decisions and in the title of none.
  Rekabet is left title-only on purpose — its upstream search already matches inside the PDFs. The regulators'
  `notes` (what `kurum_listesi` returns) now say which side holds the text.
- `triyaj.hazirla()` validates `konu`/`esik` in one place, before any request; `status.triyaj`
  carries the legislation measurement.

## 0.3.0 — 2026-09-20

- `resmi_gazete_tara` and `resmi_gazete_fihrist` take a `konu` filter
  (`enerji` · `rekabet` · `vergi` · `icra`): a local, network-free topic triage
  that finds items whose titles never mention the topic. This is a capability
  the tools did not have — `query` matches literally, and in the labelled
  corpus the topic word appears in only 10% of `icra` items, 14% of `rekabet`,
  58% of `enerji`, 61% of `vergi`. "Konkordato Gider Avansı Tarifesi" is an
  enforcement matter that no `icra` query will ever return.
  - `query` is no longer required; `query` or `konu` is. Given both, they AND.
  - New `triyaj.py` — standard library only, no numpy, no network, no key, no
    second process. Character 3–5-grams + tf-idf + logistic regression, Platt
    calibrated, over a rule layer that only ever raises a score. Trained in
    `arthurlegal-1.9.1-jev-edition`; only inference ships here.
  - **It is a pre-filter, not a guarantee.** Out-of-fold sensitivity at the
    measured 0.20 threshold: enerji 97%, rekabet 100%, vergi 93%, icra 92% —
    and lowering the threshold does not recover the rest. Every response says
    how many items were dropped; `esik=0` disables the filter and returns the
    full list with scores. Do not use `konu` for publication verification.
  - `status` now reports the triage model's presence, threshold and measured
    sensitivity, so a missing model is visible before it is relied on.

## 0.2.0 — 2026-09-06

- Removed KİK, Sayıştay, TÜRKPATENT and İSTAÇ: their official endpoints do not answer reliably
  (EKAP 500 after the signing change, Sayıştay WAF 418, reCAPTCHA, istac.org.tr DNS). Eight regulators
  remain, each verified live for both search and fetch. `aes_min.py` went with KİK.
- `mevzuat_gerekce` now calls `/getGerekceContent` (was returning "Content bulunmadı").
- SPK archive years (2005–2024) and every Sigorta Tahkim heading spelling are parsed.
- TLS fallback for hosts whose chain the container CA store cannot verify (BDDK, Resmî Gazete).
- Full index: 19,4xx documents across 8 regulators, vectorised with voyage-4-lite.

## 0.1.0 — 2026-09-05

First cut. 25 tools, 17 source adapters, standard library + optional `pypdf`.

- Bedesten içtihat (Yargıtay, Danıştay, BAM, yerel, KYB) and mevzuat (12 türler, madde ağacı,
  gerekçe) on a shared rate-limit bucket.
- AYM Kararlar Bilgi Bankası, Uyuşmazlık Mahkemesi, Resmî Gazete fihrist (+ aralık taraması).
- Regulators behind one `kurum_karari_*` interface: Rekabet, EPDK (Kurul kararları ağacı,
  5 piyasa), SPK (haftalık bülten + bölüm içi arama), BDDK (iki liste), KVKK (karar özetleri),
  BTK, KİK, Sayıştay, GİB, Sigorta Tahkim (66 dergi), İSTAÇ (kurallar), TÜRKPATENT (rota).
- Local hybrid index (`crawl.py`): FTS5 + trigram + vectors; dotless-ı query expansion;
  `semantik_ara` with per-kurum filter; `status` reports coverage per kurum.
- No paid search keys anywhere.

Known gaps: KİK API answers 500 after the 2026-09 signing-header change; Sayıştay WAF 418;
TÜRKPATENT reCAPTCHA. See docs/SOURCES.md.
