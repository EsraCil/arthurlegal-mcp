#!/usr/bin/env python3
"""duman_nobetci — günlük duman testinin issue adımı (.github/workflows/duman-testi.yml).

`duman_testi.py --json sonuc.json`un sonucunu okur. Kalıp hesap-mcp/nobetci.py ile aynıdır:

* ADRES, BOS ya da HATA: o kaynak için tek bir issue açar; aynı başlıklı açık issue varsa yenisini açmaz.
  Issue kapatıldıktan sonra kaynak yeniden bozulursa yenisi açılır.
* ERISIM: kırmızıya düşmez. Aynı kaynağa üç farklı günde üst üste erişilemezse o kaynak için tek bir issue açar
  (aynı başlıklı açık issue varsa açmaz). Sayaç `--durum` dosyasındadır (Actions önbelleği); kaynak TAMAM
  olunca ya da başka bir sınıfa düşünce sıfırlanır. O gün denenmeyen kaynağın sayacına dokunulmaz.

master'a commit atmaz; yalnız `gh issue` kullanır. `--kuru`: hiçbir şey açmaz, sayacı yazmaz, yalnız ne
yapılacağını yazar (dallarda ve PR'larda).

    python duman_nobetci.py --sonuc sonuc.json --durum .nobet/durum.json [--kuru]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

ETIKET = "duman-testi"
ERISIM_ESIGI = 3
BOZUK = ("ADRES", "BOS", "HATA")
ACIKLAMA = {
    "ADRES": "İstemci alan dışı bir adresi reddetti: kurum başka bir alan adına yönlendiriyor ya da belgeyi başka "
             "alandan veriyor olabilir. Düzeltme: ilgili kaynağın `Http(...)` çağrısına `allowed_domains` (net.py).",
    "BOS": "Arama hiç sonuç döndürmedi: sorgu ya da sayfa yapısı değişmiş olabilir.",
    "HATA": "Arama ya da getir beklenmeyen bir hata verdi.",
}


def _tr_tarih(iso: str) -> str:
    y, a, g = iso.split("-")
    return "%s.%s.%s" % (g, a, y)


def erisim_basligi(kaynak: str) -> str:
    return "Duman testi: %s kaynağına %d gündür ulaşılamıyor" % (kaynak, ERISIM_ESIGI)


def bozuk_basligi(kaynak: str) -> str:
    return "Duman testi: %s kaynağı denetimden geçmedi" % kaynak


def _komut(kaynak: str) -> str:
    return ("`python arthur-tr-hukuk-mcp/duman_testi.py --url https://<uygulama>.fly.dev/mcp --onek tr_ "
            "--kaynak %s`" % kaynak)


def erisim_govdesi(satir: Dict[str, Any], gunler: List[str]) -> str:
    kaynak = str(satir.get("kaynak"))
    return "\n".join([
        "Günlük duman testi `%s` kaynağına üst üste %d gün ulaşamadı: %s." % (
            kaynak, len(gunler), ", ".join(_tr_tarih(g) for g in gunler)),
        "",
        "- Adım: %s" % (satir.get("adim") or "-"),
        "- Son hata: %s" % (satir.get("ayrinti") or "-"),
        "- GitHub'ın makineleri yurt dışındadır; kurum sitesi bu adreslerden gelen isteği engelliyor ya da yavaş "
        "yanıtlıyor olabilir.",
        "",
        "Yapılacak: kurum sitesini bir tarayıcıdan açıp çalıştığını görün. Site çalışıyorsa aynı denetimi canlı "
        "uçta koşturun: %s." % _komut(kaynak),
        "",
        "Bu issue'yu duman testi açtı; açık kaldıkça aynı kaynak için yenisi açılmaz.",
    ])


def bozuk_govdesi(satir: Dict[str, Any], bugun: str) -> str:
    kaynak, sinif = str(satir.get("kaynak")), str(satir.get("sinif"))
    return "\n".join([
        "Günlük duman testinde `%s` kaynağı %s sınıfına düştü (%s)." % (kaynak, sinif, _tr_tarih(bugun)),
        "",
        "- %s" % ACIKLAMA.get(sinif, ACIKLAMA["HATA"]),
        "- Adım: %s" % (satir.get("adim") or "-"),
        "- Ayrıntı: %s" % (satir.get("ayrinti") or "-"),
        "",
        "Yapılacak: aynı denetimi canlı uçta koşturun: %s." % _komut(kaynak),
        "",
        "Bu issue'yu duman testi açtı; açık kaldıkça aynı kaynak için yenisi açılmaz.",
    ])


def planla(sonuc: Dict[str, Any], durum: Dict[str, Any], mevcut: List[Dict[str, str]],
           bugun: Optional[str] = None) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """Açılacak issue'lar ve yeni sayaç. `mevcut`: [{title, state}] (etiketli bütün issue'lar)."""
    bugun = bugun or str(sonuc.get("zaman") or "")[:10] or date.today().isoformat()
    acik = {m.get("title") for m in mevcut if str(m.get("state", "")).upper() == "OPEN"}
    sayac: Dict[str, List[str]] = {str(k): list(v or []) for k, v in (durum.get("erisilemeyen_gunler") or {}).items()}
    ac: List[Dict[str, str]] = []
    for satir in sonuc.get("sonuclar") or []:
        kaynak, sinif = str(satir.get("kaynak")), str(satir.get("sinif"))
        if sinif == "ERISIM":
            gunler = sayac.get(kaynak, [])
            if bugun not in gunler:
                gunler.append(bugun)
            sayac[kaynak] = gunler[-10:]
            baslik = erisim_basligi(kaynak)
            if len(gunler) >= ERISIM_ESIGI and baslik not in acik:
                ac.append({"title": baslik, "body": erisim_govdesi(satir, gunler[-ERISIM_ESIGI:])})
            continue
        sayac.pop(kaynak, None)
        if sinif in BOZUK and bozuk_basligi(kaynak) not in acik:
            ac.append({"title": bozuk_basligi(kaynak), "body": bozuk_govdesi(satir, bugun)})
    return ac, {"erisilemeyen_gunler": sayac, "son_gun": bugun}


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout


def _oku(yol: str) -> Dict[str, Any]:
    try:
        with open(yol, encoding="utf-8") as fh:
            veri = json.load(fh)
    except (OSError, ValueError):
        return {}
    return veri if isinstance(veri, dict) else {}


def _ozet(sonuc: Dict[str, Any], ac: List[Dict[str, str]], yeni_durum: Dict[str, Any], kuru: bool) -> str:
    satirlar = ["## Duman testi: issue adımı", ""]
    if not sonuc.get("sonuclar"):
        satirlar.append("- Test sonucu okunamadı; issue açılmadı, sayaç değişmedi.")
    for kaynak, gunler in sorted((yeni_durum.get("erisilemeyen_gunler") or {}).items()):
        satirlar.append("- %s: üst üste %d gün erişilemedi (%s)" % (kaynak, len(gunler), ", ".join(gunler)))
    for i in ac:
        satirlar.append("- %s: %s" % ("Açılırdı (kuru)" if kuru else "Açıldı", i["title"]))
    if len(satirlar) == 2:
        satirlar.append("- Açılacak issue yok.")
    return "\n".join(satirlar) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="duman_nobetci.py")
    p.add_argument("--sonuc", required=True)
    p.add_argument("--durum", required=True)
    p.add_argument("--kuru", action="store_true", help="issue açma, sayacı yazma; yalnız planı yaz")
    a = p.parse_args(argv)
    sonuc = _oku(a.sonuc)
    durum = _oku(a.durum)
    mevcut: List[Dict[str, str]] = []
    try:
        if not a.kuru:
            _gh("label", "create", ETIKET, "--color", "B60205", "--force",
                "--description", "TR kaynakları günlük duman testi")
        mevcut = json.loads(_gh("issue", "list", "--state", "all", "--label", ETIKET, "--limit", "500",
                                "--json", "title,state") or "[]")
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        if not a.kuru:
            sys.stderr.write("gh kullanılamadı: %s\n" % exc)
            return 1
    if sonuc.get("sonuclar"):
        ac, yeni_durum = planla(sonuc, durum, mevcut)
    else:  # test koşmadı ya da sonucu yazamadı: kırmızı koşu yeter, sayaç olduğu gibi kalır
        ac, yeni_durum = [], durum
    if not a.kuru:
        for i in ac:
            _gh("issue", "create", "--title", i["title"], "--body", i["body"], "--label", ETIKET)
        os.makedirs(os.path.dirname(os.path.abspath(a.durum)), exist_ok=True)
        with open(a.durum, "w", encoding="utf-8") as fh:
            json.dump(yeni_durum, fh, ensure_ascii=False, indent=2)
    ozet = _ozet(sonuc, ac, yeni_durum, a.kuru)
    sys.stdout.write(ozet)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(ozet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
