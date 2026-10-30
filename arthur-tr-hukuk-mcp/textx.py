"""Text utilities for arthur-tr-hukuk-mcp: HTML → text, PDF → text, Turkish folding.

The server's optional dependencies live here. ``pypdf`` is pure Python and
extracts text from the PDFs that Rekabet Kurumu, EPDK, SPK, BTK and the Sigorta
Tahkim Komisyonu publish. Without it the server still runs; PDF tools then
return the URL and say plainly that text extraction is off, rather than an empty
string that looks like an empty decision. ``olefile`` (pure Python, BSD) opens
old Word files (.doc), in which EPDK published most decisions before 2016; the
crawl workstation needs it, the server answers those from the index.
"""

from __future__ import annotations

import html as _html
import io
import math
import re
import struct
from typing import Any, Dict, List, Optional, Tuple

try:  # optional, pure Python
    from pypdf import PdfReader  # type: ignore
    HAS_PYPDF = True
except Exception:  # noqa: BLE001
    PdfReader = None  # type: ignore
    HAS_PYPDF = False

try:  # optional, pure Python
    import olefile  # type: ignore
    HAS_OLEFILE = True
except Exception:  # noqa: BLE001
    olefile = None  # type: ignore
    HAS_OLEFILE = False

__all__ = ["html_to_text", "pdf_to_text", "doc_to_text", "tr_lower", "tr_upper", "tr_fold", "fold_same_len",
           "paginate", "clean_ws", "strip_tags", "HAS_PYPDF", "HAS_OLEFILE", "excerpt", "count_hits",
           "iso_tarih"]

_TR_LOWER = str.maketrans("İIÇĞÖŞÜÂÎÛ", "iıçğöşüâîû")
_TR_UPPER = str.maketrans("iıçğöşüâîû", "İIÇĞÖŞÜÂÎÛ")
_TR_FOLD = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛ", "cgiosuaiuCGIOSUAIU")
# Uzunluğu KORUYAN katlama: her karakter tek karaktere gider. Bir regex'i katlanmış
# kopyada çalıştırıp konumu özgün metinde kullanmak için (``str.lower`` 'İ'yi iki
# karaktere açar; konumlar kayar).
_FOLD_1 = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZÇçĞğİıÖöŞşÜüÂâÎîÛû",
    "abcdefghijklmnopqrstuvwxyzccggiioossuuaaiiuu")


def tr_lower(text: str) -> str:
    """Lowercase with Turkish İ/I handling (``str.lower`` maps I→i, which is wrong)."""
    return text.translate(_TR_LOWER).lower()


def tr_upper(text: str) -> str:
    """Uppercase with Turkish i/ı handling (``str.upper`` maps i→I, which is wrong)."""
    return text.translate(_TR_UPPER).upper()


def tr_fold(text: str) -> str:
    """Lowercase + strip Turkish diacritics, so ``Kürşat`` and ``kursat`` compare equal."""
    return tr_lower(text).translate(_TR_FOLD)


def fold_same_len(text: str) -> str:
    """ASCII + Turkish letters → lower-case ASCII, one character for one character.

    ``len(fold_same_len(s)) == len(s)`` always holds, so a match position in the
    folded copy is a valid position in the original.
    """
    return text.translate(_FOLD_1)


def clean_ws(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


_BLOCK = re.compile(r"</?(p|div|br|tr|li|h[1-6]|table|ul|ol|section|article|blockquote|pre)\b[^>]*>", re.I)
_CELL = re.compile(r"</?(td|th)\b[^>]*>", re.I)
_DROP = re.compile(r"<(script|style|noscript|head|svg)\b.*?</\1>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
# Satır içi (inline) etiketler kelimeyi BÖLMEZ: '<span>İ</span>cra' tarayıcıda 'İcra'dır.
# Boşlukla değiştirmek 'İ cra' üretiyordu (canlı İİK metni) ve 'icra' araması kaçıyordu.
_INLINE = re.compile(r"</?(?:span|b|i|u|a|font|strong|em|sup|sub|small|big|mark|abbr|s|strike|"
                     r"del|ins|tt|nobr|wbr|o:p)\b[^>]*>", re.I)
_PRE = re.compile(r"(<pre\b[^>]*>.*?</pre>)", re.I | re.S)


def strip_tags(fragment: str) -> str:
    return clean_ws(_html.unescape(_TAG.sub(" ", fragment)))


def _collapse_ws(html: str) -> str:
    """HTML boşluk kuralı: <pre> dışında her boşluk dizisi (satır sonu dahil) tek boşluktur."""
    parts = _PRE.split(html)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"\s+", " ", parts[i])
    return "".join(parts)


def html_to_text(html: str, keep_headings: bool = True) -> str:
    """HTML → readable plain text with paragraph breaks. No parser dependency.

    Not Markdown: headings become their own lines, tables become rows with
    ``|`` between cells, everything else is paragraphs. That is what a language
    model needs from a court decision; bold and italics are noise.

    Whitespace follows HTML: when the input has block tags (p, div, br …), a
    raw line break inside a paragraph is a space, as in a browser. Bedesten's
    mevzuat HTML is hard-wrapped ('İtiraz\\netmek'); keeping those breaks split
    phrases and gave line-anchored regexes arbitrary line starts. Input with NO
    block tags keeps its newlines (EPDK feeds DOCX XML with '</w:p>' already
    turned into '\\n'), as before.
    """
    if not html:
        return ""
    text = _COMMENT.sub("", html)
    text = _DROP.sub(" ", text)
    if _BLOCK.search(text):
        text = _collapse_ws(text)
    if keep_headings:
        text = re.sub(r"<h([1-6])\b[^>]*>(.*?)</h\1>",
                      lambda m: "\n\n" + tr_upper(" ".join(_TAG.sub("", m.group(2)).split())) + "\n\n",
                      text, flags=re.I | re.S)
    text = _CELL.sub(" | ", text)
    text = _BLOCK.sub("\n", text)
    text = _INLINE.sub("", text)
    text = _TAG.sub(" ", text)
    text = _html.unescape(text)
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip(" |") for ln in text.split("\n")]
    text = "\n".join(lines)
    return clean_ws(text)


_TARIH = re.compile(r"(\d{1,2})\s*[./]\s*(\d{1,2})\s*[./]\s*(\d{4})")


def iso_tarih(text: str) -> str:
    """Metindeki ilk 'G.A.YYYY' ya da 'GG/AA/YYYY' tarihini ISO'ya çevirir; yoksa ''.

    Gün ve ay tek haneli olabilir: Rekabet'in listesi "4.8.1999" yazar. Kurum
    çözümleyicileri yalnız iki haneyi tanıyordu ve tarih alanı boş kalıyordu
    (11.10.2026: Rekabet'te 10.445 kararın 8.529'u); tarih süzgeci o kararları görmüyordu.
    """
    m = _TARIH.search(text or "")
    if not m:
        return ""
    gun, ay = int(m.group(1)), int(m.group(2))
    if not (1 <= gun <= 31 and 1 <= ay <= 12):
        return ""
    return "%s-%02d-%02d" % (m.group(3), ay, gun)


OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
# Word's control characters: cell/row end, line/page break, non-breaking and optional hyphen, picture,
# footnote and comment anchors. Field codes (0x13 instruction 0x14 result 0x15) keep only the result.
_DOC_CTRL = str.maketrans({"\r": "\n", "\x07": "\t", "\x0b": "\n", "\x0c": "\n", "\x1e": "-", "\x1f": "",
                           "\x01": "", "\x02": "", "\x05": "", "\x08": ""})


def _doc_fields(text: str) -> str:
    out: List[str] = []
    stack: List[bool] = []          # per open field: still inside its instruction?
    for ch in text:
        if ch == "\x13":
            stack.append(True)
        elif ch == "\x14" and stack:
            stack[-1] = False
        elif ch == "\x15" and stack:
            stack.pop()
        elif not any(stack):        # a field nested in an instruction belongs to that instruction
            out.append(ch)
    return "".join(out)


def doc_to_text(data: bytes) -> str:
    """Word 97–2003 (.doc) bytes → main document text; '' if olefile is missing or the file is not one.

    The text lies in the WordDocument stream in pieces. The piece table (the Clx in the
    0Table/1Table stream) gives each piece's position and whether it is 8-bit (cp1252)
    or UTF-16; the first ``ccpText`` characters are the body, headers and notes follow.
    Encrypted files and the pre-97 format give ''.
    """
    if not HAS_OLEFILE or not data.startswith(OLE_MAGIC):
        return ""
    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
        wd = ole.openstream("WordDocument").read()
        ident, nfib = struct.unpack_from("<HH", wd, 0)
        flags = struct.unpack_from("<H", wd, 0x0A)[0]
        if ident != 0xA5EC or nfib < 0xC1 or flags & 0x0100:
            return ""
        table = ole.openstream("1Table" if flags & 0x0200 else "0Table").read()
        csw = struct.unpack_from("<H", wd, 0x20)[0]
        lw = 0x22 + csw * 2 + 2                       # FibRgLw97, after its count
        cslw = struct.unpack_from("<H", wd, lw - 2)[0]
        ccp_text = struct.unpack_from("<i", wd, lw + 12)[0]
        fclcb = lw + cslw * 4 + 2                     # FibRgFcLcb97, after cbRgFcLcb
        fc_clx, lcb_clx = struct.unpack_from("<II", wd, fclcb + 33 * 8)
        clx = table[fc_clx:fc_clx + lcb_clx]
        i = 0
        while i < len(clx) and clx[i] == 0x01:       # Prc entries come first; skip them
            i += 3 + struct.unpack_from("<h", clx, i + 1)[0]
        if i >= len(clx) or clx[i] != 0x02:
            return ""
        lcb = struct.unpack_from("<I", clx, i + 1)[0]
        plc = clx[i + 5:i + 5 + lcb]
        n = (lcb - 4) // 12
        cps = struct.unpack_from("<%dI" % (n + 1), plc, 0)
        parts = []
        for k in range(n):
            fc = struct.unpack_from("<I", plc, 4 * (n + 1) + 8 * k + 2)[0]
            count = cps[k + 1] - cps[k]
            if fc & 0x40000000:
                start = (fc & 0x3FFFFFFF) // 2
                parts.append(wd[start:start + count].decode("cp1252", "replace"))
            else:
                parts.append(wd[fc:fc + 2 * count].decode("utf-16-le", "replace"))
        text = "".join(parts)[:max(0, ccp_text)]
    except Exception:  # noqa: BLE001 - damaged or unusual file: no text, not a crash
        return ""
    return clean_ws(_doc_fields(text).translate(_DOC_CTRL))


def pdf_to_text(data: bytes, max_pages: int = 0) -> Tuple[str, int]:
    """PDF bytes → (text, page_count). Empty text if pypdf is missing or the PDF is scanned."""
    if not HAS_PYPDF:
        return "", 0
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:  # noqa: BLE001 - corrupt PDF is a data problem, not a crash
        return "", 0
    pages = []
    n = len(reader.pages)
    limit = n if not max_pages else min(n, max_pages)
    for i in range(limit):
        try:
            pages.append(reader.pages[i].extract_text() or "")
        except Exception:  # noqa: BLE001
            pages.append("")
    text = "\n\n".join(p.strip() for p in pages)
    return clean_ws(text), n


def paginate(text: str, page: int, size: int = 6000) -> Dict[str, Any]:
    """Slice a long text into numbered chunks the model can page through."""
    text = text or ""
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = 6000
    if size <= 0:          # negatif boy text[0:-n] ile neredeyse tüm metni döndürüyordu
        size = 6000
    total = max(1, math.ceil(len(text) / size)) if text else 0
    page = max(1, int(page or 1))
    start = (page - 1) * size
    chunk = text[start:start + size]
    out = {"page": page, "total_pages": total, "chars": len(text),
           "has_more": page < total, "text": chunk}
    if total and page > total:
        # Boş 'text' burada "bölüm boş" demek değildir; sayfa yoktur.
        out["warning"] = "page %d > total_pages %d: bu sayfa yok; metin boş değil." % (page, total)
    return out


def excerpt(body: str, needle: str, width: int = 220) -> str:
    """Short window around the first (folded) occurrence of ``needle``."""
    if not body:
        return ""
    fb, fn = tr_fold(body), tr_fold(needle)
    i = fb.find(fn) if fn else -1
    if i < 0:
        return body[: width * 2].strip() + ("…" if len(body) > width * 2 else "")
    s, e = max(0, i - width), min(len(body), i + len(needle) + width)
    return ("…" if s > 0 else "") + body[s:e].strip() + ("…" if e < len(body) else "")


def count_hits(body: str, needle: str) -> int:
    fb, fn = tr_fold(body), tr_fold(needle)
    return fb.count(fn) if fn else 0
