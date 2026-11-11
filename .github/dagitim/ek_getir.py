"""Veri dalındaki ek dosyalarını, yalnız onları, bir klasöre getirir (Fly dağıtımı ve kurum taraması).

    python .github/dagitim/ek_getir.py --hedef .                              # dağıtım: <arka uç>/ek/ kodun yanına
    python .github/dagitim/ek_getir.py --hedef "$RUNNER_TEMP/veri" --gerekli  # tarama: dal şart

Dal (``veri/ek``) yoksa hiçbir şey getirmez, çıkış 0 (``--gerekli`` ile 1): dağıtım eksiz sürer. Dal var ama
alınamıyorsa ya da getirilen bir dosya denetimden geçmiyorsa çıkış 1: dağıtım durur, indeks sessizce eskiye
dönmez.

Daldan yalnız ``<arka uç>/ek/YYYY-MM-DD[-N].jsonl.gz`` ve ``<arka uç>/ek/kayit.json.gz`` alınır; yalnız düzenli
dosya kipinde (100644: yürütülebilir değil, bağ değil) ve yalnız bu depoda klasörü olan arka uçlar için. Başka
her yol yok sayılır. Dal çalışma ağacına alınmaz (checkout yok): dosyalar git nesnelerinden tek tek, boyutu
denetlenerek yazılır, hiçbir şey çalıştırılmaz; sonra master'daki ``indeks_ek.py`` ile biçimleri denetlenir.

GITHUB_OUTPUT varsa: ``sha`` (dalın ucu; dal yoksa boş) ve ``dosya`` (getirilen dosya sayısı).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DAL = "veri/ek"
YEREL_REF = "refs/ek-getir/veri"
_YOL = re.compile(r"^([a-z0-9][a-z0-9-]{0,63})/ek/(\d{4}-\d{2}-\d{2}(?:-[1-9][0-9]{0,2})?\.jsonl\.gz|kayit\.json\.gz)$")

_spec = importlib.util.spec_from_file_location("indeks_ek", os.path.join(ROOT, "indeks_ek.py"))
indeks_ek = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(indeks_ek)


def izinli(yol: str, kip: str, tur: str, kok: str = ROOT) -> str | None:
    """Daldaki bir girdi alınacaksa arka ucun adı, alınmayacaksa None."""
    eslesme = _YOL.match(yol)
    if not eslesme or tur != "blob" or kip != "100644":
        return None
    ad = eslesme.group(1)
    return ad if os.path.isdir(os.path.join(kok, ad)) else None


def _git(kok: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=kok, capture_output=True)


class Hata(Exception):
    pass


def getir(hedef: str, gerekli: bool, kok: str = ROOT) -> dict:
    """``kok``: kodun çalışma ağacı (git deposu; ``origin`` veri dalının da kaynağı)."""
    uzak = _git(kok, "ls-remote", "--exit-code", "--heads", "origin", "refs/heads/" + DAL)
    if uzak.returncode == 2:
        if gerekli:
            raise Hata("%s dalı yok (README 'Indexes': veri dalının açılması)" % DAL)
        return {"dal": False, "sha": "", "dosyalar": []}
    if uzak.returncode != 0:
        raise Hata("%s dalı sorgulanamadı: %s" % (DAL, uzak.stderr.decode(errors="replace").strip()[:300]))
    al = _git(kok, "fetch", "--no-tags", "--depth=1", "origin", "+refs/heads/%s:%s" % (DAL, YEREL_REF))
    if al.returncode != 0:
        raise Hata("%s dalı alınamadı: %s" % (DAL, al.stderr.decode(errors="replace").strip()[:300]))
    sha = _git(kok, "rev-parse", YEREL_REF).stdout.decode().strip()
    agac = _git(kok, "ls-tree", "-r", "-z", "--full-tree", sha)
    if agac.returncode != 0:
        raise Hata("dal ağacı okunamadı")
    alinan, yok_sayilan = [], []
    for girdi in agac.stdout.decode("utf-8", "surrogateescape").split("\0"):
        if not girdi:
            continue
        bilgi, yol = girdi.split("\t", 1)
        kip, tur, nesne = bilgi.split()
        ad = izinli(yol, kip, tur, kok)
        if ad is None:
            yok_sayilan.append(yol)
            continue
        kayit = yol.endswith("/" + indeks_ek.KAYIT_ADI)
        sinir = indeks_ek.KAYIT_SINIRI if kayit else indeks_ek.DOSYA_SINIRI
        boyut = int(_git(kok, "cat-file", "-s", nesne).stdout or b"0")
        if boyut > sinir:
            raise Hata("%s çok büyük (%d bayt)" % (yol, boyut))
        icerik = _git(kok, "cat-file", "blob", nesne)
        if icerik.returncode != 0 or len(icerik.stdout) != boyut:
            raise Hata("%s okunamadı" % yol)
        cikti = os.path.join(hedef, *yol.split("/"))
        os.makedirs(os.path.dirname(cikti), exist_ok=True)
        with open(cikti, "xb") as fh:                 # var olanın üstüne yazmaz
            fh.write(icerik.stdout)
        try:
            if kayit:
                indeks_ek.kayit_oku(cikti, ad)
            else:
                indeks_ek.dosya_oku(cikti, ad)
        except indeks_ek.EkHatasi as exc:
            raise Hata("%s denetimden geçmedi: %s" % (yol, exc)) from None
        alinan.append(yol)
    return {"dal": True, "sha": sha, "dosyalar": alinan, "yok_sayilan": yok_sayilan}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hedef", required=True)
    ap.add_argument("--gerekli", action="store_true", help="dal yoksa hata")
    a = ap.parse_args(argv)
    try:
        sonuc = getir(os.path.abspath(a.hedef), a.gerekli)
    except Hata as exc:
        print("::error::%s" % exc)
        return 1
    for yol in sonuc.get("yok_sayilan", []):
        print("::warning::veri dalında beklenmeyen yol yok sayıldı: %s" % yol[:200])
    print(json.dumps(sonuc, ensure_ascii=False))
    cikti = os.environ.get("GITHUB_OUTPUT")
    if cikti:
        with open(cikti, "a", encoding="utf-8") as fh:
            fh.write("sha=%s\ndosya=%d\n" % (sonuc["sha"], len(sonuc["dosyalar"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
