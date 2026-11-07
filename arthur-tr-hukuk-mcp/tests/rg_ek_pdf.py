"""Testler için küçük sentetik PDF'ler (ağsız, bağımlılıksız).

Resmî Gazete eklerinin üç sayfa türü:
    metin   — metin katmanı olan sayfa (Helvetica, eşiğin üstünde metin)
    jpeg    — tek DCTDecode görüntü + kısa üst bilgi: 20261103-3-1.pdf gibi (JPEG taraması)
    bos     — görüntüsü ve metni olmayan sayfa: JBIG2 taramasının (20251231M5-28.pdf) yerine; pypdf'in
              bayt olarak veremediği her sayfa gibi PDFium ister

JPEG 16x24 gri bir degradedir (Pillow ile bir kez üretildi; baytları aşağıda).
"""

from __future__ import annotations

from typing import Dict, List

JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300140e0f120f0d14121012171514181e32211e1c1c1e3d2c"
    "2e243249404c4b47404645505a736250556d5645466488656d777b8182814e608d978c7d96737e817cffc0000b080018"
    "001001011100ffc400160000030000000000000000000000000000030506ffc40025100001020602000700000000000000"
    "000011031200010204051306321431428182a1a2ffda0008010100003f00518a4bac56dbd7e12c96b863f4a7528d2089"
    "10627f14975867c86bd3809d0c76f5284c9ebea3f91ef00c525d603caeb75ed95bb06b4e6a38f9ba600f8fdc7fffd9")

METIN = ["MADDE 1 - Bu Tebligin amaci, avukatlik ucret tarifesinin uygulanmasina iliskin",
         "usul ve esaslari belirlemektir. Tarife eki cetvelde gosterilen tutarlar, dava ve",
         "takiplerin disindaki hukuki yardimlarda uygulanir. MADDE 2 - Bu Teblig yayimi",
         "tarihinde yururluge girer. MADDE 3 - Bu Teblig hukumlerini Bakan yurutur."]


def _dize(s: str) -> bytes:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1")


def _metin_akisi(satirlar: List[str]) -> bytes:
    govde = b"".join(b"(" + _dize(s) + b") Tj T* " for s in satirlar)
    return b"BT /F1 9 Tf 12 TL 40 800 Td " + govde + b"ET\n"


def pdf(sayfalar: List[Dict]) -> bytes:
    """``sayfalar``: her biri ``{"metin": [satır, ...], "jpeg": bayt, "genislik": n, "yukseklik": n,
    "dondur": derece}``; hepsi isteğe bağlı."""
    nesneler: List[bytes] = []

    def ekle(govde: bytes) -> int:
        nesneler.append(govde)
        return len(nesneler)

    katalog, kok = ekle(b""), ekle(b"")
    font = ekle(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    cocuklar = []
    for s in sayfalar:
        kaynak = [b"/Font << /F1 %d 0 R >>" % font]
        akis = b""
        if s.get("jpeg"):
            veri = s["jpeg"]
            im = ekle(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray "
                      b"/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n"
                      % (s.get("genislik", 16), s.get("yukseklik", 24), len(veri)) + veri + b"\nendstream")
            kaynak.append(b"/XObject << /Im0 %d 0 R >>" % im)
            akis += b"q 595 0 0 842 0 0 cm /Im0 Do Q\n"
        if s.get("metin"):
            akis += _metin_akisi(s["metin"])
        icerik = ekle(b"<< /Length %d >>\nstream\n" % len(akis) + akis + b"\nendstream")
        dondur = b" /Rotate %d" % s["dondur"] if s.get("dondur") else b""
        cocuklar.append(ekle(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842]%s /Resources << %s >> "
                             b"/Contents %d 0 R >>" % (kok, dondur, b" ".join(kaynak), icerik)))
    nesneler[katalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % kok
    nesneler[kok - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % c for c in cocuklar), len(cocuklar))

    out = b"%PDF-1.4\n"
    konumlar = []
    for i, govde in enumerate(nesneler, 1):
        konumlar.append(len(out))
        out += b"%d 0 obj\n" % i + govde + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(nesneler) + 1)
    out += b"".join(b"%010d 00000 n \n" % k for k in konumlar)
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(nesneler) + 1, katalog, xref)
    return out


def ek_pdf() -> bytes:
    """Üç sayfalık ek: 1 metin, 2 JPEG taraması (üst bilgiyle), 3 görüntüsüz (JBIG2 yerine)."""
    return pdf([{"metin": METIN}, {"jpeg": JPEG, "metin": ["Teblig (N) Ekleri 2"]}, {}])
