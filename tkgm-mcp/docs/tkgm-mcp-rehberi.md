# Tapu-Kadastro — tkgm MCP (Kullanım Rehberi)

> ✅ **Custom MCP backend VAR (`tkgm-mcp` v0.5.2).** TKGM Parsel Sorgu'dan **canlı parsel**
> (il/ilçe/mahalle + ada/parsel, koordinat ya da yer adı), parsel raporu, ölçü, **ölçekli kroki**,
> harita ve 3D arazi, güzergâh-koridor kesişimi, föy/portföy, harç-masraf hesabı, tapu kaydı
> yapılandırma ve **dayanak köprüsü**. Araç öneki **`tkgm_`**.
>
> **Kullanıcıdan dosya İSTEME.** "Kadıköy Caferağa 123 ada 45 parsel" diyen kullanıcıya parseli
> `tkgm_parsel_sorgula` getirir. Dosya yalnız kullanıcı kendiliğinden getirirse okunur.

---

## 0. Bağlantı bilgileri

| Alan | Değer |
|------|-------|
| **Birleşik uç** | `https://arthurlegal-mcp.fly.dev/mcp`: canlı sorgu, rapor, kroki (SVG yanıtta), harita; tapu kaydı aracı ÇALIŞMAZ (kişisel veri) |
| **Tam özellik** | Yerel stdio: `python server.py`, dosya okur, kroki/harita/Word föy dosyası yazar |
| **Araç öneki** | `tkgm_` |
| **Auth** | Yok |
| **Sunucu kaynağı** | `github.com/beerbottle90/arthurlegal-mcp/tree/master/tkgm-mcp` |
| **Tasarım** | `tkgm-mcp/docs/MANIFESTO.md`: hız sınırı, önbellek, onay kartı |

> ⚠️ `tkgm_` araçları görünmüyorsa `status` çağır: backend yüklenmemiş olabilir. Araç yokken
> parsel bilgisi, ölçü, kroki ya da harç tutarı **uydurma**.

---

## 1. Parsel nasıl gelir (ÖNCE BUNU OKU)

1. **Onay kartı.** Bir sohbetteki ilk canlı çağrı (`parsel_sorgula`, `konumdan_parsel`, `yer_bul`,
   `parsel_raporu` getirirken) `onay_gerekli` ve bir `kart` döndürür. Kartı kullanıcıya **olduğu
   gibi** göster. Kabul ederse aynı çağrıyı `onay=true` ile yinele; o sohbetin sonraki canlı
   çağrılarında da `onay=true` gönder, kartı bir daha gösterme. Kabul etmezse canlı sorgu yapma.
2. **Ada/parsel biliniyorsa:** `tkgm_parsel_sorgula(il, ilce, mahalle, ada, parsel)` ya da
   `tkgm_parsel_sorgula(metin="İstanbul Kadıköy Caferağa 123 ada 45 parsel")`. İl söylenmemişse
   ilçeden çıkarıp sen doldur (Kadıköy → İstanbul). Köy parsellerinde ada **0**'dır.
3. **Yer ya da adres biliniyorsa:** `tkgm_yer_bul(sorgu)` → adaylar → doğru adayla
   `tkgm_konumdan_parsel(enlem, boylam)`. Aday bir mahalle/ilçe gibi geniş bir yerse nokta
   rastgele bir parsele düşer: kullanıcıdan ada/parsel ya da kesin adres iste.
4. **Belirsizlik.** Yanıt `belirsiz` + `adaylar` taşıyorsa kullanıcıya sor, seçilen `mahalle_id` ile
   (ada/parsel yanıtta hazır) yinele. Tahminle seçme.
5. Her yol bir **`ref`** döndürür; sonraki bütün araçlar `ref` ister. Kullanıcı GeoJSON/KML
   getirirse `tkgm_parsel_oku` aynı `ref`i üretir.

**Hız ve sınırlar.** TKGM'ye tek sıra, dakikada en çok 30 istek (bütün kullanıcıların toplamı).
Yanıt "sırası dolu" ya da "bekliyor" diyorsa belirtilen süreden önce yineleme; kullanıcıya süreyi
söyle. Aynı adada parsel numaralarını tek tek **tarama**: ardışık altı numarada ada bir saat
kapanır. Komşu parsel için ada/parsel numarası ya da komşunun içindeki bir nokta kullan.

**Yapma:** ada/parsel numarasından geometri üretme; kullanıcıdan TKGM/e-Devlet şifresi isteme;
yüzlerce parsellik döküm için döngü kurma (araç bunu durdurur).

---

## 2. Araçlar

| Araç | İş | Dönen |
|---|---|---|
| `tkgm_baslangic` | Akış, sınırlar, kapsam dışı kalanlar | metin (~300 token) |
| `tkgm_parsel_sorgula` | Canlı parsel: il/ilçe/mahalle + ada/parsel ya da `metin` | kimlik, alanlar, konum, bağlantılar, `ref` |
| `tkgm_konumdan_parsel` | Koordinattaki parsel (canlı) | aynı |
| `tkgm_yer_bul` | Yer adı/adres → en çok 5 koordinat adayı (OpenStreetMap) | adaylar |
| `tkgm_parsel_raporu` | Tek parsel raporu (markdown); `konu` ile dayanak adresleri | `rapor_md` (+ yerelde dosyalar) |
| `tkgm_baglanti` | Parsel Sorgu / Web Tapu / e-Devlet bağlantıları | bağlantılar |
| `tkgm_parsel_oku` | Kullanıcının getirdiği GeoJSON/KML → `ref` | kısa JSON |
| `tkgm_geometri` | Alan, çevre, kenar boyları, semtler, iç açılar (grad) | kısa JSON |
| `tkgm_kroki` | A4, mm birimli **ölçekli SVG**; `komsular` ile çevre parseller | dosya yolu / SVG |
| `tkgm_harita` | OpenStreetMap altlıklı etkileşimli HTML harita | dosya yolu / HTML |
| `tkgm_arazi_3d` | Copernicus GLO-30 üstüne parsel; kot, eğim, bakı + 3D HTML (yalnız yerel) | özet + dosya yolu |
| `tkgm_koridor_kesisim` | Hat + genişlik → parsel başına kesilen alan, eksen boyu | tablo + CSV |
| `tkgm_parsel_foyu` | Tek parsel için Word föyü (yalnız yerel) | .docx yolu |
| `tkgm_portfoy_tablosu` | Çok parsel için Excel dökümü (yalnız yerel) | .xlsx yolu |
| `tkgm_tarife_kalemi` | 2026 tapu harcı ve döner sermaye cetvelinde işlem kalemi bulur | kod, oran/tutar |
| `tkgm_harc_hesapla` | Tapu harcı + TKGM döner sermaye bedeli; her kalem kaynağıyla | kısa JSON |
| `tkgm_tapu_kaydi_oku` | Kullanıcının kendi aldığı tapu kaydı METNİ → şema (yalnız yerel, maskeli) | kısa JSON |
| `tkgm_disa_aktar` | GeoJSON / KML / DXF (R12, TM koordinatlı) / CSV | dosya yolu / içerik |
| `tkgm_koordinat_donustur` | Coğrafi ↔ ITRF96 TM 3° / UTM 6° | koordinatlar |
| `tkgm_dayanak_koprusu` | Dayanak madde adresleri + hazır `tr_` çağrıları | kısa JSON |

**Token disiplini.** Yerel kipte görsel ve raporlar **dosyaya** yazılır, yanıtta yol ve birkaç
satır özet döner. `parsel_raporu`nun `rapor_md` alanı kullanıcıya olduğu gibi gösterilmek içindir.
SVG/HTML içeriğini sohbete dökme; kullanıcı isterse dosya yolunu ver.

---

## 3. Tipik akışlar

**"Şu parseli getir / bu parsel ne?"** `parsel_sorgula` → sonucu kısaca özetle (nitelik, kayıtlı
alan, konum bağlantısı, rejim uyarıları) → kullanıcı ayrıntı isterse `parsel_raporu(ref)`.

**Genel soru → yer.** "Moda'daki park hangi parselde?" → `yer_bul` → uygun aday →
`konumdan_parsel` → `parsel_raporu`.

**Tek parsel, dava dosyası.** `parsel_sorgula` → komşuları da getir → `kroki(ref, komsular)` →
`dayanak_koprusu(konu=…, ref=…)` → köprünün önerdiği `tr_mevzuat_madde_getir` / `tr_ictihat_ara`
çağrılarını çalıştır → yerelde `parsel_foyu`. Eğim/kot tartışması varsa `arazi_3d`.

**Portföy / due diligence.** Parseller tek tek `parsel_sorgula` ile gelir (toplu döngü değil) →
yerelde `portfoy_tablosu` → dikkat çekenlerde (büyük alan farkı, özel rejim uyarısı) föy.

**Güzergâh / proje sahası.** Parseller getirilir ya da dosyadan okunur →
`koridor_kesisim(refs, hat, genislik_m)` → CSV. `sayisal_hata_yuzde_en_kotu` ölçülmüş hata payıdır;
bedel hesabına girecek alan için yine de harita mühendisi ölçüsü gerekir.

**İşlem masrafı.** `tarife_kalemi(sorgu)` → `harc_hesapla(tapu_harci_islem, bedel,
emlak_vergi_degeri)` ve/veya `doner_sermaye_kod` + `yoresel_katsayi`. Tutarı yazarken aracın
döndürdüğü `dayanak`, `alinti` ve `matrah_kurali` alanlarını da aktar; tarife her yıl değişir.

---

## 4. Disiplin — bu verinin söyleyebildiği ve söyleyemediği

- **Parsel Sorgu verisi bilgi amaçlıdır.** Malik, şerh, beyan, rehin **içermez**. Bunlar için
  kullanıcı kendi e-Devlet / Web Tapu oturumundan belge alır; metnini `tapu_kaydi_oku`ya verir.
  Sunucu hiçbir oturuma girmez.
- **Kroki resmî belge değildir.** Aplikasyon krokisi ve röperli kroki yerine geçmez; mahkemeye
  sunulacaksa "bilgi amaçlı, TKGM Parsel Sorgu verisinden üretilmiştir" kaydıyla ek yapılır.
- **Alan farkı bir İŞARETTİR.** Hesap alanı ile tapu alanı arasındaki fark tek başına hata
  tespiti değildir; düzeltme 3402 s. K. m. 41 yoluna ve kadastro müdürlüğü kayıtlarına tabidir.
- **3D arazi 30 m'lik yüzey modelidir (DSM):** bina ve ağaç yüksekliklerini içerir; küçük kent
  parsellerinde eğim kabadır. Keşif ve harita mühendisi ölçüsünün yerini tutmaz.
- **`dayanak_koprusu` adres verir, metin vermez.** Madde metnini `tr_mevzuat_*` ile çekmeden
  alıntılama; yanıttaki `teyit` alanı "YOK" diyorsa adres ezberdendir: önce doğrula, doğrulanamadıysa yanına
  açıkça "UYARI: veri çekilemedi, teyidiniz gerekli: https://www.mevzuat.gov.tr/" yaz. Bu kural
  süs değildir: modülün ilk taslağı 5403 m. 8/İ'deki sınırdaş önalım fıkrasını yürürlükte
  sanıyordu (7255 s. K. ile 2020'de kaldırıldı); TMK 733 de 24.12.2025'te değişti (7571 s. K.
  m. 35: mutlak süre iki yıldan BİR yıla indi; 733/1'e 2886 s. K. satışları eklendi — cebrî artırma
  yasağı önceden de vardı) ve 734/2 rayiç bedel kuralını aldı (7571/36).
- **Tapu kaydı kişisel veridir (KVKK).** Araç T.C. kimlik no'yu maskeler ve hiçbir şeyi saklamaz;
  sen de malik adını gereksiz yere tekrarlama, başka araçlara taşıma. `yer_bul`a müvekkil adı gibi
  kişisel veri yazma: sorgu metni OpenStreetMap'e gider.
- **Koordinat sistemi.** Ölçü ITRF96 / TM 3° düzlemindedir (DOM 27-45). ED50 (eski paftalar)
  datum dönüşümü yapılmaz; ED50 koordinatı verilirse bunu söyle.

---

## 5. Kapsam dışı

| İstenen | Durum |
|---|---|
| Toplu indirme, güzergâh boyunca yüzlerce parsel | Yapılmaz: çağrı başına tek parsel, tarama deseni durdurulur |
| Malik/şerh bilgisini sunucunun çekmesi | Yapılmaz; yalnız kullanıcının kendi oturumundan aldığı belge |
| İmar durumu | Belediyelerin e-imar sistemleri ayrı ayrıdır; kapsamda değil |
| UYAP e-Satış ilanları | Ayrı çalışma; uçlar kullanıcının oturumundan kalibre edilmeden bağlanılmaz |
