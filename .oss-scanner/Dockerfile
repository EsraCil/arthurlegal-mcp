# Anthropic OSS Scanner imajı (https://github.com/anthropics/oss-scanner).
#
# Tarayıcı bu dosyayı depo kökü bağlamıyla derler (ağ açık), sonra imajı internetsiz çalıştırıp kodu inceler.
# Bu yüzden testlerin istediği her şey burada kurulur. Paketler ve Python sürümü kökteki Dockerfile ve
# .github/workflows/testler.yml ile aynı. Altı hazır indeks (baked/, ~2 GB) depoda yok, imaja da girmez: testler
# onlarsız çalışır. Aynı imaj .github/workflows/oss-scanner-imaj.yml'de ağ kapalıyken denenir.

FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "de-eli-mcp==0.5.4" "pypdf==6.21.4" "numpy==2.4.6" "pypdfium2==5.14.0"

# Tarayıcı sözleşmesi: depo imajda /src'de durur.
COPY . /src
WORKDIR /src

# Testler: ağ geçidi (kök tests/, dosya başına ayrı süreç; sebebi testler.yml'de) ve her arka ucun tests/ klasörü.
# Başarısız test derlemeyi durdurmaz; sonuç günlükte kalır.
RUN for f in tests/test_*.py; do python -m unittest discover -s tests -p "$(basename "$f")" || echo "UYARI: $f"; done; \
    for d in */tests; do (cd "${d%/tests}" && python -m unittest discover -s tests) || echo "UYARI: $d"; done
