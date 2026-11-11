#!/usr/bin/env python3
"""Zamanlanmış kurum kararı taraması: gömülü indeks olmadan yeni kararları ek dosyası olarak yazar.

GitHub Actions'ta (.github/workflows/kurum-taramasi.yml) haftada iki kez koşar. Gömülü indeks (1,4 GB)
orada yoktur; yerine veri dalındaki kayıt (``ek/kayit.json.gz``) vardır: bilinen her ref kurum kurum ve
Rekabet'te metni henüz gelmemiş kararların tam kaydı. Kayıttan bir iskelet indeks kurulur; ``crawl.py``'nin
yalnız-yeni yolu bilinen ref'i indirmeden atlar ve ref'i bilinmeyen her kaydı yeni sayar. Karar tarihine
bakılmaz: listeler yayım sırasıyla gelir, Rekabet'te karar ile yayım arasında 1-14 ay olabilir. Sonra
Rekabet metin tamamlama adımı (``crawl.py --backfill-text``) sınırlı sayıda adayla koşar. İskeletin
taramadan önceki ve sonraki hâlinin farkı ek dosyasıdır (kök ``indeks_ek.py``); kayıt güncellenir.

    python ek_tarama.py tara --veri DIZIN --cikti DIZIN [--kuru]
    python ek_tarama.py kayit --indeks data/index.db --cikti DIZIN/arthur-tr-hukuk-mcp/ek/kayit.json.gz

``--veri``: veri dalının kopyası (``<veri>/arthur-tr-hukuk-mcp/ek/``: kayıt ve önceki ekler). ``--cikti``
altına yeni ek ve güncel kayıt aynı düzenle yazılır, ``ozet.json`` köke. ``--kuru``: hiçbir şey yazmaz,
yalnız rapor. ``kayit``: bir tam indeksten ilk kayıt (taban yeniden kurulunca da).

Kaynaklara nazik: kurumların kendi hız sınırlayıcıları aynen çalışır, üstüne aynı sunucuya iki istek
arasında en az ``--aralik`` sn (1) beklenir. Sayfa sınırları haftada iki koşuya göre (tazele.sh aylık
koşuya göre ayarlı). Bir kurum hata verirse ötekiler sürer; hiçbiri cevap vermezse çıkış kodu 1.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import crawl  # noqa: E402
import net  # noqa: E402
import retrieval  # noqa: E402
import sources  # noqa: E402
from sources import sigorta_tahkim  # noqa: E402

_spec = importlib.util.spec_from_file_location("indeks_ek", os.path.join(os.path.dirname(HERE), "indeks_ek.py"))
indeks_ek = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(indeks_ek)

AD = "arthur-tr-hukuk-mcp"
# Gövdesi önce başlıkla gelen, metni sonradan tamamlanan kurumlar (tazele.sh: yalnız Rekabet).
METINSIZ_KURUMLAR = ("rekabet",)
# Metin tamamlamada bir koşuda en çok bu kadar aday (en yeni karar tarihinden geriye). 10.11.2026'da
# metni gelmemiş Rekabet kararı 10'du; bir koşuda ~7 yeni karar gelir.
REKABET_ADAY = 40


def plan(kayit: Dict[str, Any], bugun: Optional[time.struct_time] = None) -> List[tuple]:
    """Kurum sırası ve sınırları (tazele.sh ile aynı sıra; sınırlar haftada iki koşuya göre).

    tazele.sh aylık: Rekabet 40 sayfa (~400 karar), KVKK 6 sayfa, BTK 3 sayfa (50'şer). Listeler yayım
    sırasıyla geldiği için yeni kayıt ilk sayfalardadır (10.11.2026: Rekabet'in 28 yeni kararı ilk 3
    sayfadaydı, BTK'nin 3'ü ilk sayfada). SPK bülteni yılın listesinden okunur; Ocak'ın ilk haftasında
    geçen yıl da taranır. Sigorta Tahkim'de kayıttaki en büyük dergi sayısı ve bir sonrakine bakılır
    (henüz çıkmamışsa 404, hata sayılmaz).
    """
    bugun = bugun or time.gmtime()
    yil = bugun.tm_year
    yillar = sorted({time.gmtime(time.mktime(bugun) - 7 * 86400).tm_year, yil})
    sayilar = [int(r.split(":")[1]) for r in kayit["kurumlar"].get("sigorta_tahkim", {}).get("refler", [])
               if r.count(":") >= 2 and r.split(":")[1].isdigit()]
    son = max(sayilar) if sayilar else sigorta_tahkim.LATEST_KNOWN
    return [
        ("kvkk", {"pages": 3}),
        ("btk", {"pages": 2}),
        ("bddk", {}),
        ("spk", {"years": yillar}),
        ("sigorta_tahkim", {"issues": [son, son + 1]}),
        ("rekabet", {"pages": 10}),
        ("epdk", {"fetch_text": True}),
    ]


# --------------------------------------------------------------------------- #
# Kayıt                                                                         #
# --------------------------------------------------------------------------- #
def _metinsiz_kimlikler(db: sqlite3.Connection) -> List[int]:
    """crawl._backfill'in seçtiği satırlar: gövdesi başlıktan en çok _BACKFILL_PAY uzun."""
    yer = ",".join("?" * len(METINSIZ_KURUMLAR))
    return [r[0] for r in db.execute(
        "SELECT id FROM docs WHERE subject IN (%s) AND length(body) <= length(title) + ?" % yer,
        list(METINSIZ_KURUMLAR) + [crawl._BACKFILL_PAY])]


def kayit_uret(db: sqlite3.Connection) -> Dict[str, Any]:
    kurumlar: Dict[str, Dict[str, Any]] = {}
    for kurum, ref, tarih in db.execute("SELECT subject, ref, date FROM docs ORDER BY subject, ref"):
        bilgi = kurumlar.setdefault(kurum or "", {"refler": [], "en_yeni": ""})
        bilgi["refler"].append(ref)
        if (tarih or "") > bilgi["en_yeni"]:
            bilgi["en_yeni"] = tarih
    return {"kayit": indeks_ek.SURUM, "indeks": AD, "guncelleme": _simdi(),
            "belge": sum(len(b["refler"]) for b in kurumlar.values()), "kurumlar": kurumlar,
            "metinsiz": indeks_ek.belgeleri_getir(db, _metinsiz_kimlikler(db))}


def iskelet_kur(kayit: Dict[str, Any], yol: str) -> retrieval.Index:
    """Kayıttan indeks: bilinen ref'ler yalnız ref'iyle (konu boş: metin tamamlama adayı olmaz),
    metni gelmemiş kararlar tam kaydıyla (konusu kurum: aday olurlar)."""
    idx = retrieval.Index(yol)
    metinsiz = {b["ref"] for b in kayit["metinsiz"]}
    idx.db.executemany("INSERT INTO docs(ref) VALUES(?)", (
        (ref,) for bilgi in kayit["kurumlar"].values() for ref in bilgi["refler"] if ref not in metinsiz))
    for belge in kayit["metinsiz"]:
        idx.upsert(belge)
    idx.db.commit()
    return idx


# --------------------------------------------------------------------------- #
# Tarama                                                                        #
# --------------------------------------------------------------------------- #
def _simdi() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class _Sayan(crawl._Tagged):
    """Önekle atlanan (SPK bülteni, Sigorta Tahkim sayısı) kaydı da 'bilinen' sayar: kaynak cevap verdi mi?"""

    def exists_prefix(self, prefix: str) -> bool:
        var = super().exists_prefix(prefix)
        if var:
            self.skipped += 1
        return var


class _Kibar:
    """Bütün kaynak istekleri: kurum başına sayılır, aynı sunucuya iki istek arasında en az ``aralik`` sn."""

    def __init__(self, aralik: float) -> None:
        self.aralik = aralik
        self.kurum = ""
        self.istek: Dict[str, int] = collections.Counter()
        self._son: Dict[str, float] = {}
        self._asil = None

    def __enter__(self) -> "_Kibar":
        self._asil = asil = net.Http.request
        kibar = self

        def request(http, method, path, *args, **kwargs):
            sunucu = urllib.parse.urlsplit(http._url(path)).hostname or ""
            bekle = kibar._son.get(sunucu, -1e9) + kibar.aralik - time.monotonic()
            if bekle > 0:
                time.sleep(bekle)
            kibar._son[sunucu] = time.monotonic()
            kibar.istek[kibar.kurum] += 1
            return asil(http, method, path, *args, **kwargs)

        net.Http.request = request
        return self

    def __exit__(self, *exc: Any) -> None:
        net.Http.request = self._asil


def _gunluk(kurum: str, hatalar: List[str]) -> Callable[[str], None]:
    def log(mesaj: str) -> None:
        sys.stderr.write(mesaj + "\n")
        if " failed" in mesaj:
            # Sigorta Tahkim'in bir sonraki sayısı henüz çıkmadıysa 404 döner: beklenen durum.
            if not (kurum == "sigorta_tahkim" and "404" in mesaj):
                hatalar.append(mesaj[:300])
    return log


def tara(veri: str, cikti: str, kuru: bool = False, tarih: Optional[str] = None, aralik: float = 1.0,
         kaynaklar: Optional[Dict[str, Any]] = None, rekabet_aday: int = REKABET_ADAY) -> Dict[str, Any]:
    t0 = time.time()
    tarih = tarih or time.strftime("%Y-%m-%d", time.gmtime())
    ek_klasoru = os.path.join(veri, AD, "ek")
    kayit_yolu = os.path.join(ek_klasoru, indeks_ek.KAYIT_ADI)
    if not os.path.isfile(kayit_yolu):
        raise indeks_ek.EkHatasi("kayıt yok: %s/ek/%s (veri/ek dalı açılmamış ya da kayıt silinmiş; README "
                                 "'Indexes')" % (AD, indeks_ek.KAYIT_ADI))
    kayit = indeks_ek.kayit_oku(kayit_yolu, AD)
    bilinen = {r for b in kayit["kurumlar"].values() for r in b["refler"]}
    kaynaklar = kaynaklar if kaynaklar is not None else sources.load_all()
    is_klasoru = tempfile.mkdtemp(prefix="ek-tarama-")
    try:
        idx = iskelet_kur(kayit, os.path.join(is_klasoru, "iskelet.db"))
        once = sqlite3.connect(os.path.join(is_klasoru, "once.db"))
        idx.db.backup(once)
        once.close()

        kurumlar: Dict[str, Dict[str, Any]] = {}
        with _Kibar(aralik) as kibar:
            for kurum, secenek in plan(kayit):
                kibar.kurum = kurum
                hatalar: List[str] = []
                t1 = time.time()
                src = kaynaklar.get(kurum)
                sonuc: Dict[str, Any] = {}
                atlanan = 0
                if src is None or not getattr(src, "crawlable", True):
                    hatalar.append("kaynak yüklenemedi: %s" % sources.failed().get(kurum, "?"))
                else:
                    try:
                        sonuc, tagged = crawl.tara_kurum(idx, kurum, src, log=_gunluk(kurum, hatalar),
                                                         tagged=_Sayan(idx, kurum, only_new=True), **secenek)
                        atlanan = tagged.skipped
                    except Exception as exc:  # noqa: BLE001 - bir kurum ötekileri durdurmasın
                        hatalar.append("tarama düştü: %s: %s" % (type(exc).__name__, str(exc)[:200]))
                    idx.db.commit()
                    if not hatalar and not (sonuc or {}).get("indexed") and not atlanan:
                        hatalar.append("kaynak hiç kayıt döndürmedi (erişim engeli ya da sayfa yapısı değişti?)")
                kurumlar[kurum] = {"yeni": 0, "bilinen_atlandi": atlanan, "istek": 0,
                                   "sure_sn": round(time.time() - t1, 1), "hatalar": hatalar}

            kibar.kurum = "rekabet"
            hatalar = kurumlar["rekabet"]["hatalar"]
            t1 = time.time()
            try:
                tamamlama = crawl._backfill(idx, list(METINSIZ_KURUMLAR), rekabet_aday, _gunluk("rekabet", hatalar))
            except Exception as exc:  # noqa: BLE001
                tamamlama = {}
                hatalar.append("metin tamamlama düştü: %s: %s" % (type(exc).__name__, str(exc)[:200]))
            kurumlar["rekabet"]["sure_sn"] = round(kurumlar["rekabet"]["sure_sn"] + time.time() - t1, 1)
            for kurum in kurumlar:
                kurumlar[kurum]["istek"] = kibar.istek.get(kurum, 0)

        idx.set_state("last_crawl", _simdi())
        if any((t or {}).get("metin_eklendi") for t in tamamlama.values()):
            idx.set_state("last_backfill", _simdi())
        idx.db.close()
        baslik, belgeler = indeks_ek.uret(os.path.join(is_klasoru, "once.db"),
                                          os.path.join(is_klasoru, "iskelet.db"), AD, tarih)

        # Yeni kayıt: ref'ler eklenir, metinsiz liste iskeletin son hâlinden.
        yeni_kayit = json.loads(json.dumps(kayit))
        for belge in belgeler:
            if belge["ref"] in bilinen:
                continue
            bilgi = yeni_kayit["kurumlar"].setdefault(belge["subject"], {"refler": [], "en_yeni": ""})
            bilgi["refler"].append(belge["ref"])
            if belge["date"] > bilgi["en_yeni"]:
                bilgi["en_yeni"] = belge["date"]
        for bilgi in yeni_kayit["kurumlar"].values():
            bilgi["refler"].sort()
        iskelet = indeks_ek.salt_okunur(os.path.join(is_klasoru, "iskelet.db"))
        try:
            yeni_kayit["metinsiz"] = indeks_ek.belgeleri_getir(iskelet, _metinsiz_kimlikler(iskelet))
        finally:
            iskelet.close()
        yeni_kayit["belge"] = sum(len(b["refler"]) for b in yeni_kayit["kurumlar"].values())
        yeni_kayit["guncelleme"] = _simdi()
    finally:
        shutil.rmtree(is_klasoru, ignore_errors=True)

    for belge in belgeler:
        bilgi = kurumlar.setdefault(belge["subject"], {"yeni": 0, "istek": 0, "hatalar": []})
        if belge["ref"] in bilinen:
            bilgi["metin_eklenen"] = bilgi.get("metin_eklenen", 0) + 1
        else:
            bilgi["yeni"] += 1
    for kurum, bilgi in kurumlar.items():
        bilgi["en_yeni_tarih"] = yeni_kayit["kurumlar"].get(kurum, {}).get("en_yeni", "")

    dosya = None
    if belgeler and not kuru:
        hedef = os.path.join(cikti, AD, "ek")
        os.makedirs(hedef, exist_ok=True)
        dosya = indeks_ek.sonraki_ad(indeks_ek.ek_dosyalari(ek_klasoru), tarih)
        indeks_ek.dosya_yaz(os.path.join(hedef, dosya), baslik, belgeler)
        indeks_ek.kayit_yaz(os.path.join(hedef, indeks_ek.KAYIT_ADI), yeni_kayit)
    hata_sayisi = sum(len(b.get("hatalar", [])) for b in kurumlar.values())
    return {
        "surum": 1, "indeks": AD, "tarih": tarih, "kuru": kuru, "dosya": dosya,
        "belge": len(belgeler), "yeni": baslik["yeni"], "metin_eklenen": baslik["degisen"],
        "kurumlar": kurumlar, "rekabet_metin_tamamlama": tamamlama.get("rekabet", {}),
        "hata_sayisi": hata_sayisi,
        "cevap_veren_kurum": sum(1 for b in kurumlar.values() if not b.get("hatalar")),
        "istek": sum(b.get("istek", 0) for b in kurumlar.values()), "sure_sn": round(time.time() - t0, 1),
    }


# --------------------------------------------------------------------------- #
# GitHub çıktıları                                                              #
# --------------------------------------------------------------------------- #
def _github_yaz(ozet: Dict[str, Any]) -> None:
    """İş özeti (insan + JSON), REST'le okunabilen bir not (annotation) ve sonraki işlerin girdileri."""
    satir = json.dumps(ozet, ensure_ascii=False, separators=(",", ":"))
    # Notun iletisinde yalnız %, \r, \n kaçırılır (workflow commands).
    print("::notice title=kurum-tarama-ozet::" + satir.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"))
    for kurum, bilgi in ozet["kurumlar"].items():
        for hata in bilgi.get("hatalar", [])[:3]:
            print("::warning title=%s::%s" % (kurum, hata.replace("%", "%25").replace("\n", " ")))
    yol = os.environ.get("GITHUB_STEP_SUMMARY")
    if yol:
        tablo = ["| Kurum | Yeni | Metni gelen | En yeni karar | İstek | Hata |", "|---|---|---|---|---|---|"]
        for kurum, b in sorted(ozet["kurumlar"].items()):
            tablo.append("| %s | %d | %d | %s | %d | %s |" % (
                kurum, b.get("yeni", 0), b.get("metin_eklenen", 0), b.get("en_yeni_tarih", "") or "-",
                b.get("istek", 0), "; ".join(b.get("hatalar", []))[:200].replace("|", "/") or "-"))
        with open(yol, "a", encoding="utf-8") as fh:
            fh.write("## Kurum kararı taraması %s%s\n\n" % (ozet["tarih"], " (kuru)" if ozet["kuru"] else ""))
            fh.write("Ek dosyası: %s, %d belge (yeni %d, metni gelen %d), %d istek, %.0f sn.\n\n" % (
                ozet["dosya"] or "yok", ozet["belge"], ozet["yeni"], ozet["metin_eklenen"], ozet["istek"],
                ozet["sure_sn"]))
            fh.write("\n".join(tablo) + "\n\n```json\n" + json.dumps(ozet, ensure_ascii=False, indent=1) + "\n```\n")
    cikti = os.environ.get("GITHUB_OUTPUT")
    if cikti:
        with open(cikti, "a", encoding="utf-8") as fh:
            fh.write("belge=%d\ndosya=%s\n" % (ozet["belge"] if ozet["dosya"] else 0, ozet["dosya"] or ""))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    alt = ap.add_subparsers(dest="komut", required=True)
    t = alt.add_parser("tara", help="yeni kararları ek dosyası olarak yazar")
    t.add_argument("--veri", required=True, help="veri dalının kopyası")
    t.add_argument("--cikti", required=True)
    t.add_argument("--kuru", action="store_true", help="hiçbir şey yazmadan yalnız rapor")
    t.add_argument("--tarih", help="YYYY-MM-DD (varsayılan bugün, UTC)")
    t.add_argument("--aralik", type=float, default=1.0, help="aynı sunucuya iki istek arası en az sn")
    k = alt.add_parser("kayit", help="bir tam indeksten kayıt")
    k.add_argument("--indeks", required=True)
    k.add_argument("--cikti", required=True)
    a = ap.parse_args(argv)

    if a.komut == "kayit":
        db = indeks_ek.salt_okunur(a.indeks)
        try:
            kayit = kayit_uret(db)
        finally:
            db.close()
        os.makedirs(os.path.dirname(os.path.abspath(a.cikti)), exist_ok=True)
        indeks_ek.kayit_yaz(a.cikti, kayit)
        print(json.dumps({"kayit": a.cikti, "bayt": os.path.getsize(a.cikti), "belge": kayit["belge"],
                          "metinsiz": len(kayit["metinsiz"]),
                          "kurumlar": {k: len(v["refler"]) for k, v in kayit["kurumlar"].items()}},
                         ensure_ascii=False))
        return 0
    os.makedirs(a.cikti, exist_ok=True)
    try:
        ozet = tara(a.veri, a.cikti, a.kuru, a.tarih, a.aralik)
    except indeks_ek.EkHatasi as exc:
        print("::error title=kurum-tarama::%s" % exc)
        return 1
    with open(os.path.join(a.cikti, "ozet.json"), "w", encoding="utf-8") as fh:
        json.dump(ozet, fh, ensure_ascii=False, indent=1)
    _github_yaz(ozet)
    print(json.dumps(ozet, ensure_ascii=False, indent=1))
    return 0 if ozet["cevap_veren_kurum"] else 1


if __name__ == "__main__":
    sys.exit(main())
