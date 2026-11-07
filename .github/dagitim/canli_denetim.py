"""Canlı uç, Fly dağıtımından önce ve sonra (.github/workflows/fly-dagitim.yml).

    python .github/dagitim/canli_denetim.py olc > once.json
    python .github/dagitim/canli_denetim.py denetle --etiket gh-abc1234 --once once.json --makineler makineler.json
    python .github/dagitim/canli_denetim.py saglik --once once.json

olc      iki uçtaki araç sayısı (JSON).
denetle  dağıtımdan sonra: çalışan her makine yeni imajda, status yeni imajı söylüyor, araç sayısı düşmemiş,
         masa sayfası bu commit'tekiyle bayt bayt aynı, hesap kartları cevap veriyor.
saglik   önceki imaja dönüldükten sonra: iki uç cevap veriyor, araç sayısı düşmemiş.

Yalnız standart kütüphane. Makineler yeni açılmış olabilir: ağ hatasında birkaç kez dener.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

KOK = os.environ.get("CANLI_KOK", "https://arthurlegal-mcp.fly.dev")
UCLAR = ("/mcp", "/directory/mcp")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _istek(yol: str, govde: dict, oturum: str | None = None) -> tuple[dict, str | None]:
    basliklar = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                 "User-Agent": "ArthurLegal-dagitim/1.0"}
    if oturum:
        basliklar["Mcp-Session-Id"] = oturum
    istek = urllib.request.Request(KOK + yol, data=json.dumps(govde).encode(), headers=basliklar)
    with urllib.request.urlopen(istek, timeout=120) as yanit:
        ham = yanit.read().decode("utf-8")
        oturum = yanit.headers.get("Mcp-Session-Id") or oturum
    if ham.lstrip().startswith("{"):
        return json.loads(ham), oturum
    veri = [s[5:].strip() for s in ham.splitlines() if s.startswith("data:")]
    return (json.loads(veri[-1]) if veri else {}), oturum


def _baglan(yol: str) -> str | None:
    _, oturum = _istek(yol, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "dagitim", "version": "1"}}})
    try:
        _istek(yol, {"jsonrpc": "2.0", "method": "notifications/initialized"}, oturum)
    except Exception:  # bildirim cevapsız kalabilir
        pass
    return oturum


def _sonuc(yol: str, oturum: str | None, method: str, params: dict | None = None) -> dict:
    yanit, _ = _istek(yol, {"jsonrpc": "2.0", "id": 2, "method": method, "params": params or {}}, oturum)
    if "result" not in yanit:
        raise RuntimeError(f"{yol} {method}: {json.dumps(yanit, ensure_ascii=False)[:300]}")
    return yanit["result"]


def _dene(islev, kez: int = 6, bekle: int = 20):
    for deneme in range(1, kez + 1):
        try:
            return islev()
        except Exception as hata:  # ağ ya da yeni açılan makine
            if deneme == kez:
                raise
            print(f"  deneme {deneme}: {hata}; {bekle} sn sonra yeniden", file=sys.stderr)
            time.sleep(bekle)


def olc() -> dict[str, int]:
    sayilar = {}
    for yol in UCLAR:
        oturum = _baglan(yol)
        sayilar[yol] = len(_sonuc(yol, oturum, "tools/list")["tools"])
    return sayilar


def karsilastir(once: dict, sonra: dict, azalabilir: bool) -> list[str]:
    hatalar = []
    for yol in UCLAR:
        n = sonra.get(yol, 0)
        if n < 1:
            hatalar.append(f"{yol}: araç listesi boş")
        elif not azalabilir and yol in once and n < once[yol]:
            hatalar.append(f"{yol}: araç sayısı {once[yol]} → {n} düştü")
    return hatalar


def makineler_tamam(makineler: list[dict], etiket: str) -> list[str]:
    calisan = [m for m in makineler if m.get("state") == "started"]
    if not calisan:
        return ["çalışan makine yok"]
    return [f"makine {m.get('id')} başka imajda: {m.get('config', {}).get('image', '?')}"
            for m in calisan if not m.get("config", {}).get("image", "").endswith(":" + etiket)]


def _status_imaji() -> str:
    oturum = _baglan("/mcp")
    sonuc = _sonuc("/mcp", oturum, "tools/call", {"name": "status", "arguments": {}})
    return json.loads(sonuc["content"][0]["text"]).get("build", {}).get("image", "")


def _masa_ayni() -> bool:
    sys.path.insert(0, ROOT)
    from arayuz import al_arayuz  # noqa: E402  (bu commit'in masası)

    beklenen = al_arayuz.gorunum("masa.html")
    oturum = _baglan("/mcp")
    sonuc = _sonuc("/mcp", oturum, "resources/read", {"uri": "ui://arthurlegal/masa.html"})
    return sonuc["contents"][0]["text"] == beklenen


def _hesap_cevap_veriyor() -> bool:
    oturum = _baglan("/mcp")
    sonuc = _sonuc("/mcp", oturum, "tools/call", {"name": "hesap_kalemleri", "arguments": {}})
    return not sonuc.get("isError") and bool(sonuc.get("content"))


def _yaz(satirlar: list[str]) -> None:
    for satir in satirlar:
        print(satir)
    ozet = os.environ.get("GITHUB_STEP_SUMMARY")
    if ozet:
        with open(ozet, "a", encoding="utf-8") as fh:
            fh.write("\n".join(f"- {s}" for s in satirlar) + "\n")


def denetle(etiket: str, once: dict, makineler: list[dict] | None, azalabilir: bool) -> list[str]:
    hatalar: list[str] = []
    if makineler is not None:
        hatalar += makineler_tamam(makineler, etiket)
    sonra = _dene(olc)
    hatalar += karsilastir(once, sonra, azalabilir)
    # Her bağlantı bir makineye düşer; birkaç kez sorunca ikisi de görülür.
    imajlar = {_dene(_status_imaji) for _ in range(4)}
    if imajlar != {etiket}:
        hatalar.append(f"status imajı {sorted(imajlar)}, beklenen {etiket}")
    if not _dene(_masa_ayni):
        hatalar.append("masa sayfası bu commit'tekiyle aynı değil")
    if not _dene(_hesap_cevap_veriyor):
        hatalar.append("hesap_kalemleri cevap vermedi")
    _yaz([f"araç sayısı önce {once or '-'}, sonra {sonra}", f"imaj {etiket}"] +
         ([f"HATA: {h}" for h in hatalar] or ["canlı denetim tamam"]))
    return hatalar


def saglik(once: dict) -> list[str]:
    sonra = _dene(olc)
    hatalar = karsilastir(once, sonra, False)
    _yaz([f"geri dönüş sonrası araç sayısı {sonra}"] + ([f"HATA: {h}" for h in hatalar] or ["canlı uç cevap veriyor"]))
    return hatalar


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("islem", choices=("olc", "denetle", "saglik"))
    ap.add_argument("--etiket", help="yeni imajın etiketi (denetle)")
    ap.add_argument("--once", help="olc çıktısı (JSON dosyası)")
    ap.add_argument("--makineler", help="flyctl machines list --json çıktısı")
    ap.add_argument("--arac-azalabilir", action="store_true", help="araç sayısı bilerek azaldı")
    a = ap.parse_args(argv)

    if a.islem == "olc":
        print(json.dumps(_dene(olc)))
        return 0
    once = {}
    if a.once and os.path.exists(a.once):
        with open(a.once, encoding="utf-8") as fh:
            once = json.loads(fh.read() or "{}")
    if a.islem == "saglik":
        return 1 if saglik(once) else 0
    if not a.etiket:
        ap.error("denetle --etiket ister")
    makineler = None
    if a.makineler:
        with open(a.makineler, encoding="utf-8") as fh:
            makineler = json.load(fh)
    return 1 if denetle(a.etiket, once, makineler, a.arac_azalabilir) else 0


if __name__ == "__main__":
    sys.exit(main())
