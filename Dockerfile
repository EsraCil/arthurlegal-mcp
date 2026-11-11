FROM python:3.11-slim

WORKDIR /app
COPY . /app

# pypdf (pure Python) is an optional dependency of the Turkish backend: Rekabet,
# EPDK, SPK, BTK and Sigorta Tahkim publish decisions as PDF.
# numpy is optional too: with it the semantic scan in retrieval.py is ~6x faster
# (16,545 vectors: 1.3 s -> 0.2 s); without it the same ranking comes slower.
# pypdfium2 (Apache-2.0 / BSD-3-Clause; PDFium BSD) is optional as well: tr_resmi_gazete_ek_sayfa
# renders scanned Official Gazette annex pages that are not plain JPEG (JBIG2, CCITT) with it.
# Without it a JPEG scan still goes through; the other pages answer with an UYARI line.
# olefile (old Word .doc files) is left out on purpose: the crawl workstation reads EPDK's .doc
# decisions, and the server answers them from the index.
# Exact versions, the same lines in .github/workflows/testler.yml and .oss-scanner/Dockerfile:
# the image installs what the tests ran against. The process runs as root inside its own VM; moving
# it to a separate user changes where start.sh seeds the indexes and needs a tested full build.
RUN pip install --no-cache-dir "de-eli-mcp==0.5.4" "pypdf==6.21.4" "numpy==2.4.6" "pypdfium2==5.14.0"  && chmod +x start.sh  && python -c "import sqlite3, json, zipfile, urllib.request, numpy, pypdfium2; print('stdlib ok, numpy', numpy.__version__, 'pypdfium2', pypdfium2.PYPDFIUM_INFO)"

# One BLAS thread: the machines have one shared vCPU, and a matrix-vector product
# per chunk gains nothing from more.
ENV MCP_TRANSPORT=http     MCP_HOST=0.0.0.0     MCP_PORT=8080     DE_ELI_URL=http://127.0.0.1:8790/mcp     INDEX_SOURCE=/app/baked     OPENBLAS_NUM_THREADS=1

EXPOSE 8080
CMD ["./start.sh"]
