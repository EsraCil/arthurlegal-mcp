# hesap-mcp: hesap kartları

Türk avukatları için üç hesap: avukatlık asgari ücreti, yargı harcı ve gider avansı. Hesabı model
değil kod yapar. Her kalem dayanağını ve Resmî Gazete kaynağını taşır.

| Araç (birleştiricide) | Ne yapar |
|---|---|
| `hesap_kalemleri` | İşlem menüsü ve her işlemin istediği girdiler |
| `hesap_vekalet_ucreti` | Avukatlık Asgari Ücret Tarifesi'ne göre asgari ücret |
| `hesap_yargi_harci` | 492 sayılı Kanun (1) sayılı tarife ve HMK gider avansı tarifesine göre harç |

## Veri

`veri/tarife_2026.json`. Kodda tarife tutarı yoktur; tutar, oran, eşik ve çarpanlar bu dosyadadır.
Her satır kaynağın metninden alıntı taşır.

| Kaynak | Künye |
|---|---|
| Avukatlık Asgari Ücret Tarifesi | RG 03.11.2026, S. 33419 |
| Harçlar Kanunu Genel Tebliği (Seri No: 98) eki | RG 31.12.2025, S. 33124 (5. Mükerrer) |
| HMK Gider Avansı Tarifesi | RG 28.10.2025, S. 33061 |

Metinden açıkça çıkmayan kurallar hesaba girmez. Toplamın dışında `ayrica` ya da `teyit_edin`
olarak görünürler; listesi verideki `dogrulanamayanlar` alanında.

Yeni yıl tarifesi gelince yalnız veri ve `tests/test_hesap.py`'deki sabit tutarlar güncellenir.

## Çalıştırma

    python server.py                                 # stdio
    python server.py --transport http --port 8070    # HTTP
    python -m unittest discover -s tests             # testler (ağsız)

Faiz, işçilik alacağı ve süre hesabı bu sunucuda yok.
