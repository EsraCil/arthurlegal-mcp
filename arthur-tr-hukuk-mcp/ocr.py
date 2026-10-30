"""İsteğe bağlı OCR: taranmış PDF'i (ve Word'e resim olarak konmuş sayfayı) metne çevirir.

Yalnız tarama makinesinde; sunucu imajına girmez.

EPDK'nın 2016-2020 kararlarının bir kısmı ve daha eskilerin bazıları taranmış PDF: içinde metin
katmanı yok, ``pypdf`` boş döndürür. Bu modül sayfaları görüntüye çevirip Tesseract'la okur;
çıkan metin indekse yazılır ve sunucu o kararları oradan verir.

Gerekenler (hepsi serbest lisanslı):
- ``pypdfium2`` ve ``Pillow``: sayfayı görüntüye çevirmek için.
- Tesseract 5 komut satırı: ``TESSERACT`` ortam değişkeni, yoksa PATH, yoksa Windows'ta
  ``C:\\Program Files\\Tesseract-OCR\\tesseract.exe``.
- Türkçe dil verisi ``tur.traineddata`` (tessdata_best): ``TESSDATA_PREFIX`` dizininde.

OCR metni okuma hatası taşıyabilir (rakam, özel isim); indekste ``meta.metin = "ocr"`` diye
işaretlenir ve sunucu bunu cevapta söyler.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Tuple

from textx import clean_ws

_WINDOWS_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def tesseract() -> str:
    """Tesseract'ın yolu; bulunamazsa ''."""
    yol = os.environ.get("TESSERACT") or shutil.which("tesseract") or ""
    if not yol and os.path.exists(_WINDOWS_TESSERACT):
        yol = _WINDOWS_TESSERACT
    return yol


def hazir(dil: str = "tur") -> bool:
    """OCR bu makinede çalışır mı: görüntüleyici, Tesseract ve dil verisi."""
    try:
        import pypdfium2  # noqa: F401
        import PIL  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    veri = os.environ.get("TESSDATA_PREFIX", "")
    return bool(tesseract()) and (not veri or os.path.exists(os.path.join(veri, dil + ".traineddata")))


def _oku(yol: str, dil: str) -> str:
    komut = [tesseract(), yol, "stdout", "-l", dil, "--psm", "3"]
    if os.environ.get("TESSDATA_PREFIX"):
        komut += ["--tessdata-dir", os.environ["TESSDATA_PREFIX"]]
    cikti = subprocess.run(komut, capture_output=True, timeout=600,
                           env=dict(os.environ, OMP_THREAD_LIMIT="1"))   # paralel belgelerde çekirdekleri paylaş
    return cikti.stdout.decode("utf-8", "replace")


def goruntu_ocr(resimler, dil: str = "tur") -> str:
    """Görüntü baytları (PNG, JPEG …) → metin. Taranmış sayfayı Word'e resim olarak koyan .docx'ler için."""
    from PIL import Image
    import io

    if not tesseract():
        raise RuntimeError("Tesseract bulunamadı (TESSERACT ortam değişkeni ya da PATH).")
    parcalar = []
    with tempfile.TemporaryDirectory() as dizin:
        for i, data in enumerate(resimler):
            yol = os.path.join(dizin, "r%03d.png" % i)
            Image.open(io.BytesIO(data)).convert("L").save(yol)
            parcalar.append(_oku(yol, dil))
    return clean_ws("\n\n".join(parcalar))


def pdf_ocr(data: bytes, dil: str = "tur", dpi: int = 300, max_sayfa: int = 40) -> Tuple[str, int]:
    """PDF baytları → (metin, sayfa sayısı). Her sayfa ``dpi`` çözünürlükte, gri tonlu okunur."""
    import pypdfium2 as pdfium

    if not tesseract():
        raise RuntimeError("Tesseract bulunamadı (TESSERACT ortam değişkeni ya da PATH).")
    pdf = pdfium.PdfDocument(data)
    n = len(pdf)
    parcalar = []
    with tempfile.TemporaryDirectory() as dizin:
        for i in range(min(n, max_sayfa)):
            yol = os.path.join(dizin, "s%03d.png" % i)
            pdf[i].render(scale=dpi / 72).to_pil().convert("L").save(yol)
            parcalar.append(_oku(yol, dil))
    pdf.close()
    return clean_ws("\n\n".join(parcalar)), n
