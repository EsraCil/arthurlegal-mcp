# hesap-mcp: hesap kartları

Türk avukatları için hesap kartları: avukatlık asgari ücreti, yargı harcı ve gider avansı, faiz dökümü (kanuni
faiz, ticari işlerde temerrüt faizi, sabit oran), kıdem ve ihbar tazminatı. Hesabı model değil kod yapar. Her kalem dayanağını ve Resmî Gazete kaynağını taşır; her sonuç verinin tazeliğini de söyler.

| Araç (birleştiricide) | Ne yapar |
|---|---|
| `hesap_kalemleri` | İşlem menüsü ve her işlemin istediği girdiler |
| `hesap_vekalet_ucreti` | Avukatlık Asgari Ücret Tarifesi'ne göre asgari ücret |
| `hesap_yargi_harci` | 492 sayılı Kanun (1) sayılı tarife ve HMK gider avansı tarifesine göre harç |
| `hesap_faiz` | 3095 sayılı Kanuna göre faiz dökümü: dönem dönem, gün sayılı, her dönemin oranı ve RG kaynağıyla |
| `hesap_iscilik_alacagi` | Kıdem (1475 m. 14, tavanlı) ve ihbar (4857 m. 17) tazminatı, brüt |

## Veri

`veri/tarife_2026.json`. Kodda tarife tutarı yoktur; tutar, oran, eşik ve çarpanlar bu dosyadadır.
Her satır kaynağın metninden alıntı taşır.

| Kaynak | Künye |
|---|---|
| Avukatlık Asgari Ücret Tarifesi | RG 03.11.2026, S. 33419 |
| Harçlar Kanunu Genel Tebliği (Seri No: 98) eki | RG 31.12.2025, S. 33124 (5. Mükerrer) |
| HMK Gider Avansı Tarifesi | RG 28.10.2025, S. 33061 |
| Kanuni faiz | BKK 2005/9831 (RG 30.12.2005, S. 26039; %9), CB Kararı 8485 (RG 21.05.2024, S. 32552; %24), 7589 s. K. m. 10 (RG 31.07.2026, S. 33326; reeskont oranının %80'i) |
| TCMB reeskont ve avans oranları | 32 tebliğ, RG 20.12.2005–20.12.2025; her biri kendi RG künyesi ve SHA-256'sıyla (`faiz.tcmb`) |
| Kıdem tazminatı tavanı | ÇSGB tablosu (Resmî Gazete'de yayımlanmaz), 01.01.2016–31.12.2026, 22 dönem |

Faiz kuralı kodda, oranlar veride: kanuni faiz ve ticari temerrüt faizi önceki yılın 31 Aralık günü uygulanan TCMB
oranına bağlıdır; 30 Haziran'daki oran beş puan ya da daha çok farklıysa yılın ikinci yarısında o oran geçerlidir
(3095 m. 1 ve m. 2/2). Bu yüzden iki tarih dışındaki oran değişiklikleri hesabı etkilemez, ama tamlık için 32 tebliğin
hepsi kayıtlıdır. Kanuni ve ticari faiz 01.01.2006–31.12.2026 arası için hesaplanır; 2027'nin oranı 31.12.2026'daki TCMB
oranına bağlıdır.

Metinden açıkça çıkmayan kurallar hesaba girmez. Toplamın dışında `ayrica` ya da `teyit_edin`
olarak görünürler; listesi verideki `dogrulanamayanlar` alanında.

Yeni yıl tarifesi gelince yalnız veri ve `tests/test_hesap.py`'deki sabit tutarlar güncellenir.

## Tazelik: Resmî Gazete nöbetçisi

Kayıtlı tarife sessizce eskiyebilir. Her hesap sonucu bu yüzden `tazelik` taşır (`tazelik.py`):

| Durum | Ne zaman | Sonuç |
|---|---|---|
| `guncel` | Fihrist tarandı, kartı etkileyebilecek bilinmeyen yayım yok | Toplam verilir |
| `yeni_duzenleme` | Desene uyan ve `izlenen.bilinen`de olmayan yayım var | Başta UYARI satırı; `toplam_tl` null, tutarlar `teyit_bekleyen_tutarlar`da |
| `kontrol_edilemedi` | Resmî Gazete'ye ulaşılamadı ya da tarama tamamlanmadı | Başta UYARI satırı; toplam "teyit edilmedi" diye işaretli |

- **Ne zaman taranır:** bir hesap aracı çağrılınca, son başarılı tarama 24 saatten eskiyse ya da bugünkü sayı henüz
  taranmadıysa. Aralık: verinin derlendiği gün ile 7 gün öncesinin geç olanından bugüne; o günlerin mükerrer
  sayıları dahil. Bitmiş ve taranmış günler yeniden istenmez.
- **Nezaket:** yalnız `https://www.resmigazete.gov.tr`; istekler arası en az 1 sn; süreç başına günde en çok 20
  istek; aynı anda tek tarama; başarısız denemeden sonra 30 dk beklenir. Hesap girdileri hiçbir yere gönderilmez.
- **Desenler ve bilinen yayımlar:** `veri/tarife_2026.json` → `izlenen`. Hangi kartın hangi yayımdan etkilendiği
  (`kartlar`) orada: yeni bir AAÜT yalnız vekâlet kartını, harç tebliği yalnız harç kartını, TCMB reeskont/avans
  tebliği ya da kanuni faiz kararı yalnız faiz kartını, İş Kanunu değişikliği ya da Kamu Görevlileri Hakem Kurulu kararı
  yalnız işçilik kartını kilitler.
- **Geçerlilik tarihi (takvim kilidi):** `izlenen.gecerlilik`. Kıdem tavanı Resmî Gazete'de yayımlanmaz (Hazine ve
  Maliye Bakanlığı genelgesi) ve yeni yılın faiz oranı 31 Aralık'taki TCMB oranına bağlıdır: fihrist bunları her zaman
  göstermez. Faiz ve işçilik kartları bu yüzden 31.12.2026'dan sonra yeni dönem verisi işlenene kadar kesin toplam
  vermez; GitHub nöbetçisi aynı gün bir kez issue açar.
- **Günlük GitHub nöbetçisi:** `.github/workflows/tarife-nobetcisi.yml` her gün 06.00 UTC'de son 3 günü aynı kodla
  tarar ve bilinmeyen her yayım için bir kez issue açar (`nobetci.py`). Üç farklı günde üst üste erişemezse ayrıca
  issue açar; kırmızıya düşmez, master'a commit atmaz.
- **Yeni yayım veriye işlenince:** tutarlar resmî ekten ikinci okumayla güncellenir, yayım `izlenen.bilinen`e eklenir.
- **Sınırlar:** torba kanunlar başlıktan yakalanamaz (öneri: mevzuat.gov.tr'de 492 ve 1136 kayıtlarının değişiklik
  listesine haftalık bakan ek denetim); süreç yeniden başlarsa yalnız son 7 gün yeniden taranır.
- `HESAP_RG_NOBETCI=kapali` ağı kapatır; kartlar `kontrol_edilemedi` döner.

## Çalıştırma

    python server.py                                 # stdio
    python server.py --transport http --port 8070    # HTTP
    python -m unittest discover -s tests             # testler (ağsız; faiz ve işçilik: tests/test_faiz_iscilik.py)
    python tazelik.py --gun 3                        # son 3 günün fihristini tara (canlı)

Süre (son gün) hesabı bu sunucuda yok.
