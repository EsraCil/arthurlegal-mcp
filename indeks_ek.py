"""İndeks ekleri: gömülü indeksin üstüne açılışta işlenen küçük, eklemeli belge dosyaları.

Altı yerel SQLite indeksi (~2,2 GB) imaja gömülü (``baked/``); birini tazelemek tam imaj yüklemesi
istiyordu (~2,3 GB, ~3 saat). Kurum kararları eklemelidir: her karar ref'iyle tekildir, sonradan
değişmez, yenisi eklenir. Ek dosyası yalnız tabandan bu yana eklenen ya da düzeltilen belgeleri
taşır; sunucu açılışta (``start.sh``) onları gömülü indeksin üstüne işler. Altı indeks aynı
``retrieval.Index`` şemasını paylaştığı için biçim hepsinde aynıdır.

Biçim: ``<arka uç>/ek/YYYY-MM-DD[-N].jsonl.gz``. Bir kez yazılır, sonra değişmez; aynı gün ikinci dosya
``-2`` alır. Sıkıştırılmış JSON satırları, ilki başlık::

    {"ek": 1, "indeks": "arthur-tr-hukuk-mcp", "tarih": "2026-11-10", "uretim": "2026-11-10T09:00:00Z",
     "belge": 103, "yeni": 101, "degisen": 2, "durum": {"last_crawl": "2026-11-10T02:25:37"}}
    {"ref": "...", "title": "...", "body": "...", "url": "...", "lang": "tr", "date": "2026-11-06",
     "status": "...", "court": "...", "subject": "rekabet", "citation": "...", "meta": {...}}

Güvenlik: dosya yalnız veri taşır; ``json`` ile okunur, hiçbir şey çalıştırılmaz. Boyut (sıkışık ve
açılmış), alan kümesi, alan uzunluğu, belge sayısı ve hedef indeks adı denetlenir; uymayan, yarım ya da
bozuk dosya bütünüyle atlanır ve günlüğe yazılır, sunucu düşmez.

Uygulama eşgüçlüdür: içeriği aynı belge yeniden yazılmaz. Başlığı ya da gövdesi değişen belgenin
vektörü silinir (``embed_missing`` yenisini üretir); yalnız tarih, atıf, durum, adres, kurum, konu ya da
meta alanı değişen belge yerinde güncellenir ve vektörü kalır. Arama dizinleri (FTS5, dış içerik) belge
belge güncellenir: Türkiye indeksinde (1,4 GB) baştan kurmak sunucu okurken bütün dizini yeniden yazar.

    python indeks_ek.py uret --taban baked/pl-sejm-mcp.db --yeni yeni.db --ad pl-sejm-mcp --cikti DIZIN
    python indeks_ek.py uygula --indeks pl-sejm-mcp/data/index.db --klasor pl-sejm-mcp/ek --ad pl-sejm-mcp
    python indeks_ek.py dogrula DOSYA...          # yazmaz; biçime uymayan varsa çıkış 1
    python indeks_ek.py karsilastir A.db B.db --sorgu "ihale" --sorgu "kartel"   # eşdeğerlik

Yalnız standart kütüphane.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import sys
import time
import zlib
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

SURUM = 1
ALANLAR = ("ref", "title", "body", "url", "lang", "date", "status", "court", "subject", "citation", "meta")
METIN_ALANLARI = ("title", "body")
# Bugünkü indekslerde en uzunlar (2026-11-10): gövde 2.151.157 (İrlanda, bir Finance Act), başlık 2.788,
# atıf 1.297, meta 441 karakter. Sınırlar bunların birkaç katı; aşan dosya reddedilir.
_SINIR = {"ref": 512, "title": 20_000, "body": 5_000_000, "url": 4_000, "lang": 16, "date": 40,
          "status": 2_000, "court": 2_000, "subject": 2_000, "citation": 8_000}
META_SINIRI = 64 * 1024
DOSYA_SINIRI = 64 * 1024 * 1024          # sıkışık bayt
ACIK_SINIRI = 512 * 1024 * 1024          # açılmış bayt (gzip bombası)
BELGE_SINIRI = 200_000
KAYIT_ADI = "kayit.json.gz"
KAYIT_SINIRI = 16 * 1024 * 1024
KAYIT_ACIK_SINIRI = 128 * 1024 * 1024
DURUM_ANAHTARLARI = ("last_crawl", "last_backfill", "coverage", "corpus")
EK_ADI = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-([1-9][0-9]{0,2}))?\.jsonl\.gz$")
ARKA_UC = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_TARIH = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BASLIK_ALANLARI = frozenset(("ek", "indeks", "tarih", "uretim", "belge", "yeni", "degisen", "durum"))
_KAYIT_ALANLARI = frozenset(("kayit", "indeks", "guncelleme", "belge", "kurumlar", "metinsiz"))


class EkHatasi(ValueError):
    """Ek ya da kayıt dosyası okunamadı veya biçime uymuyor."""


# --------------------------------------------------------------------------- #
# Okuma ve denetim                                                             #
# --------------------------------------------------------------------------- #
def _cift_anahtar(ciftler: List[Tuple[str, Any]]) -> Dict[str, Any]:
    nesne: Dict[str, Any] = {}
    for anahtar, deger in ciftler:
        if anahtar in nesne:
            raise EkHatasi("yinelenen alan: %s" % anahtar[:40])
        nesne[anahtar] = deger
    return nesne


def _sayi_degil(ad: str) -> Any:
    raise EkHatasi("JSON'da %s geçersiz" % ad)


def _json(ham: bytes) -> Any:
    try:
        return json.loads(ham.decode("utf-8"), object_pairs_hook=_cift_anahtar, parse_constant=_sayi_degil)
    except EkHatasi:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise EkHatasi("JSON okunamadı: %s" % str(exc)[:120]) from None


def _gz_oku(yol: str, sikisik: int, acik: int) -> bytes:
    if os.path.islink(yol) or not os.path.isfile(yol):
        raise EkHatasi("düzenli dosya değil")
    boyut = os.path.getsize(yol)
    if boyut > sikisik:
        raise EkHatasi("dosya çok büyük (%d bayt, sınır %d)" % (boyut, sikisik))
    try:
        with gzip.open(yol, "rb") as fh:
            veri = fh.read(acik + 1)
    except (OSError, EOFError, zlib.error) as exc:
        raise EkHatasi("gzip okunamadı (yarım ya da bozuk): %s" % str(exc)[:120]) from None
    if len(veri) > acik:
        raise EkHatasi("açılmış içerik %d baytı aşıyor" % acik)
    return veri


def belge_dogrula(belge: Any) -> Dict[str, Any]:
    """Bir belge satırı: tam olarak ALANLAR, metin alanları sınırlı, meta küçük bir nesne."""
    if not isinstance(belge, dict):
        raise EkHatasi("belge satırı nesne değil")
    if set(belge) != set(ALANLAR):
        raise EkHatasi("beklenmeyen alan kümesi (fazla %s, eksik %s)" % (
            sorted(set(belge) - set(ALANLAR))[:5], sorted(set(ALANLAR) - set(belge))[:5]))
    for alan, sinir in _SINIR.items():
        deger = belge[alan]
        if not isinstance(deger, str):
            raise EkHatasi("%s metin değil" % alan)
        if len(deger) > sinir:
            raise EkHatasi("%s çok uzun (%d > %d)" % (alan, len(deger), sinir))
    ref = belge["ref"]
    if not ref or ref != ref.strip() or "\x00" in ref:
        raise EkHatasi("ref boş ya da geçersiz")
    if not isinstance(belge["meta"], dict):
        raise EkHatasi("meta nesne değil")
    if len(json.dumps(belge["meta"], ensure_ascii=False)) > META_SINIRI:
        raise EkHatasi("meta çok büyük")
    return belge


def _tamsayi(deger: Any) -> bool:
    return isinstance(deger, int) and not isinstance(deger, bool)


def _baslik_dogrula(baslik: Any, ad: Optional[str], tarih: str) -> Dict[str, Any]:
    if not isinstance(baslik, dict) or baslik.get("ek") != SURUM:
        raise EkHatasi("başlık satırı yok ya da sürümü bilinmiyor")
    fazla = set(baslik) - _BASLIK_ALANLARI
    if fazla:
        raise EkHatasi("başlıkta beklenmeyen alan: %s" % sorted(fazla)[:5])
    indeks = baslik.get("indeks")
    if not isinstance(indeks, str) or not ARKA_UC.match(indeks):
        raise EkHatasi("başlıkta indeks adı yok")
    if ad and indeks != ad:
        raise EkHatasi("başka indeksin eki (%s, beklenen %s)" % (indeks, ad))
    if baslik.get("tarih") != tarih:
        raise EkHatasi("başlık tarihi %r, dosya adı %s" % (baslik.get("tarih"), tarih))
    if not _tamsayi(baslik.get("belge")) or not 0 <= baslik["belge"] <= BELGE_SINIRI:
        raise EkHatasi("başlıkta belge sayısı geçersiz")
    for alan in ("yeni", "degisen"):
        if alan in baslik and not _tamsayi(baslik[alan]):
            raise EkHatasi("başlıkta %s sayı değil" % alan)
    if "uretim" in baslik and (not isinstance(baslik["uretim"], str) or len(baslik["uretim"]) > 40):
        raise EkHatasi("başlıkta üretim zamanı geçersiz")
    durum = baslik.get("durum", {})
    if not isinstance(durum, dict) or set(durum) - set(DURUM_ANAHTARLARI) or any(
            not isinstance(v, str) or len(v) > 2_000 for v in durum.values()):
        raise EkHatasi("başlıkta durum geçersiz")
    return baslik


def ek_sirasi(dosya_adi: str) -> Tuple[str, int]:
    """Uygulama sırası: tarih, sonra aynı günün sıra numarası (adsız = 1). Düz alfabetik sıra
    ``2026-11-13-2`` dosyasını ``2026-11-13``'ten önce koyardı."""
    eslesme = EK_ADI.match(dosya_adi)
    if not eslesme:
        raise EkHatasi("dosya adı biçime uymuyor: %s" % dosya_adi[:80])
    return eslesme.group(1), int(eslesme.group(2) or 1)


def dosya_oku(yol: str, ad: Optional[str] = None) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Bir ek dosyasını okur ve bütünüyle denetler; uymayan her şeyde EkHatasi."""
    tarih, _ = ek_sirasi(os.path.basename(yol))
    veri = _gz_oku(yol, DOSYA_SINIRI, ACIK_SINIRI)
    if not veri.endswith(b"\n"):
        raise EkHatasi("son satır yarım")
    satirlar = veri[:-1].split(b"\n")
    baslik = _baslik_dogrula(_json(satirlar[0]), ad, tarih)
    belgeler: List[Dict[str, Any]] = []
    refler = set()
    for no, satir in enumerate(satirlar[1:], 2):
        try:
            belge = belge_dogrula(_json(satir))
        except EkHatasi as exc:
            raise EkHatasi("satır %d: %s" % (no, exc)) from None
        if belge["ref"] in refler:
            raise EkHatasi("satır %d: ref iki kez geçiyor" % no)
        refler.add(belge["ref"])
        belgeler.append(belge)
    if len(belgeler) != baslik["belge"]:
        raise EkHatasi("başlık %d belge diyor, dosyada %d var (yarım dosya?)" % (baslik["belge"], len(belgeler)))
    return baslik, belgeler


def kayit_oku(yol: str, ad: Optional[str] = None) -> Dict[str, Any]:
    """Zamanlanmış taramanın kaydı: bilinen ref'ler kurum kurum, metni henüz gelmemiş belgeler tam haliyle."""
    kayit = _json(_gz_oku(yol, KAYIT_SINIRI, KAYIT_ACIK_SINIRI))
    if not isinstance(kayit, dict) or set(kayit) != _KAYIT_ALANLARI or kayit.get("kayit") != SURUM:
        raise EkHatasi("kayıt biçimi bilinmiyor")
    if not isinstance(kayit["indeks"], str) or not ARKA_UC.match(kayit["indeks"]) or (ad and kayit["indeks"] != ad):
        raise EkHatasi("kayıt başka indeksin (%r)" % kayit.get("indeks"))
    if not isinstance(kayit["guncelleme"], str) or len(kayit["guncelleme"]) > 40:
        raise EkHatasi("kayıtta güncelleme zamanı geçersiz")
    kurumlar = kayit["kurumlar"]
    if not isinstance(kurumlar, dict):
        raise EkHatasi("kayıtta kurumlar nesne değil")
    hepsi = set()
    for kurum, bilgi in kurumlar.items():
        if len(kurum) > 64 or not isinstance(bilgi, dict) or set(bilgi) != {"refler", "en_yeni"}:
            raise EkHatasi("kayıtta kurum girdisi geçersiz")
        if not isinstance(bilgi["en_yeni"], str) or len(bilgi["en_yeni"]) > 40 or not isinstance(bilgi["refler"], list):
            raise EkHatasi("kayıtta %s girdisi geçersiz" % kurum[:20])
        for ref in bilgi["refler"]:
            if not isinstance(ref, str) or not ref or len(ref) > _SINIR["ref"] or ref in hepsi:
                raise EkHatasi("kayıtta geçersiz ya da yinelenen ref")
            hepsi.add(ref)
    if not _tamsayi(kayit["belge"]) or kayit["belge"] != len(hepsi):
        raise EkHatasi("kayıt %r belge diyor, %d ref var" % (kayit.get("belge"), len(hepsi)))
    if not isinstance(kayit["metinsiz"], list):
        raise EkHatasi("kayıtta metinsiz liste değil")
    gorulen = set()
    for belge in kayit["metinsiz"]:
        belge_dogrula(belge)
        if belge["ref"] not in hepsi or belge["ref"] in gorulen:
            raise EkHatasi("metinsiz belge ref'i kayıtta yok ya da yinelenmiş")
        gorulen.add(belge["ref"])
    return kayit


# --------------------------------------------------------------------------- #
# Yazma                                                                         #
# --------------------------------------------------------------------------- #
def _satir(nesne: Any) -> bytes:
    return (json.dumps(nesne, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _gz_yaz(yol: str, parcalar: Sequence[bytes]) -> None:
    """Önce geçici dosyaya, sonra yerine: yarım yazılmış dosya hiçbir zaman asıl adı taşımaz."""
    gecici = yol + ".yaziliyor"
    with open(gecici, "wb") as ham:
        with gzip.GzipFile(filename="", mode="wb", fileobj=ham, compresslevel=9, mtime=0) as gz:
            for parca in parcalar:
                gz.write(parca)
    os.replace(gecici, yol)


def dosya_yaz(yol: str, baslik: Dict[str, Any], belgeler: Sequence[Dict[str, Any]]) -> None:
    baslik = dict(baslik, belge=len(belgeler))
    _baslik_dogrula(baslik, None, ek_sirasi(os.path.basename(yol))[0])
    satirlar = [_satir(baslik)]
    for belge in belgeler:
        satirlar.append(_satir({k: belge_dogrula(belge)[k] for k in ALANLAR}))
    _gz_yaz(yol, satirlar)


def kayit_yaz(yol: str, kayit: Dict[str, Any]) -> None:
    _gz_yaz(yol, [json.dumps(kayit, ensure_ascii=False, separators=(",", ":")).encode("utf-8")])
    kayit_oku(yol, kayit["indeks"])           # yazılan okunabiliyor mu


def ek_dosyalari(klasor: str) -> List[str]:
    """Klasördeki ek dosyaları, uygulama sırasıyla. Adı uymayanlar (kayıt dahil) listeye girmez."""
    if not os.path.isdir(klasor):
        return []
    adlar = [a for a in os.listdir(klasor) if EK_ADI.match(a)]
    return [os.path.join(klasor, a) for a in sorted(adlar, key=ek_sirasi)]


def sonraki_ad(mevcut: Sequence[str], tarih: str) -> str:
    """O gün için henüz kullanılmamış ad: 2026-11-13.jsonl.gz, sonra 2026-11-13-2.jsonl.gz ..."""
    var = {os.path.basename(m) for m in mevcut}
    for sira in range(1, 1000):
        ad = "%s.jsonl.gz" % tarih if sira == 1 else "%s-%d.jsonl.gz" % (tarih, sira)
        if ad not in var:
            return ad
    raise EkHatasi("%s için ad kalmadı" % tarih)


# --------------------------------------------------------------------------- #
# İndeks okuma, üretim                                                          #
# --------------------------------------------------------------------------- #
def salt_okunur(yol: str) -> sqlite3.Connection:
    """İndeksi değiştirmeden açar. Bekleyen WAL yoksa ``immutable``: kilit, -shm, yazma yok."""
    if not os.path.isfile(yol):
        raise FileNotFoundError(yol)
    wal = yol + "-wal"
    kip = "mode=ro" if os.path.exists(wal) and os.path.getsize(wal) > 0 else "immutable=1"
    return sqlite3.connect(pathlib.Path(yol).resolve().as_uri() + "?" + kip, uri=True)


def _meta_kanonik(ham: Any) -> str:
    try:
        return json.dumps(json.loads(ham or "{}"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(ham)


def _parmak(degerler: Sequence[Any]) -> str:
    """Bir belgenin bütün alanlarının özeti (meta anahtar sırasından bağımsız)."""
    h = hashlib.sha256()
    for alan, deger in zip(ALANLAR, degerler):
        metin = _meta_kanonik(deger) if alan == "meta" else ("" if deger is None else str(deger))
        h.update(metin.encode("utf-8", "surrogatepass"))
        h.update(b"\x1f")
    return h.hexdigest()


def _ozetler(db: sqlite3.Connection) -> Dict[str, Tuple[int, str]]:
    return {r[1]: (r[0], _parmak(r[1:])) for r in db.execute("SELECT id, %s FROM docs" % ", ".join(ALANLAR))}


def _durum(db: sqlite3.Connection) -> Dict[str, str]:
    try:
        return {r[0]: r[1] for r in db.execute("SELECT k, v FROM state")}
    except sqlite3.Error:
        return {}


def _satirdan(satir: Sequence[Any]) -> Dict[str, Any]:
    belge = {alan: ("" if deger is None else str(deger)) for alan, deger in zip(ALANLAR, satir)}
    try:
        meta = json.loads(belge["meta"] or "{}")
    except ValueError:
        raise EkHatasi("%s: meta JSON değil" % belge["ref"][:80]) from None
    belge["meta"] = meta if isinstance(meta, dict) else {}
    return belge


def belgeleri_getir(db: sqlite3.Connection, kimlikler: Sequence[int]) -> List[Dict[str, Any]]:
    """Kimlik sırasıyla tam belgeler (yeni indeksin sırası: uygulanınca kimlikler de aynı sırada artar)."""
    satirlar: Dict[int, Dict[str, Any]] = {}
    for i in range(0, len(kimlikler), 500):
        parca = list(kimlikler[i:i + 500])
        sql = "SELECT id, %s FROM docs WHERE id IN (%s)" % (", ".join(ALANLAR), ",".join("?" * len(parca)))
        for r in db.execute(sql, parca):
            satirlar[r[0]] = _satirdan(r[1:])
    return [satirlar[k] for k in sorted(kimlikler)]


def uret(taban: str, yeni: str, ad: str, tarih: Optional[str] = None,
         silinene_izin: bool = False) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Tabanda olmayan ya da herhangi bir alanı farklı olan belgeler, yeni indeksteki sırayla.

    Ek silme taşımaz: tabanda olup yenide olmayan ref varsa durur (o fark ancak tam imajla taşınır).
    """
    if not ARKA_UC.match(ad):
        raise EkHatasi("indeks adı geçersiz: %r" % ad)
    tarih = tarih or time.strftime("%Y-%m-%d", time.gmtime())
    t_db, y_db = salt_okunur(taban), salt_okunur(yeni)
    try:
        t_oz, y_oz = _ozetler(t_db), _ozetler(y_db)
        silinen = [r for r in t_oz if r not in y_oz]
        if silinen and not silinene_izin:
            raise EkHatasi("tabanda olup yeni indekste olmayan %d ref var (ör. %s); ek silme taşımaz"
                           % (len(silinen), silinen[0][:80]))
        secilen = [kimlik for ref, (kimlik, parmak) in y_oz.items()
                   if ref not in t_oz or t_oz[ref][1] != parmak]
        belgeler = belgeleri_getir(y_db, secilen)
        yeni_n = sum(1 for b in belgeler if b["ref"] not in t_oz)
        t_durum, y_durum = _durum(t_db), _durum(y_db)
    finally:
        t_db.close()
        y_db.close()
    durum = {k: y_durum[k] for k in DURUM_ANAHTARLARI if k in y_durum and y_durum[k] != t_durum.get(k)}
    durum.setdefault("last_crawl", y_durum.get("last_crawl") or tarih + "T00:00:00Z")
    baslik = {"ek": SURUM, "indeks": ad, "tarih": tarih,
              "uretim": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "belge": len(belgeler), "yeni": yeni_n, "degisen": len(belgeler) - yeni_n, "durum": durum}
    return baslik, belgeler


# --------------------------------------------------------------------------- #
# Uygulama                                                                      #
# --------------------------------------------------------------------------- #
def _tirnak(ad: str) -> str:
    return '"%s"' % ad.replace('"', '""')


class _Sema:
    """Hedef indeksin docs sütunları, dış içerikli FTS5 tabloları ve vektör tablosu."""

    def __init__(self, db: sqlite3.Connection) -> None:
        sutunlar = {r[1] for r in db.execute("PRAGMA table_info(docs)")}
        eksik = [a for a in ALANLAR if a not in sutunlar]
        if eksik:
            raise EkHatasi("indeksin docs tablosunda eksik sütun: %s" % eksik)
        self.fts: List[Tuple[str, List[str]]] = []
        for ad, sql in db.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' "
                                  "AND sql LIKE 'CREATE VIRTUAL TABLE%'").fetchall():
            duz = re.sub(r"\s+", "", sql or "").lower()
            if "usingfts5(" not in duz or not re.search(r"content=['\"]?docs['\"]?[,)]", duz):
                continue
            kolonlar = [r[1] for r in db.execute("PRAGMA table_info(%s)" % _tirnak(ad))]
            if not kolonlar or any(k not in ALANLAR for k in kolonlar):
                raise EkHatasi("%s arama dizininin sütunları beklenen değil: %s" % (ad, kolonlar))
            self.fts.append((ad, kolonlar))
        self.vecs = db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'vecs'").fetchone() is not None


def _ayni(alan: str, eski: Any, yeni: str, meta: Dict[str, Any]) -> bool:
    if alan == "meta":
        try:
            return json.loads(eski or "{}") == meta
        except ValueError:
            return False
    return ("" if eski is None else str(eski)) == yeni


def _fts_sil(db: sqlite3.Connection, ad: str, kolonlar: List[str], kimlik: int, degerler: Dict[str, Any]) -> None:
    db.execute("INSERT INTO %s(%s, rowid, %s) VALUES('delete', ?, %s)" % (
        _tirnak(ad), _tirnak(ad), ", ".join(kolonlar), ", ".join("?" * len(kolonlar))),
        [kimlik] + [degerler[k] for k in kolonlar])


def _fts_ekle(db: sqlite3.Connection, ad: str, kolonlar: List[str], kimlik: int, degerler: Dict[str, Any]) -> None:
    db.execute("INSERT INTO %s(rowid, %s) VALUES(?, %s)" % (_tirnak(ad), ", ".join(kolonlar),
                                                             ", ".join("?" * len(kolonlar))),
               [kimlik] + [degerler[k] for k in kolonlar])


def _belgeleri_isle(db: sqlite3.Connection, sema: _Sema, belgeler: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    sayac = {"yeni": 0, "degisen": 0, "metin_disi": 0, "ayni": 0, "vektor_silinen": 0}
    alanlar = ALANLAR[1:]
    secim = "SELECT id, %s FROM docs WHERE ref = ?" % ", ".join(alanlar)
    ekle = "INSERT INTO docs(%s) VALUES(%s)" % (", ".join(ALANLAR), ", ".join("?" * len(ALANLAR)))
    for belge in belgeler:
        yeni = {a: belge[a] for a in alanlar}
        yeni["meta"] = json.dumps(belge["meta"], ensure_ascii=False)
        satir = db.execute(secim, (belge["ref"],)).fetchone()
        if satir is None:
            kimlik = db.execute(ekle, [belge["ref"]] + [yeni[a] for a in alanlar]).lastrowid
            for ad, kolonlar in sema.fts:
                _fts_ekle(db, ad, kolonlar, kimlik, yeni)
            sayac["yeni"] += 1
            continue
        kimlik, eski = satir[0], dict(zip(alanlar, satir[1:]))
        farkli = [a for a in alanlar if not _ayni(a, eski[a], yeni[a], belge["meta"])]
        if not farkli:
            sayac["ayni"] += 1
            continue
        etkilenen = [(ad, kolonlar) for ad, kolonlar in sema.fts if any(k in farkli for k in kolonlar)]
        for ad, kolonlar in etkilenen:
            _fts_sil(db, ad, kolonlar, kimlik, eski)
        db.execute("UPDATE docs SET %s WHERE id = ?" % ", ".join("%s = ?" % a for a in farkli),
                   [yeni[a] for a in farkli] + [kimlik])
        for ad, kolonlar in etkilenen:
            _fts_ekle(db, ad, kolonlar, kimlik, yeni)
        if any(a in METIN_ALANLARI for a in farkli):
            # Vektör eski metni anlatıyordu; embed_missing yenisini üretir (Index.upsert de böyle yapar).
            if sema.vecs:
                sayac["vektor_silinen"] += db.execute("DELETE FROM vecs WHERE doc_id = ?", (kimlik,)).rowcount
        else:
            sayac["metin_disi"] += 1         # tarih, atıf, durum ...: metin aynı, vektör geçerli
        sayac["degisen"] += 1
    return sayac


def _zaman(deger: str) -> str:
    return (deger or "").replace("Z", "").replace(" ", "T")[:19]


def uygula(indeks: str, klasor: str, ad: str, log: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Klasördeki ekleri sırayla indekse işler. Her dosya kendi işleminde: yarısı uygulanmış dosya olmaz."""
    log = log or (lambda m: sys.stderr.write(m + "\n"))
    t0 = time.time()
    dosyalar = ek_dosyalari(klasor)
    ozet: Dict[str, Any] = {"indeks": ad, "dosya": 0, "yeni": 0, "degisen": 0, "metin_disi": 0, "ayni": 0,
                            "vektor_silinen": 0, "atlanan": []}
    if not dosyalar:
        ozet["sure_sn"] = round(time.time() - t0, 2)
        return ozet
    if not os.path.isfile(indeks):                  # boş bir dosya yaratmasın
        ozet["hata"] = "indeks yok: %s" % indeks
        return ozet
    db = sqlite3.connect(indeks, timeout=120, isolation_level=None)
    try:
        db.execute("PRAGMA journal_mode=WAL")          # sunucu okurken yazılır; okuyucu beklemez
        sema = _Sema(db)
        uygulanan: List[Tuple[Dict[str, Any], set]] = []
        for yol in dosyalar:                            # dosya dosya: bellekte bir dosya durur
            dosya_adi = os.path.basename(yol)
            t1 = time.time()
            try:
                baslik, belgeler = dosya_oku(yol, ad)
            except EkHatasi as exc:
                ozet["atlanan"].append("%s: %s" % (dosya_adi, exc))
                log("ek atlandı: %s: %s" % (dosya_adi, exc))
                continue
            try:
                db.execute("BEGIN IMMEDIATE")
                sayac = _belgeleri_isle(db, sema, belgeler)
                db.execute("COMMIT")
            except sqlite3.Error as exc:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                ozet["atlanan"].append("%s: %s" % (dosya_adi, exc))
                log("ek uygulanamadı: %s: %s" % (dosya_adi, exc))
                continue
            for k, v in sayac.items():
                ozet[k] += v
            ozet["dosya"] += 1
            uygulanan.append((baslik, {b["ref"] for b in belgeler}))
            log("ek %s: %s (%.1f sn)" % (dosya_adi, sayac, time.time() - t1))
        _durumu_yaz(db, uygulanan, ozet["atlanan"])
        db.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except (EkHatasi, sqlite3.Error) as exc:
        ozet["hata"] = "%s: %s" % (type(exc).__name__, exc)
        log("ekler uygulanamadı: %s" % ozet["hata"])
    finally:
        db.close()
    ozet["sure_sn"] = round(time.time() - t0, 2)
    return ozet


def _durumu_yaz(db: sqlite3.Connection, uygulanan: Sequence[Tuple[Dict[str, Any], set]],
                atlanan: Sequence[str]) -> None:
    """state: ``last_crawl`` vb. en yeni ekin taramasına çekilir (taban daha yeniyse dokunulmaz); ``ek``
    status'un gösterdiği özet. Değişmeyen değer yeniden yazılmaz: ikinci uygulama indekse dokunmaz."""
    mevcut = _durum(db)
    hedef = dict(mevcut)
    for baslik, _ in uygulanan:
        durum = baslik.get("durum") or {}
        if _zaman(durum.get("last_crawl", baslik["tarih"])) >= _zaman(hedef.get("last_crawl", "")):
            hedef.update(durum)
    refler = set().union(*(r for _, r in uygulanan)) if uygulanan else set()
    ek = {"files": len(uygulanan), "documents": len(refler),
          "latest": uygulanan[-1][0]["tarih"] if uygulanan else ""}
    if atlanan:
        ek["skipped_files"] = [a[:300] for a in atlanan[:20]]
    hedef["ek"] = json.dumps(ek, ensure_ascii=False, sort_keys=True)
    farkli = {k: v for k, v in hedef.items() if mevcut.get(k) != v}
    if not farkli:
        return
    db.execute("BEGIN IMMEDIATE")
    db.execute("CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
    for k, v in farkli.items():
        db.execute("INSERT OR REPLACE INTO state(k, v) VALUES(?, ?)", (k, v))
    db.execute("COMMIT")


def ek_durumu(deger: str) -> Optional[Dict[str, Any]]:
    """status için: state'teki ``ek`` değeri (yoksa None)."""
    try:
        sonuc = json.loads(deger or "null")
    except ValueError:
        return None
    return sonuc if isinstance(sonuc, dict) else None


# --------------------------------------------------------------------------- #
# Eşdeğerlik                                                                    #
# --------------------------------------------------------------------------- #
def _vektorler(db: sqlite3.Connection) -> Dict[str, str]:
    try:
        return {r[0]: "%s/%s/%s" % (r[1], r[2], hashlib.sha1(r[3]).hexdigest()) for r in db.execute(
            "SELECT d.ref, v.model, v.dim, v.vec FROM vecs v JOIN docs d ON d.id = v.doc_id")}
    except sqlite3.Error:
        return {}


def _sorgu_ifadesi(sorgu: str, onek: bool) -> str:
    kelimeler = [w for w in re.sub(r"[^\w\s]", " ", sorgu).split() if len(w) > 1]
    return " ".join('"%s"%s' % (w, "*" if onek else "") for w in kelimeler)


def _arama(db: sqlite3.Connection, sema: _Sema, sorgu: str) -> Dict[str, Any]:
    sonuc: Dict[str, Any] = {}
    for ad, _ in sema.fts:
        for onek in (False, True):
            ifade = _sorgu_ifadesi(sorgu, onek)
            if not ifade:
                continue
            anahtar = "%s%s" % (ad, "*" if onek else "")
            try:
                say = db.execute("SELECT COUNT(*) FROM %s WHERE %s MATCH ?" % (_tirnak(ad), _tirnak(ad)),
                                 (ifade,)).fetchone()[0]
                ilk = [r[0] for r in db.execute(
                    "SELECT d.ref FROM %s f JOIN docs d ON d.id = f.rowid WHERE %s MATCH ? "
                    "ORDER BY bm25(%s), d.ref LIMIT 20" % (_tirnak(ad), _tirnak(ad), _tirnak(ad)), (ifade,))]
            except sqlite3.OperationalError as exc:
                say, ilk = -1, [str(exc)]
            sonuc[anahtar] = {"eslesen": say, "ilk20": ilk}
    return sonuc


def karsilastir(a_yolu: str, b_yolu: str, sorgular: Sequence[str] = ()) -> Dict[str, Any]:
    """İki indeks aynı mı: belge sayısı, ref kümesi, her belgenin bütün alanları, vektörler, state
    (``ek`` hariç), bütünlük denetimi ve anahtar kelime aramalarının döndürdüğü ref'ler."""
    a, b = salt_okunur(a_yolu), salt_okunur(b_yolu)
    hatalar: List[str] = []
    try:
        a_oz, b_oz = _ozetler(a), _ozetler(b)
        rapor: Dict[str, Any] = {"belge": [len(a_oz), len(b_oz)]}
        yalniz_a, yalniz_b = sorted(set(a_oz) - set(b_oz)), sorted(set(b_oz) - set(a_oz))
        farkli = sorted(r for r in set(a_oz) & set(b_oz) if a_oz[r][1] != b_oz[r][1])
        rapor.update(yalniz_a=len(yalniz_a), yalniz_b=len(yalniz_b), alani_farkli=len(farkli))
        if yalniz_a or yalniz_b or farkli:
            hatalar.append("belgeler farklı: yalnız A %s, yalnız B %s, alanı farklı %s"
                           % (yalniz_a[:3], yalniz_b[:3], farkli[:3]))
        a_vek, b_vek = _vektorler(a), _vektorler(b)
        vek_farki = sorted(set(a_vek.items()) ^ set(b_vek.items()))
        rapor["vektor"] = [len(a_vek), len(b_vek)]
        rapor["vektor_farki"] = len(vek_farki)
        if vek_farki:
            hatalar.append("vektörler farklı: %d (ör. %s)" % (len(vek_farki), vek_farki[0][0][:80]))
        a_dur = {k: v for k, v in _durum(a).items() if k != "ek"}
        b_dur = {k: v for k, v in _durum(b).items() if k != "ek"}
        rapor["durum_ayni"] = a_dur == b_dur
        if a_dur != b_dur:
            hatalar.append("state farklı: %s / %s" % (a_dur, b_dur))
        rapor["butunluk"] = [a.execute("PRAGMA integrity_check").fetchone()[0],
                             b.execute("PRAGMA integrity_check").fetchone()[0]]
        if rapor["butunluk"] != ["ok", "ok"]:
            hatalar.append("integrity_check: %s" % rapor["butunluk"])
        a_sema, b_sema = _Sema(a), _Sema(b)
        rapor["aramalar"] = {}
        for sorgu in sorgular:
            sa, sb = _arama(a, a_sema, sorgu), _arama(b, b_sema, sorgu)
            rapor["aramalar"][sorgu] = {k: v["eslesen"] for k, v in sa.items()}
            if sa != sb:
                hatalar.append("arama farklı: %r" % sorgu)
    finally:
        a.close()
        b.close()
    rapor["hatalar"] = hatalar
    return rapor


# --------------------------------------------------------------------------- #
# Komut satırı                                                                  #
# --------------------------------------------------------------------------- #
def _ad_tahmini(yol: str) -> Optional[str]:
    """``<arka uç>/ek/<dosya>`` düzeninde arka ucun adı."""
    klasor = os.path.dirname(os.path.abspath(yol))
    return os.path.basename(os.path.dirname(klasor)) if os.path.basename(klasor) == "ek" else None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    alt = ap.add_subparsers(dest="komut", required=True)
    u = alt.add_parser("uret", help="iki indeksin farkından ek dosyası")
    u.add_argument("--taban", required=True)
    u.add_argument("--yeni", required=True)
    u.add_argument("--ad", required=True, help="arka uç klasörü, ör. arthur-tr-hukuk-mcp")
    u.add_argument("--cikti", required=True, help="kök klasör; dosya <cikti>/<ad>/ek/ altına yazılır")
    u.add_argument("--tarih", help="YYYY-MM-DD (varsayılan bugün, UTC)")
    u.add_argument("--silinene-izin", action="store_true", help="tabanda olup yenide olmayan ref'i yok say")
    g = alt.add_parser("uygula", help="klasördeki ekleri indekse işler")
    g.add_argument("--indeks", required=True)
    g.add_argument("--klasor", required=True)
    g.add_argument("--ad", required=True)
    d = alt.add_parser("dogrula", help="ek ve kayıt dosyalarını denetler, yazmaz")
    d.add_argument("dosyalar", nargs="+")
    d.add_argument("--ad", help="beklenen indeks adı (varsayılan: <ad>/ek/ klasöründen)")
    k = alt.add_parser("karsilastir", help="iki indeksin eşdeğerliği")
    k.add_argument("a")
    k.add_argument("b")
    k.add_argument("--sorgu", action="append", default=[])
    a = ap.parse_args(argv)

    if a.komut == "uret":
        t0 = time.time()
        baslik, belgeler = uret(a.taban, a.yeni, a.ad, a.tarih, a.silinene_izin)
        klasor = os.path.join(a.cikti, a.ad, "ek")
        os.makedirs(klasor, exist_ok=True)
        yol = os.path.join(klasor, sonraki_ad(ek_dosyalari(klasor), baslik["tarih"]))
        dosya_yaz(yol, baslik, belgeler)
        print(json.dumps({"dosya": yol, "bayt": os.path.getsize(yol), **{k: baslik[k] for k in (
            "belge", "yeni", "degisen", "durum")}, "sure_sn": round(time.time() - t0, 1)}, ensure_ascii=False))
        return 0
    if a.komut == "uygula":
        if not ARKA_UC.match(a.ad):
            ap.error("--ad geçersiz")
        ozet = uygula(a.indeks, a.klasor, a.ad)
        print(json.dumps(ozet, ensure_ascii=False))
        return 1 if ozet["atlanan"] or ozet.get("hata") else 0
    if a.komut == "dogrula":
        hata = 0
        for yol in a.dosyalar:
            ad = a.ad or _ad_tahmini(yol)
            try:
                if os.path.basename(yol) == KAYIT_ADI:
                    kayit = kayit_oku(yol, ad)
                    print("tamam %s: kayıt, %d ref, %d metinsiz" % (yol, kayit["belge"], len(kayit["metinsiz"])))
                else:
                    baslik, _ = dosya_oku(yol, ad)
                    print("tamam %s: %d belge (yeni %s, değişen %s)" % (
                        yol, baslik["belge"], baslik.get("yeni", "?"), baslik.get("degisen", "?")))
            except EkHatasi as exc:
                hata = 1
                print("HATA %s: %s" % (yol, exc))
        return hata
    rapor = karsilastir(a.a, a.b, a.sorgu)
    print(json.dumps(rapor, ensure_ascii=False, indent=1))
    return 1 if rapor["hatalar"] else 0


if __name__ == "__main__":
    sys.exit(main())
