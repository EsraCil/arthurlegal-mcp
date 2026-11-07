#!/usr/bin/env python3
"""nobetci — GitHub'daki günlük tarife nöbetçisinin issue adımı (.github/workflows/tarife-nobetcisi.yml).

`tazelik.py --gun 3 --cikti sonuc.json`un sonucunu okur:

* Bilinmeyen ilgili yayım: o yayım için BİR KEZ issue açar. Başlık tarih ve yayım adını taşır; aynı başlıklı
  bir issue (açık ya da kapalı) varsa yenisini açmaz. Kapatılmış bir issue yeniden açılmaz.
* Resmî Gazete'ye erişilemedi: kırmızıya düşmez. Üç farklı günde üst üste erişilemezse tek bir issue açar
  (aynı başlıklı açık issue varsa açmaz). Sayaç `--durum` dosyasındadır (Actions önbelleği).

master'a commit atmaz; yalnız `gh issue` kullanır. `--kuru`: hiçbir şey açmaz, yalnız ne yapılacağını yazar
(dallarda ve PR'larda).

    python nobetci.py --sonuc .nobet/sonuc.json --durum .nobet/durum.json [--kuru]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

ETIKET = "tarife-nobetcisi"
ERISIM_ESIGI = 3
ERISIM_BASLIGI = "Tarife nöbetçisi: Resmî Gazete'ye %d gündür ulaşılamıyor" % ERISIM_ESIGI


def _tr_tarih(iso: str) -> str:
    y, a, g = iso.split("-")
    return "%s.%s.%s" % (g, a, y)


def yayim_basligi(y: Dict[str, Any]) -> str:
    baslik = " ".join(str(y.get("baslik") or "").split())
    if len(baslik) > 180:
        baslik = baslik[:177].rstrip() + "…"
    return "Tarife nöbetçisi: %s %s" % (_tr_tarih(y["tarih"]), baslik)


def yayim_govdesi(y: Dict[str, Any]) -> str:
    kartlar = ", ".join(y.get("kartlar") or []) or "?"
    return "\n".join([
        "Günlük Resmî Gazete taraması, hesap kartlarını etkileyebilecek ve veride bilinmeyen bir yayım buldu.",
        "",
        "- Yayım: %s" % y.get("baslik"),
        "- Künye: %s" % y.get("rg"),
        "- Bölüm: %s" % (y.get("bolum") or "-"),
        "- Bağlantı: %s" % y.get("url"),
        "- Desen: %s · etkilenebilecek kart: %s" % (y.get("desen"), kartlar),
        "",
        "Sunucu bu yayımı gördüğü sürece ilgili kart kesin toplam vermez (`yeni_duzenleme`).",
        "",
        "## Veriye işlenmeli: resmî ekten ikinci okuma",
        "",
        "1. Yayımı ve ekini resmî kaynaktan indirin (`tr_resmi_gazete_getir`; taranmış ek için "
        "`tr_resmi_gazete_ek_sayfa`). SHA-256'yı kaydedin.",
        "2. Hesabı etkileyen her tutarı, oranı ve eşiği ekten tek tek okuyun; taranmış sayfayı görüntüden okuyun.",
        "3. İkinci okuma: aynı değerleri bağımsız olarak bir kez daha okuyun ve veriyle programla karşılaştırın "
        "(`ikinci_okuma` kaydı).",
        "4. `hesap-mcp/veri/tarife_2026.json`: tutarları, `kaynaklar` künyesini ve `derlenme_tarihi`ni güncelleyin; "
        "yayımı `izlenen.bilinen`e ekleyin. Hesabı etkilemiyorsa yalnız `izlenen.bilinen`e notuyla ekleyin.",
        "5. `hesap-mcp/tests/test_hesap.py`'deki sabit tutarları güncelleyin; testler yeşil olunca PR açın.",
        "",
        "Bu issue'yu tarife nöbetçisi açtı; aynı yayım için yenisi açılmaz.",
    ])


def erisim_govdesi(gunler: List[str], neden: Optional[str]) -> str:
    return "\n".join([
        "Günlük tarife nöbetçisi Resmî Gazete'ye üst üste %d gün ulaşamadı: %s." % (
            len(gunler), ", ".join(_tr_tarih(g) for g in gunler)),
        "",
        "- Son hata: %s" % (neden or "-"),
        "- GitHub'ın makineleri yurt dışındadır; resmigazete.gov.tr bu adreslerden gelen isteği engelliyor olabilir.",
        "- Sunucudaki kartlar kendi taramasını yapar; ulaşamazsa sonuç `kontrol_edilemedi` olur ve toplam "
        "\"teyit edilmedi\" diye işaretlenir.",
        "",
        "Yapılacak: Resmî Gazete'yi bir tarayıcıdan açıp sitenin çalıştığını görün. Site çalışıyorsa nöbetçiyi "
        "Türkiye'den erişen bir makinede koşturmayı düşünün.",
    ])


def planla(sonuc: Dict[str, Any], durum: Dict[str, Any], mevcut: List[Dict[str, str]],
           bugun: Optional[str] = None) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """Açılacak issue'lar ve yeni sayaç. `mevcut`: [{title, state}] (etiketli bütün issue'lar)."""
    bugun = bugun or (sonuc.get("aralik") or [None, date.today().isoformat()])[1]
    basliklar = {m.get("title") for m in mevcut}
    acik = {m.get("title") for m in mevcut if str(m.get("state", "")).upper() == "OPEN"}
    ac: List[Dict[str, str]] = []
    gorulen = set()
    for y in sonuc.get("yeni") or []:
        t = yayim_basligi(y)
        if t in basliklar or t in gorulen:
            continue
        gorulen.add(t)
        ac.append({"title": t, "body": yayim_govdesi(y)})
    gunler = list(durum.get("erisilemeyen_gunler") or [])
    if sonuc.get("durum") == "kontrol_edilemedi":
        if bugun not in gunler:
            gunler.append(bugun)
        if len(gunler) >= ERISIM_ESIGI and ERISIM_BASLIGI not in acik:
            ac.append({"title": ERISIM_BASLIGI, "body": erisim_govdesi(gunler[-ERISIM_ESIGI:], sonuc.get("neden"))})
    else:
        gunler = []
    return ac, {"erisilemeyen_gunler": gunler[-10:], "son_durum": sonuc.get("durum"), "son_gun": bugun}


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout


def _oku(yol: str) -> Dict[str, Any]:
    try:
        with open(yol, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _ozet(sonuc: Dict[str, Any], ac: List[Dict[str, str]], kuru: bool) -> str:
    satirlar = ["## Tarife nöbetçisi", "",
                "- Durum: `%s`" % sonuc.get("durum"),
                "- Aralık: %s" % " – ".join(sonuc.get("aralik") or []),
                "- İstek: %s" % sonuc.get("istek")]
    if sonuc.get("neden"):
        satirlar.append("- Neden: %s" % sonuc["neden"])
    for g, k in sorted((sonuc.get("taranan_gunler") or {}).items()):
        satirlar.append("- %s: %s, sayı %s, mükerrer %s, %s kalem" % (
            g, k.get("durum"), k.get("sayi") or "-", k.get("mukerrerler") or "-", k.get("kalem")))
    for y in sonuc.get("yeni") or []:
        satirlar.append("- Yeni: %s %s (%s)" % (y.get("rg"), y.get("baslik"), y.get("url")))
    for i in ac:
        satirlar.append("- %s: %s" % ("Açılırdı (kuru)" if kuru else "Açıldı", i["title"]))
    return "\n".join(satirlar) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="nobetci.py")
    p.add_argument("--sonuc", required=True)
    p.add_argument("--durum", required=True)
    p.add_argument("--kuru", action="store_true", help="issue açma, sayacı yazma; yalnız planı yaz")
    a = p.parse_args(argv)
    sonuc = _oku(a.sonuc)
    if not sonuc:
        sonuc = {"durum": "kontrol_edilemedi", "neden": "tarama sonucu okunamadı"}
    durum = _oku(a.durum)
    mevcut: List[Dict[str, str]] = []
    try:
        if not a.kuru:
            _gh("label", "create", ETIKET, "--color", "B60205", "--force",
                "--description", "Resmî Gazete tarife nöbetçisi")
        mevcut = json.loads(_gh("issue", "list", "--state", "all", "--label", ETIKET, "--limit", "500",
                                "--json", "title,state") or "[]")
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        if not a.kuru:
            sys.stderr.write("gh kullanılamadı: %s\n" % exc)
            return 1
    ac, yeni_durum = planla(sonuc, durum, mevcut)
    if not a.kuru:
        for i in ac:
            _gh("issue", "create", "--title", i["title"], "--body", i["body"], "--label", ETIKET)
        os.makedirs(os.path.dirname(os.path.abspath(a.durum)), exist_ok=True)
        with open(a.durum, "w", encoding="utf-8") as fh:
            json.dump(yeni_durum, fh, ensure_ascii=False, indent=2)
    ozet = _ozet(sonuc, ac, a.kuru)
    sys.stdout.write(ozet)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(ozet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
