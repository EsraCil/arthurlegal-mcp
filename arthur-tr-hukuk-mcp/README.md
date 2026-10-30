# arthur-tr-hukuk-mcp

> Eski adı **ArthurLegalTR** (23.09.2026'da değişti: ArthurLegal paket deposuyla karışıyordu).
> İşlev ve araç adları (`tr_`) aynı.

**Türk hukuku tek MCP ucunda: içtihat, mevzuat, 8 düzenleyici kurum, Resmî Gazete ve semantik arama.**

Yargıtay · Danıştay · BAM · yerel mahkeme · KYB · Anayasa Mahkemesi · Uyuşmazlık Mahkemesi ·
mevzuat (12 tür, madde ağacı, gerekçe) · Resmî Gazete · **Rekabet Kurumu · EPDK · SPK · BDDK ·
KVKK · BTK · GİB · Sigorta Tahkim Komisyonu**

24 araç. Standart kütüphane; tek opsiyonel bağımlılık `pypdf`. Ücretli arama anahtarı yok
(Brave / Tavily / OpenRouter gerekmez). Yerel SQLite indeks: FTS5 + trigram + vektör
(Voyage, OpenAI-uyumlu veya yerel Ollama).

## Neden

Said Sürücü'nün `yargi-mcp` / `mevzuat-mcp` çalışması Türk yargı kaynaklarının hangi uçtan
konuştuğunu ortaya koydu ([ATTRIBUTION.md](ATTRIBUTION.md)). Bu sunucu o bilgiyi alır, çerçeveyi
ve ücretli anahtarları bırakır, enerji/finans pratiğinin gerçekten atıf yaptığı sektör
düzenleyicilerini — EPDK Kurul kararları ağacı, SPK haftalık bültenleri, BDDK'nın iki karar
listesi, Sigorta Tahkim dergileri — ekler ve hepsinin arkasına, Türkçe sorunun
kelime paylaşmadığı kararı da bulabilen bir yerel indeks koyar.

## Kurulum

```bash
git clone https://github.com/beerbottle90/arthur-tr-hukuk-mcp
cd arthur-tr-hukuk-mcp
pip install pypdf          # opsiyonel ama PDF'ler için gerekli
python server.py           # stdio
```

Claude Desktop / Claude Code (`claude_desktop_config.json` ya da `claude mcp add`):

```json
{ "mcpServers": { "arthur-tr-hukuk-mcp": { "command": "python", "args": ["C:/…/arthur-tr-hukuk-mcp/server.py"] } } }
```

HTTP (claude.ai connector, Copilot Studio): `python server.py --transport http --port 8080` →
`POST /mcp`, `GET /health`. Kimlik doğrulama yoktur; `--host 0.0.0.0` bilinçli bir seçimdir.

## Araçlar

| Aile | Araçlar | Kaynak |
|---|---|---|
| İçtihat | `ictihat_ara` `ictihat_getir` `ictihat_semantik_ara` | Bedesten (5 mahkeme türü, 79 daire kodu) |
| AYM | `aym_ara` `aym_getir` | Kararlar Bilgi Bankası (norm + bireysel) |
| Uyuşmazlık | `uyusmazlik_ara` `uyusmazlik_getir` | kararlar.uyusmazlik.gov.tr |
| Mevzuat | `mevzuat_ara` `mevzuat_getir` `mevzuat_icindekiler` `mevzuat_madde_getir` `mevzuat_gerekce` `mevzuat_icinde_ara` | Bedesten mevzuat |
| Resmî Gazete | `resmi_gazete_fihrist` `resmi_gazete_getir` `resmi_gazete_tara` | resmigazete.gov.tr |
| Kurum | `kurum_karari_ara` `kurum_karari_getir` `kurum_listesi` `spk_bulten_icinde_ara` | 8 kurum, tek arayüz |
| Yerel indeks | `semantik_ara` `belge_getir` | `data/index.db` |
| Yardımcı | `hukuk_arastirma_rehberi` `status` | — |

Her sonuç `citation` taşır ve kayıttan üretilir; model esas/karar numarası uydurmaz. Erişilemeyen
kaynak `error` / `upstream_blocked` / `unavailable` döner — boş liste değil.

### Konu taraması — `resmi_gazete_tara(konu=…)`

```
resmi_gazete_tara(konu="enerji", date_from="2026-09-01", date_to="2026-09-19")
resmi_gazete_tara(konu="icra", query="tarife")      # ikisi VE ile birleşir
resmi_gazete_tara(konu="vergi", esik=0)             # süzgeç kapalı, skorlar yine gelir
```

`query` harfi harfine eşleşir; bir konuyu taramak için bu yetmez, çünkü Resmî Gazete başlıkları
konu adını çoğu zaman taşımaz. Etiketli gövdede ölçüldü:

| konu | gerçek pozitif | başlıkta konu adı geçen |
|---|---|---|
| enerji | 179 | 103 (%58) |
| vergi | 87 | 53 (%61) |
| rekabet | 37 | 5 (%14) |
| icra | 52 | 5 (%10) |

"Konkordato Gider Avansı Tarifesi" icradır ama `icra` yazmaz; "Şarj Hizmeti Yönetmeliği"
enerjidir ama `enerji` yazmaz. `konu` bunları bulur.

Süzgeç yereldir: ağ yok, anahtar yok, ücret yok, ikinci süreç yok — `triyaj.py`, standart
kütüphane. Karakter 3-5 gramı + tf-idf + lojistik regresyon, Platt ile kalibre, üstünde yalnız
yukarı çeken bir kural katmanı. Model `arthurlegal-1.9.1-jev-edition` deposunda eğitildi;
buraya yalnız çıkarım geldi.

**Sınırı:** bu bir ön elemedir. Kat dışı ölçümde 0.20 eşiğinde duyarlılık enerji %97,
rekabet %100, vergi %93, icra %92 — ve eşiği düşürmek kalanı kurtarmıyor. Her yanıt kaç
kalemin elendiğini yazar. **Yayım teyidi gibi eksiksizlik gerektiren işlerde `konu`
kullanmayın**; `query` kullanın ya da `esik=0` ile tam listeyi alın. Modelin durumu ve
ölçülmüş duyarlılığı `status` çıktısındaki `triyaj` bloğundadır.

### Kurum arayüzü

```
kurum_karari_ara(kurum="epdk", query="lisanssız üretim", market="elektrik")
kurum_karari_ara(kurum="rekabet", query="enerji", decision_type="birlesme_devralma")
kurum_karari_ara(kurum="bddk", query="faaliyet izni", year=2026)
kurum_karari_ara(kurum="sigorta_tahkim", query="kasko", issue=66)
kurum_karari_ara(kurum="spk", year=2026)  →  spk_bulten_icinde_ara(bulten="2026/27", query="halka arz")
kurum_karari_getir(kurum="epdk", id="https://www.epdk.gov.tr/Detay/DownloadDocument?id=…")
```

Kuruma özel filtreler `kurum_listesi` ile görülür; `params={…}` ile geçilir.


### Mevzuatta konu taraması — `mevzuat_ara(konu=…)`

```
mevzuat_ara(types=["KANUN"], rg_date_from="2024-09-01", konu="icra", max_pages=3)
mevzuat_ara(types=["TEBLIGLER"], rg_date_from="2026-06-01", konu="vergi")
```

Sorgu kelimesi gerekmez: Bedesten tür ve/veya RG tarih aralığıyla listeler, `konu` bu listeyi yerel
olarak eler. Asıl kazanç **torba kanunlar**dır. 7531 sayılı Kanun İcra ve İflas Kanununu ve HMK'yı
değiştirir; adı "Bazı Kanunlarda Değişiklik Yapılmasına Dair Kanun"dur. Başlık eşiği geçemezse
değiştirilen kanun adları metnin ilk sayfasından okunur ve onlar skorlanır. Adları okunamayan torba
kanun (çağrı başına en çok 5 metin açılır) **elenmez**; `konu_kaynak: "belirsiz"` ile döner.

Resmî Gazete'deki duyarlılık buraya kopyalanmadı, ayrıca ölçüldü (187 başlık, 6 tür):

| dilim | ne ölçer | sonuç |
|---|---|---|
| eğitimde görülmüş 127 başlık | yalnız Bedesten biçimine aktarım | 26/26 pozitif, 3 yanlış pozitif |
| görülmemiş 60 başlık | genelleme | vergi 4/4 · icra 2/2 · **enerji 0/3** · 2 yanlış pozitif |
| aynı, torba geçişi kapalı | başlığın tek başına yettiği yer | vergi 3/4 · icra 1/2 |

Enerjideki üç kaçağın ikisi RG altın kümesinin bilinen tuzağıdır (katı yakıt, aydınlatma gideri),
biri yenidir (Rüzgâr Gücü İzleme ve Tahmin Merkezine Bağlantı Yönetmeliği). Kural eklenerek
kapatılmadı: sınav kümesine bakarak kural yazmak sınavı anlamsızlaştırır. **Enerji taramasında
`konu`'ya güvenmeyin; `query` ile birlikte kullanın.** Pozitif sayısı azdır; yüzde değil sayı okuyun.

Sayfa başı en çok 20 kayıt gelir (Bedesten sınırı; 20'den büyüğü HTTP 400 döndürür).

## Yerel indeks ve semantik arama

Canlı kaynaklar indeks olmadan da çalışır. `semantik_ara` için:

```bash
python crawl.py --source kvkk,bddk,btk,rekabet,epdk,spk,sigorta_tahkim --embed
python crawl.py --source sigorta_tahkim --issues 1-66 --embed      # 16 yıl hakem kararı
python crawl.py --source spk --years 2023,2024,2025,2026 --embed
python crawl.py --source bddk,rekabet --fetch-text --embed         # karar PDF metinleri (yavaş)
```

Gömme ucu ortam değişkenleriyle seçilir; hiçbiri yoksa `127.0.0.1:11434` (Ollama, `bge-m3`) denenir:

| Değişken | Örnek |
|---|---|
| `EMBEDDINGS_URL` | `https://api.voyageai.com/v1/embeddings` |
| `EMBEDDINGS_MODEL` | `voyage-4-lite` (çok dilli, 1024) |
| `EMBEDDINGS_API_KEY` | Voyage / OpenAI anahtarı |

Vektör yoksa `semantik_ara` yine cevap verir ve `retrieval.semantic: "off"` der; anahtar kelime
eşleşmesini kavramsal eşleşme gibi sunmaz.

## Durum (2026-09-06)

| Kaynak | Durum |
|---|---|
| Bedesten içtihat + mevzuat (madde ağacı, gerekçe), AYM, Uyuşmazlık, RG, Rekabet, EPDK, SPK, BDDK, KVKK, BTK, GİB, Sigorta Tahkim | ✅ canlı uçta arama + getirme doğrulandı (2026-09-06) |
| KİK, Sayıştay, TÜRKPATENT, İSTAÇ | ❌ **kaldırıldı** — resmi uçları güvenilir cevap vermiyor (EKAP 500, Sayıştay WAF 418, reCAPTCHA, istac.org.tr DNS). Çalışıyormuş gibi gösterilmez; ayrıntı `docs/SOURCES.md` |

Ayrıntı ve endpoint'ler: [docs/SOURCES.md](docs/SOURCES.md).

## Dağıtım

**Üretim yolu: arthurlegal-mcp aggregator'ı.** Bu repo, `beerbottle90/arthurlegal-mcp`
içinde `tr_` önekiyle bir backend olarak yüklenir (`server.py` modül düzeyinde `TOOLS`
verir; aggregator `status`'u kendi durumuna katlar). Tek uç: `https://arthurlegal-mcp.fly.dev/mcp`
→ `tr_ictihat_ara`, `tr_kurum_karari_ara`, `tr_semantik_ara` … TR indeksi vektörleriyle birlikte (voyage-4-lite) imaja gömülür;
aggregator'ın `start.sh`'ı eksik vektör kalırsa Fly'daki anahtarla tamamlar.

Tek başına: `Dockerfile` + `fly.toml` (Fly.io, `fra`). İndeks imaja gömülür; açılan konteyner
asla crawl yapmaz. `flyctl secrets set EMBEDDINGS_API_KEY=…` semantik kanalı açar.

## Test

```bash
PYTHONIOENCODING=utf-8 python tests/test_offline.py     # ağ yok, 55 test
```

## Lisans

MIT. Üçüncü taraf bileşenler ve veri kaynakları için [ATTRIBUTION.md](ATTRIBUTION.md).
Çıktılar hukuki tavsiye değildir; her atıf birincil kaynaktan teyit edilmelidir.
