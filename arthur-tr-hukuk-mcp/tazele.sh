#!/bin/sh
# Haftalik tazeleme: yalniz YENI kurum kararlarini indekse ekler.
#
#   sh tazele.sh                 # data/index.db uzerinde
#   INDEX_PATH=/yol/index.db sh tazele.sh
#
# Neden --only-new: `upsert` bir belgenin govdesini DEGISTIRIR ve vektorunu
# SILER. EPDK ilk seferde --fetch-text ile (1381 sn, 3.745 belge) tarandi;
# ayni komutu tekrar kosturmak tam metinleri kisa listeleme govdeleriyle ezer
# ve her vektoru yeniden hesaplatir. --only-new indeksteki ref'i indirmeden
# once atlar; metin ve vektor kalir.
#
# Vektorler: bu makinede Voyage anahtari yok. Fly'daki start.sh her acilista
# `crawl.py --embed-only` kosturur ve yalniz eksik vektorleri doldurur; yani
# yeni belgeler dagitimdan sonra kendiliginden vektorlenir. Anahtar varsa
# `--embed` eklenerek burada da doldurulabilir.
#
# Sayfa/yil sinirlari en yeni kayitlari kapsayacak kadar genis, tum arsivi
# yeniden tarayacak kadar dar tutuldu (rekabet 40 sayfa = ~400 karar,
# kvkk 6 sayfa = ~60 ozet). Kurumun yayim hizi degisirse buradan ayarlanir.
#
# Sigorta Tahkim: LATEST_KNOWN (sources/sigorta_tahkim.py) yeni sayi cikinca
# elle artirilir; 67 numarali dergi 2026-09-20'de henuz yoktu (404).
#
# Yeni yol (0.7.0, 2026-11-10): yeni kurum kararlari GitHub Actions'ta haftada iki
# kez taranir (.github/workflows/kurum-taramasi.yml -> ek_tarama.py: ayni --only-new
# yolu, Rekabet 10 / KVKK 3 / BTK 2 sayfa) ve canliya kucuk bir ek dosyasi olarak
# gider (veri/ek dali; kok indeks_ek.py, start.sh acilista uygular). Tam imaj
# gerekmez. Bu betik artik yalniz taban yeniden kurulurken kosar:
#   1. veri dalindaki ekleri tabanin kopyasina uygula:
#        python ../indeks_ek.py uygula --indeks <kopya> --klasor <veri>/arthur-tr-hukuk-mcp/ek --ad arthur-tr-hukuk-mcp
#   2. INDEX_PATH=<kopya> sh tazele.sh   (--pack dahil), baked/'a koy, tam imaj
#   3. kaydi yeni tabandan uret, veri dalini yalniz kayitla sifirla:
#        python ek_tarama.py kayit --indeks <kopya> --cikti <veri>/arthur-tr-hukuk-mcp/ek/kayit.json.gz
# Elle yapilan bir tazelemeyi tam imajsiz gondermek icin farki ek olarak uret (taban:
# canlidaki taban + veri dalindaki ekler) ve kaydi bu indeksten yeniden uret:
#   python ../indeks_ek.py uret --taban <taban+ekler> --yeni <bu indeks> --ad arthur-tr-hukuk-mcp --cikti <veri>
#   python ek_tarama.py kayit --indeks <bu indeks> --cikti <veri>/arthur-tr-hukuk-mcp/ek/kayit.json.gz
set -e
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
I="${INDEX_PATH:-data/index.db}"
EK="$*"   # ornek: sh tazele.sh --embed

python crawl.py --only-new --source kvkk           --pages 6            --index "$I" $EK
python crawl.py --only-new --source btk            --pages 3            --index "$I" $EK
python crawl.py --only-new --source bddk                                --index "$I" $EK
python crawl.py --only-new --source spk            --years "$(date +%Y)" --index "$I" $EK
python crawl.py --only-new --source sigorta_tahkim --issues 60-66       --index "$I" $EK
python crawl.py --only-new --source rekabet        --pages 40           --index "$I" $EK
python crawl.py --only-new --source epdk           --fetch-text         --index "$I" $EK
# Rekabet listesi metinsiz gelir; indeks 30.09.2026'dan beri tam metinli. Yalniz
# govdesi hala basliktan ibaret satirlar indirilir (yeni kararlar + metni cikmayanlar).
python crawl.py --backfill-text --source rekabet                        --index "$I" $EK
# Dagitim paketi: govde sutunu sonda, FTS birlesik (kurum suzgecli arama tam metni
# okumaz; bkz. crawl.py _pack). baked/'a kopyalamadan once.
python crawl.py --pack                                                  --index "$I"
echo "TAZELEME BITTI: $I"
