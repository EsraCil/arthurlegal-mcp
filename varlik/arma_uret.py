"""Sunucunun /logo.png ve /favicon.ico'sunu ArthurLegal simgesinden üretir (yalnız standart kütüphane).

Çizim `simge.py`'dir: ArthurLegal deposundaki ArthurLegal-setup/varlik/simge.py'nin kopyası (kurulum simgesi, Claude
eklentisi logosu ve Tapu arayüzü logosu da onunla çizilir; renkler Masa'nın armasıyla aynı). Asıl kaynak orada
değişirse kopya da güncellenir. Bağlayıcı listesi ve Claude dizini sunucunun favicon'unu gösterir.

    python varlik/arma_uret.py
"""

import sys
from pathlib import Path

VARLIK = Path(__file__).resolve().parent
sys.path.insert(0, str(VARLIK))

import simge  # noqa: E402

if __name__ == "__main__":
    (VARLIK / "logo.png").write_bytes(simge.simge_png(300))
    simge.ico_yaz(VARLIK / "favicon.ico", (16, 32, 48, 64, 128, 256))
    print("varlik/logo.png ve varlik/favicon.ico yazıldı")
