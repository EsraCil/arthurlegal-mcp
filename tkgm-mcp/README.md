# tkgm-mcp

Tapu-kadastro parsel araçları: TKGM Parsel Sorgu'dan **canlı parsel** (hız sınırlı), parsel raporu,
ölçü, **ölçekli SVG kroki**, harita (isteğe bağlı uydu görünümü), **tahmini 3D görünüm**, biçim dönüşümü
ve dayanak köprüsü. Yalnız standart kütüphane;
`pip install` yok. Birleşik `arthurlegal-mcp` ucunda araçlar `tkgm_` önekiyle görünür; adları ucun
öbür araçlarıyla (`tr_…_ara`, `tr_hukuk_arastirma_rehberi` gibi) sözcük paylaşmaz (`tests/test_canli.py`).

## Canlı parsel sorgusu (TKGM Parsel Sorgu)

Kullanıcı sohbette "Kadıköy Caferağa 123 ada 45 parsel", bir koordinat ya da bir yer adı söyler;
parsel TKGM Parsel Sorgu'nun herkese açık verisinden getirilir (`parsel_sorgula`,
`konumdan_parsel`, `yer_bul`). Dosya indirme adımı yoktur. Gelen GeoJSON, kullanıcının getirdiği
dosyayla aynı yoldan okunur; sonrası (`geometri`, `kroki`, `harita`, `parsel_raporu`,
`dayanak_koprusu`) yereldir. Malik, şerh, beyan ve rehin Parsel Sorgu'da yoktur; kullanıcı bunları
kendi e-Devlet / Web Tapu oturumundan alır. Bu sunucu hiçbir oturuma girmez.

Bağlantının bütün sınırları `tkgm_canli.py`dedir ve [docs/MANIFESTO.md](docs/MANIFESTO.md)
bunları ArthurLegal'in kim olduğuyla birlikte anlatır (istekler bu sayfanın adresini
User-Agent'ta taşır):

| Sınır | Değer | Nerede |
| --- | --- | --- |
| Aynı anda TKGM'de | makine başına en çok **1** istek | `Sinirlayici` |
| İki isteğin başlangıcı arası | en az **2 sn** → **dakikada en çok 30** (bütün kullanıcılar); paylaşılan uçta aralık makine sayısıyla çarpılır (2 makine → 4 sn), toplam yine 30 | `TKGM_ARALIK_SN`, `TKGM_MAKINE_SAYISI` |
| Sırada bekleme | en çok 20 sn → sıra `20 / aralık` istek; taşan istek TKGM'ye gitmez, "yoğun" döner | `TKGM_KUYRUK_SN` |
| TKGM yavaşlarsa | yanıt > 2 sn → aralık ×2, en çok 16 sn (dakikada ~4); 20 hızlı yanıtta yarıya | `Sinirlayici.cik` |
| 429 / 503 | en az 60 sn hiç istek yok, Retry-After'a uyulur, tekrarında 15 dk'ya kadar | `Devre` |
| Üst üste 3 ağ/sunucu hatası, JSON olmayan yanıt, yönlendirme | aynı geri çekilme; yönlendirme izlenmez | `Devre`, `_YonlendirmeYok` |
| 401 / 403 | 24 saat duruş; kimlik, adres, IP değiştirilmez | `Devre.reddedildi` |
| Önbellek | parsel 24 sa, "bulunamadı" 1 sa, il/ilçe/mahalle listeleri 30 gün; yalnız bellek | `Onbellek` |
| Toplu tarama | aynı adada 10 dk'da 12 ayrı parsel, 6 ardışık numara (ada 1 sa kapanır), mahallede saatte 40, ~1 km²'de saatte 20 nokta | `TaramaKorumasi` |
| Günlük toplam | 3.000 istek (Türkiye saatiyle; makinelere bölünür) | `TKGM_GUNLUK_AZAMI` |
| Uçlar | il (statik dosya), ilçe, mahalle listesi, parsel, konum; ada/parsel **listeleme** uçları hiç kullanılmaz | `UCLAR` |
| Yer adı | OpenStreetMap Nominatim, bütün makinelerin toplamı 1,1 sn'de bir istek (koşulu: saniyede en çok 1), 24 sa önbellek | `OSM` |
| Kapatma | `TKGM_CANLI=0` bütün canlı istekleri kapatır | `Kapi.acik` |
| Onay kartı | her sohbette ilk canlı sorgudan önce bir kez; `onay=true` ile geçilir | `server._onay` |

**Dakikada 30 nasıl hesaplandı.** Hedef, TKGM'ye Parsel Sorgu'yu kullanan tek bir kişiden fazla
yük bindirmemekti. Web arayüzü bir parseli bulmak için ilçe, mahalle, ada ve parsel listeleriyle
parselin kendisini ayrı ayrı çağırır ve harita karolarını yükler; bağlayıcı aynı parseli, listeler
önbelleğe girdikten sonra tek çağrıyla ve karosuz getirir. Ortalama eşzamanlı yük
`hız × yanıt süresi`dir: 0,5 istek/sn × 0,5 sn = 0,25, yani zamanın en az dörtte üçünde TKGM'de hiç
isteğimiz yoktur. Sıra kapasitesi `kuyruk / aralık`tır (yerelde 20 / 2 = 10, paylaşılan uçta makine
başına 20 / 4 = 5): bir anda bin çağrı gelse de TKGM'ye giden dakikada 30 istektir, kalanı burada
geri çevrilir.
Sınırlar süreç başınadır. Birleşik uç (Fly) iki makinede çalışır (`fly status`, 23.09.2026):
`TKGM_MAKINE_SAYISI` aralığı çarpar, günlük tavanı ve Nominatim hızını böler; Fly'da bu değişken
yoksa ihtiyatla 2 alınır. Makine sayısı değişirse değişken de değişmelidir. Yerel kurulumda aynı
sınırlar o bilgisayar için geçerlidir.

**Canlı doğrulama (23.09.2026).** Beş uç, bağlayıcının kendi aracıyla ve kendi sınırından geçerek
sınandı: koordinat → TBMM parseli (Ankara/Çankaya/Devlet 7955/5), aynı parsel ad yoluyla (il listesi
0,31 sn, ilçe listesi 0,14 sn, mahalle listesi 0,14 sn, parsel 0,09 sn; 2 sn aralıkla toplam 6,2 sn).
Ölçülen yanıtlarla ortalama eşzamanlı yük 0,5 × ~0,15 ≈ 0,08 istektir. Bir uç değişirse
`tkgm_canli.DOGRULANAN`dan çıkarılır; `server_status` → `canli.tkgm.dogrulanmamis_uclar` onu sayar.

TKGM ile resmî bir veri paylaşım kanalı kurulursa `tkgm_kaynak.canli_durum` o kanalın kapısıdır
(`TKGM_IZIN_BELGESI`, `TKGM_SERVIS_URL`); gövdesi o kanalın servis sözleşmesine göre yazılacaktır.

## Çalıştırma

    python server.py                                 # stdio — dosya okur/yazar
    python server.py --transport http --port 8060    # paylaşılan kip — dosya erişimi kapalı

| Ortam değişkeni | Anlamı |
| --- | --- |
| `TKGM_CIKTI` | Çıktı klasörü (varsayılan `~/ArthurLegal/tkgm`) |
| `TKGM_DOSYA_ERISIMI` | `1`/`0`: taşıma türünden bağımsız olarak dosya erişimini aç/kapat |
| `TKGM_CANLI` | `0`: canlı TKGM ve Nominatim isteklerini kapatır (varsayılan açık) |
| `TKGM_ARALIK_SN` | İki TKGM isteği arası en az süre (varsayılan 2, en az 1); makine sayısıyla çarpılır |
| `TKGM_MAKINE_SAYISI` | Paylaşılan ucun makine sayısı (Fly'da verilmezse 2, başka yerde 1) |
| `TKGM_KUYRUK_SN` | Sırada en uzun bekleme (varsayılan 20) |
| `TKGM_GUNLUK_AZAMI` | Günlük istek tavanı (varsayılan 3.000) |
| `TKGM_ZAMAN_ASIMI` | Tek isteğin zaman aşımı, sn (varsayılan 10) |
| `TKGM_ONAY_KARTI` | `0`: sohbet başı onay kartını kapatır (varsayılan açık) |
| `TKGM_API_URL`, `TKGM_WEB_URL`, `TKGM_NOMINATIM_URL` | Uç kökleri; testler sahte sunucuya yöneltmek için kullanır |
| `TKGM_IZIN_BELGESI`, `TKGM_SERVIS_URL` | Resmî veri paylaşım kanalının kapısı (gövdesiz) |

HTTP taşımasında dosya yolu kabul edilmez: kimlik doğrulamasız bir uçta yol alan araç,
sunucunun diskini internete açar. Orada parsel `icerik` ile metin olarak verilir, kroki
`svg` alanında döner.

## Tarayıcı arayüzü (ArthurLegal · Tapu)

    python server.py --ui        # http://127.0.0.1:8765 açılır: parseli TKGM'den getir ya da dosyayı bırak, kroki/harita/3D gör, Word föy indir
    python server.py --ui-ile    # MCP (stdio) + aynı süreçte arayüz: Claude'un okuttuğu parseller arayüzde de görünür

Görünüm AL Masası'yla aynı dildedir: açık tema varsayılan (beyaz zemin, siyah yazı), koyu tema AL
laciverti, Otomatik sistem temasına uyar; sol panelde sorgu formu ve parseller, sağda sekmeler. Renkler
`arthurlegal-app4all` ortak tasarımından (`al.css`), arma ve simge ArthurLegal simge üreticisinden gelir.

Arayüz ayrı bir API değildir; MCP araç listesini `POST /api/cagir` üzerinden açar. Yalnız 127.0.0.1'e
bağlanır. Kilitler: yabancı `Host` ret (DNS rebinding); süreç başına **rastgele belirteç** — her POST
`X-Tkgm: <belirteç>`, her `/api` ve `/dosya` GET'i `?t=<belirteç>` taşır ve belirteç yalnız sayfaya gömülür;
yabancı `Origin` ve `Sec-Fetch-Site: cross-site` ret; sayfa çerçevelenemez (`X-Frame-Options: DENY`).
Windows'ta kapıya münhasır bağlanılır (`SO_EXCLUSIVEADDRUSE`). Dosya sunumu çıktı klasöründeki düz
adlarla sınırlıdır.

**Harita karoları.** OpenStreetMap karo sunucusu Referer taşımayan isteğe "Access blocked" karosu döndürür.
Sayfalar `Referrer-Policy: strict-origin` ile sunulur ve Leaflet karoları aynı politikayla ister: yalnız
köken (`http://127.0.0.1:8765/`) gider, adresteki `?t=` belirteci gitmez. index.html sıkı bir CSP taşır;
harita ve 3D, srcdoc'lu iframe'lerde bu politikayı miras aldığı için izin listesi tam olarak yüklenen
adreslerdir (Leaflet için cdnjs, OSM karoları, three.js için jsDelivr, Esri karoları). Dosyadan
(çift tıklayarak) açılan harita köken gönderemez; sayfa bunu bir notla söyler.

**Arayüzde aç.** Arayüz aynı süreçte açıksa (`--ui-ile`; ArthurLegal kurulumu Tapu'yu böyle başlatır)
`harita`, `arazi_3d` ve `kroki` yanıtı `arayuzde_ac` bağlantısı da döndürür:
`http://127.0.0.1:8765/dosya/<ad>?g=<görüntüleme belirteci>`. Görüntüleme belirteci sayfa belirtecinden
ayrıdır, yalnız GET `/dosya/<ad>` açar, hiçbir `/api` ucunu açmaz ve başka bir sitenin sayfasına
gömülemez (yalnız üst düzey açılış). Ayar klasöründe kalıcıdır; arayüzü hangi süreç sunuyorsa
(Claude Desktop'un başlattığı MCP ya da masaüstü kısayolu) bağlantı çalışır.

**TKGM'den getir formu.** Parsel Sorgu'daki sırayla alt alta zorunlu kutular. İl → İlçe → Mahalle/köy açılır
listeleri TKGM'nin kendi idari listelerinden dolar (`GET /api/idari`); ad yazılmaz, seçilir, sorgu mahalle
kimliğiyle gider. Ada ve Parsel yalnız rakam alır (köy parsellerinde ada 0; parsel 0 reddedilir). Otokontrol:
bir alan eksik ya da geçersizken Sorgula kapalıdır ve altında eksikler yazar; tamamlanınca "Sorgulanacak: İl /
İlçe / Mahalle · ada/parsel" özeti çıkar. Gelen kayıt istenen ada, parsel ve mahalleyle karşılaştırılır; fark,
aktif olmayan durum ya da "gittiği parseller" varsa uyarı verilir. İlk tıklamada onay kartı sekme başına bir kez
sorulur, onaydan önce TKGM'ye istek gitmez. "Koordinatla getir" Türkiye dışındaki noktayı kabul etmez.

**Sor sekmesi.** Serbest metin kutusu. Belirlenimci girdiler yerel araçlarla hemen yanıtlanır.
"İstanbul Kadıköy Caferağa 123 ada 45 parsel" ya da koordinat yüklüyse seçilir; değilse sorgu atılmaz, metin
`GET /api/idari/coz` ile çözülüp soldaki kutulara aktarılır ve kullanıcı kontrol edip Sorgula'ya basar (mahalle
adı birden çok mahalleye uyarsa il ve ilçe dolu gelir, mahalle listeden seçilir). "satış harcı 3.000.000 TL",
"koridor 16 m", "önalım" gibi konu adları da burada yanıtlanır. Eşleşmeyen metin, Claude'a yapıştırılacak bir komuta dönüşür
("ArthurLegal'de sor: … (tkgm_ref: …)") ve Kopyala düğmesiyle alınır. Claude cevabı `arayuze_yaz` ile
teslim eder, cevap Cevaplar listesine düşer. Bunun için Claude Desktop'ta yerel tkgm-mcp gerekir: paylaşılan
uç kullanıcının diskine yazamaz. O durumda cevap elle yapıştırılıp kaydedilir. Arayüz modele doğrudan
bağlanmaz ve API anahtarı istemez.
Kroki `<img>` içinde, harita ve 3D `sandbox`'lı iframe içinde gösterilir. "Dosyalarım" sekmesi çıktı
klasörünü listeler — Claude oturumlarında üretilen dosyalar da orada görünür. Sayfa adresine `#uc`
eklenirse 3D sekmesi, `#harita` eklenirse harita sekmesi açılır.

## Tahmini 3D görünüm

`arazi_3d` (ve arayüzün "3D Görünüm" sekmesi) araziyi Copernicus GLO-30 yükseklik modelinden, çevre
binaların tabanlarını Overture Maps açık verisinden (OpenStreetMap ve Microsoft ML) çizer. Binanın boyu
kural tablosuyla tahmin edilir (`tkgm_bina.py`): kullanıcının verdiği `kat` > tapu niteliğindeki ifade
("Altı Katlı", "Zemin+4") > açık verideki yükseklik/kat > varsayılan 3 kat; kat başına 3 m. Kullanıcı ya
da nitelik yüksekliği varsa açık verideki yükseklikler o sahnede kullanılmaz, komşular varsayılan kattır.
Bina verisi gelmezse arazi yine çizilir ve yanıtta söylenir. Bina verisi diske yazılmaz.

Kütle görüntüden çıkarılmaz; Google görüntüsünden bina izi ya da 3D türetilmez. Uydu görüntüsü (aşağıda)
3D'de yalnız zemin dokusu olarak serilir. Her yüzeyde aynı uyarı durur — 3D sayfasında kartın dışında,
betiksiz ve kapatılamaz bir şerit, araç yanıtında `uyari_3d`, arayüzün 3D sekmesinde:

> UYARI: Bu 3B görsel gerçek (ölçülmüş ya da resmî) veriye dayanmaz. Yapay zekâ ve otomatik kurallarla üretilmiş bir TAHMİNDİR; yalnız fikir vermek amacıyla oluşturulmuştur. Ölçüm, imar durumu, ruhsat, röleve ya da bilirkişi incelemesi yerine geçmez ve resmî işlemde kullanılamaz.

## Uydu görünümü (Esri, kendi anahtarınızla)

Harita ve 3D, Esri World Imagery uydu görüntüsünü de gösterebilir. Bunun için her kullanıcı kendi ücretsiz
ArcGIS Location Platform anahtarını (Basemaps yetkili) arayüzün Harita ya da 3D sekmesindeki "Uydu
görünümü" kutusuna girer, ya da `TKGM_ESRI_ANAHTARI` ortam değişkenine yazar. Anahtar ayar klasöründe
durur (`~/.arthurlegal/tkgm`, `TKGM_AYAR` ile değişir), hiçbir üretilen dosyaya, araç yanıtına ya da
Claude'a gitmez: sayfada bir işaret durur, anahtarı yalnız yerel arayüz sayfayı tarayıcıya verirken
yerleştirir. Anahtar girilmemişse altlık OpenStreetMap'tir.

## Araçlar

| Araç | İş |
| --- | --- |
| `baslangic` | Akış, sınırlar, kapsam dışı kalanlar |
| `parsel_sorgula` | Canlı parsel: il/ilçe/mahalle + ada/parsel ya da serbest `metin`; belirsizlikte `adaylar` |
| `konumdan_parsel` | Koordinattaki parsel (canlı) |
| `yer_bul` | Yer adı/adres → en çok 5 koordinat adayı (OpenStreetMap Nominatim) |
| `parsel_raporu` | Tek parsel raporu (markdown); yerelde kroki, harita ve Word föy dosyaları da |
| `baglanti` | Parsel Sorgu / Web Tapu / e-Devlet bağlantıları ve indirme adımları |
| `parsel_oku` | Kullanıcının getirdiği GeoJSON/KML → `ref`, öznitelik, hesap alanı, nitelik rejimi uyarıları |
| `geometri` | Alan, çevre, kenar boyları, semtler, iç açılar (grad) |
| `kroki` | A4, mm birimli, ölçekli SVG; komşu parsellerle |
| `harita` | OpenStreetMap altlıklı etkileşimli HTML harita (karoları kullanıcının tarayıcısı yükler); arayüzde kendi anahtarınızla uydu katmanı |
| `arazi_3d` | Copernicus GLO-30 üstüne parsel: kot, eğim, bakı + çevre binaların **tahmini** kütlesi + 3D HTML; `uyari_3d` aynen iletilir — yalnız yerel kipte |
| `koridor_kesisim` | Hat + genişlik → parsel başına kesilen alan ve eksen boyu, ölçülmüş hata payıyla |
| `parsel_foyu` / `portfoy_tablosu` | Word föyü (.docx) ve Excel dökümü (.xlsx) — yalnız yerel kipte |
| `tarife_kalemi` / `harc_hesapla` | 2026 tapu harcı + döner sermaye; her rakam `veri/tarife_2026.json`da alıntısıyla |
| `tapu_kaydi_oku` | Kullanıcının kendi aldığı tapu kaydı metni → şema; TCKN/IBAN/telefon/e-posta **ve malik adları** maskeli, yalnız yerel kipte |
| `disa_aktar` | GeoJSON (RFC 7946 sarımı), KML, DXF (R12, TM koordinatlı), CSV (Türkçe Excel: `;`, ondalık virgül, BOM) |
| `arayuze_yaz` | Sor sekmesinden gelen soruya verilen cevabı yerel arayüze teslim eder — yalnız yerel kipte |
| `koordinat_donustur` | Coğrafi ↔ ITRF96 TM 3° / UTM 6° |
| `dayanak_koprusu` | Dayanak madde adresleri + hazır `tr_mevzuat_madde_getir` / `tr_ictihat_ara` çağrıları |

## Tapu kaydında ne maskelenir

`tapu_kaydi_oku` iki katmanda maskeler. Kalıp katmanı sağlama doğrular: NVİ basamakları
tutmayan on bir haneli sayı (yevmiye no) maskelenmez, tutan maskelenir; IBAN mod-97'den,
VKN ise yalnız satırında "vergi kimlik no"/"VKN" geçiyorsa. Ad katmanı NLP kullanmaz —
ayrıştırıcı adın hangi dizge olduğunu `Malik:` satırından zaten bilir, bu yüzden ad
`{{MALİK-nn}}` olur ve şerh/rehin satırları dâhil kayıdın her yerinde aynı etiketi alır.

Ad eşleşmesi Türkçe harf sınıflarıyla kurulur (İ/i/ı/I, ASCII'ye indirilmiş yazım, çift boşluk,
satır sonunda bölünmüş ad). TAKBİS biçimleri ("(SN:…) AD SOYAD : BABA", "X oğlu/kızı") çözülür;
yalnız soyadıyla anma da maskelenir. Malik satırı tanınmazsa metin **işlenmez** (kapalı başarısızlık);
`ad_maskele=false` bilinçli rıza yoludur. Tüzel kişi ve kamu malikleri (Hazine, belediye, şirket)
kişisel veri olmadığı ve hukuken anlamlı olduğu için maskelenmez.

[ArthurMask](https://github.com/beerbottle90/arthur-mask) kuruluysa şerh/rehin satırlarındaki üçüncü
kişi ve kurum adları da maskelenir (`{{KİŞİ-nn}}`, `{{KURUM-nn}}`). Kanca isteğe bağlıdır: sunucu onsuz
standart kütüphaneyle çalışır, kasa kullanılmaz, diske eşleştirme yazılmaz. `TKGM_ARTHUR_MASK=0` kapatır.
Yanıt maskelenMEYENİ de adıyla sayar: adres, doğum tarihi ve ArthurMask yoksa üçüncü kişi adları.

## Dış ağ çağrıları

- **TKGM Parsel Sorgu:** yalnız `tkgm_canli.Kapi` üzerinden, yukarıdaki sınırlarla. `server.py`
  ağ modülü içe aktarmaz (test denetler).
- **OpenStreetMap Nominatim:** yalnız `yer_bul`, saniyede en çok bir istek.
- **Copernicus DEM:** `arazi_3d`nin karosu (AWS açık veri, adres beyaz listeli, ~1-2 MB), yalnız yerel
  kipte: kimlik doğrulamasız paylaşılan uçta isteyen herkes sunucuya bant genişliği harcatabilirdi.
- **Overture Maps:** `arazi_3d`nin bina tabanları (`stac.overturemaps.org` sürüm adı + resmî PMTiles
  arşivinden HTTP Range, parsel başına ~300 KB), yalnız yerel kipte, yalnız bellekte.
- **Esri:** yalnız kullanıcı anahtarını kaydederken, anahtarın geçerliliğini sınamak için bir istek.

Harita ve 3D sayfalarındaki OSM/Esri karoları ile three.js/Leaflet betiklerini sunucu değil, dosyayı
açan tarayıcı yükler.

## Dışa aktarımın hassasiyeti

Parsel Sorgu köşe koordinatlarını **5 ondalığa** yuvarlayarak verir (~1,1 m ızgara). On üç köşeli
bir kent parselinde bu tek başına ±25 m² alan belirsizliğidir. `geometri` ve `parsel_oku`
belirsizliği kapalı formülle hesaplar ve "hesap alanı – kayıtlı alan" farkının bunun içinde
kalıp kalmadığını söyler; kroki yaklaşık boyları "≈" ile ve tek ondalıkla yazar.

## Ölçü nasıl yapılır

Kadastro alanı projeksiyon düzleminde hesaplanır: köşeler ITRF96 / TM 3° dilimine
(DOM 27…45, EPSG:5253–5259) Krüger serisiyle (n⁴) izdüşürülür, alan Gauss formülüyle çıkar.
Gidiş-dönüş hatası mikrometre mertebesindedir. ED50 datum dönüşümü yapılmaz.

Kroki milimetre birimlidir: %100 ölçekle yazdırıldığında başlıktaki ölçekte çıkar. Yine de
**aplikasyon krokisi veya röperli kroki yerine geçmez**; hesap alanı ile dosyadaki alan
arasındaki fark bir işarettir, hata tespiti değildir.

## Token disiplini

Görsel ve dışa aktarım dosyaya yazılır; modele yol ve birkaç satır özet döner (kroki yanıtı
~120 token). Yanıtlar sıkıştırılmış JSON'dur. Parseller içerik özetinden türeyen `ref` ile
anılır; geometri her çağrıda yeniden gönderilmez.

## Sürüm notları

**0.6.1 (30.10.2026)**
- Arayüzün harç kartı sadeleşti: kod yerine işlem seçilir (satış, bağış, ipotek, şerhler, taksim,
  ifraz/tevhit, terkin; gerisi "Diğer işlem" aramasıyla). Yalnız o işlemin hesapta istediği kutular
  görünür; hangisinin istendiğini tarife verisi belirler (`tkgm_harc.islemler`). Döner sermaye satırı
  parselin niteliğinden (bağımsız bölüm mü), yöresel katsayı ilçeye göre bu bilgisayarda hatırlanır.
  Sonuç tek tabloda; dayanak ve alıntılar katlanır bölümde.
- `tarife_kalemi` sonuçları her satırın istediği girdileri de taşır (`girdi`).

**0.6.0 (30.10.2026)**
- Tahmini 3D görünüm: `arazi_3d` çevre binaların tahmini kütlesini çizer (`kat`, `binalar`,
  `varsayilan_kat`); yanıt `uyari_3d` ve `binalar` özetini taşır. Uyarı her yüzeyde, kapatılamaz.
- Uydu görünümü: Esri World Imagery, kullanıcının kendi anahtarıyla; anahtar dosyaya ve yanıta yazılmaz.
- Harita karoları: `strict-origin` Referer politikası (OSM'nin "Access blocked" karosu), sıkı CSP,
  dosyadan açılan harita için not; bilgi kartı katlanır, yakınlaştırma düğmeleri kartın altında kalmaz.
- `arayuzde_ac`: harita, 3D ve kroki yanıtında arayüzde açan bağlantı (yalnız o dosyayı açan belirteçle).
- Arayüz AL Masası'nın tasarım diline geçti (açık/koyu/otomatik tema, sol panel, yumuşak kartlar);
  logo ve simge yeni ArthurLegal simgesinden.

## Test

    python -m unittest discover -s tests

İzdüşüm ezber bir koordinata karşı değil bağımsız hesaplara karşı sınanır: meridyen yayı
sayısal integralle, konformluk ve ölçek katsayısı sonlu farkla.
