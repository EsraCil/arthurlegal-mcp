"""Duman testi: her TR kaynağında bir arama ve bir getir, çalışan bir MCP ucuna karşı.

Amaç, bir kurum adresini ya da belge sunucusunu değiştirdiğinde bunu kullanıcıdan önce
görmek. Her kaynak için sonuç şu sınıflardan biridir:

  TAMAM       arama sonuç verdi, getir metin döndü
  ADRES       istemci "host outside this upstream refused" dedi: kurum başka bir alan
              adına yönlendiriyor ya da belgeyi başka alandan veriyor. Düzeltme: ilgili
              kaynağın Http(...) çağrısına allowed_domains (net.py).
  ERISIM      zaman aşımı, bağlantı hatası, 5xx: kurum o an erişilemez (tekrar dene)
  BOS         arama hiç sonuç döndürmedi (sorgu ya da sayfa yapısı değişmiş olabilir)
  HATA        diğer her şey

Yalnız standart kütüphane; kaynak başına iki istek, sırayla. Sorgu metinleri sabit ve
genel; kişisel veri içermez.

  python duman_testi.py --url https://<uygulama>.fly.dev/mcp --onek tr_
  python duman_testi.py --url http://127.0.0.1:8000/mcp            # arka uç tek başına
  python duman_testi.py ... --json sonuc.json

Çıkış kodu: hepsi TAMAM ise 0; yalnız ERISIM varsa 2; ADRES, BOS ya da HATA varsa 1.
Günlük koşuda sonuçtan issue açan adım: duman_nobetci.py.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

ZAMAN_ASIMI = 200.0


class Istemci:
    def __init__(self, url: str, onek: str = ""):
        self.url, self.onek, self._id = url, onek, 0

    def cagir(self, ad: str, argumanlar: Dict[str, Any]) -> Dict[str, Any]:
        self._id += 1
        govde = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                            "params": {"name": self.onek + ad, "arguments": argumanlar}}).encode("utf-8")
        istek = urllib.request.Request(self.url, govde, {"Content-Type": "application/json",
                                                         "Accept": "application/json, text/event-stream"})
        with urllib.request.urlopen(istek, timeout=ZAMAN_ASIMI) as yanit:
            veri = json.loads(yanit.read())
        if "error" in veri:
            return {"error": "JSON-RPC %s" % (veri["error"].get("message") or veri["error"])}
        sonuc = veri.get("result") or {}
        yapili = sonuc.get("structuredContent")
        if isinstance(yapili, dict):
            return yapili
        metin = "".join(b.get("text", "") for b in sonuc.get("content") or [] if b.get("type") == "text")
        try:
            yapili = json.loads(metin)
        except ValueError:
            return {"error": metin[:300]} if sonuc.get("isError") else {"_metin": metin}
        return yapili if isinstance(yapili, dict) else {"_metin": metin}


def _ilk(sonuc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for anahtar in ("results", "items"):
        liste = sonuc.get(anahtar)
        if isinstance(liste, list) and liste and isinstance(liste[0], dict):
            return liste[0]
    return None


def _son_is_gunu() -> str:
    gun = datetime.date.today() - datetime.timedelta(days=1)
    while gun.weekday() == 6:  # Pazar
        gun -= datetime.timedelta(days=1)
    return gun.isoformat()


# (ad, arama aracı, arama argümanları, getir aracı, ilk sonuçtan getir argümanları)
Kontrol = Tuple[str, str, Callable[[], Dict[str, Any]], str, Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]]

KONTROLLER: List[Kontrol] = [
    ("ictihat", "ictihat_ara", lambda: {"query": "kira tespiti"},
     "ictihat_getir", lambda a, i: {"document_id": i["document_id"]}),
    ("aym", "aym_ara", lambda: {"query": "mülkiyet", "kind": "bireysel"},
     "aym_getir", lambda a, i: {"id": i["id"], "kind": "bireysel"}),
    ("uyusmazlik", "uyusmazlik_ara", lambda: {"query": "görev"},
     "uyusmazlik_getir", lambda a, i: {"document_url": i["document_url"]}),
    ("mevzuat", "mevzuat_madde_getir", lambda: {"number": "6098", "madde_no": "1"}, "", None),
    ("resmi_gazete", "resmi_gazete_fihrist", lambda: {"date": _son_is_gunu()},
     "resmi_gazete_getir", lambda a, i: {"url": i["url"]}),
] + [
    (k, "kurum_karari_ara", (lambda k=k, q=q: dict({"kurum": k, "limit": 1}, **q)),
     "kurum_karari_getir", (lambda a, i, k=k, alan=alan: {"kurum": k, "id": a.get(alan) if alan == "issue" else i[alan]}))
    for k, q, alan in (
        ("rekabet", {"query": "birleşme"}, "karar_id"),
        # EPDK: tek piyasa, üç kategori. Piyasa verilmezse soğuk önbellekte beş piyasa sayfası ve ~126 liste
        # art arda çekilir (sources/epdk.py); günlük denetim kuruma bu kadar istek atmamalı.
        ("epdk", {"market": "elektrik", "params": {"max_categories": 3}}, "id"),
        ("spk", {"query": "bülten"}, "bulten"),
        ("bddk", {"query": "karar"}, "id"),
        ("kvkk", {"query": "veri sorumlusu"}, "id"),
        ("btk", {"query": "tarife"}, "pdf_url"),
        ("gib", {"query": "gelir vergisi"}, "ozelge_no"),
        ("sigorta_tahkim", {"query": "kasko"}, "issue"),
    )
]


def _sinif(hata: str) -> str:
    kucuk = hata.lower()
    # net.Http bu iki reddi "HTTP 0" ile bildirir; erişim hatası sayılmamalı.
    if "outside this upstream refused" in kucuk:
        return "ADRES"
    if "http_max_bytes" in kucuk:
        return "HATA"
    if any(s in kucuk for s in ("timed out", "zaman aşımı", "http 0 ", "http 502", "http 503", "http 504",
                                "connection", "temporarily")):
        return "ERISIM"
    return "HATA"


def kontrol_et(istemci: Istemci, k: Kontrol) -> Dict[str, Any]:
    ad, arama, arama_arg, getir, getir_arg = k
    bas = time.monotonic()
    satir: Dict[str, Any] = {"kaynak": ad}
    try:
        sonuc = istemci.cagir(arama, arama_arg())
        if sonuc.get("error"):
            satir.update(sinif=_sinif(str(sonuc["error"])), adim="arama", ayrinti=str(sonuc["error"])[:300])
            return satir
        if not getir:
            dolu = bool(sonuc.get("items") or sonuc.get("results"))
            satir.update(sinif="TAMAM" if dolu else "BOS", adim="arama")
            return satir
        ilk = _ilk(sonuc)
        if ilk is None and getir_arg is not None and ad != "sigorta_tahkim":
            satir.update(sinif="BOS", adim="arama")
            return satir
        metin = istemci.cagir(getir, getir_arg(sonuc, ilk or {}))
        if metin.get("error"):
            satir.update(sinif=_sinif(str(metin["error"])), adim="getir", ayrinti=str(metin["error"])[:300])
        else:
            satir.update(sinif="TAMAM", adim="getir")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        satir.update(sinif="ERISIM", adim="mcp", ayrinti=str(exc)[:300])
    except (KeyError, TypeError, ValueError) as exc:
        satir.update(sinif="HATA", adim="ayrıştırma", ayrinti="%s: %s" % (type(exc).__name__, exc))
    finally:
        satir["sure_sn"] = round(time.monotonic() - bas, 1)
    return satir


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="TR kaynakları duman testi")
    p.add_argument("--url", default="http://127.0.0.1:8000/mcp", help="MCP ucu (…/mcp)")
    p.add_argument("--onek", default="", help="araç adı öneki; ağ geçidinde tr_")
    p.add_argument("--kaynak", action="append", help="yalnız bu kaynak(lar)")
    p.add_argument("--json", help="sonucu bu dosyaya yaz")
    a = p.parse_args(argv)
    istemci = Istemci(a.url, a.onek)
    satirlar = []
    for k in KONTROLLER:
        if a.kaynak and k[0] not in a.kaynak:
            continue
        s = kontrol_et(istemci, k)
        satirlar.append(s)
        print("%-15s %-7s %6.1fs  %s" % (s["kaynak"], s["sinif"], s["sure_sn"], s.get("ayrinti", "")), flush=True)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"url": a.url, "zaman": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       "sonuclar": satirlar}, f, ensure_ascii=False, indent=1)
    siniflar = {s["sinif"] for s in satirlar}
    if siniflar & {"ADRES", "BOS", "HATA"}:
        return 1
    return 2 if "ERISIM" in siniflar else 0


if __name__ == "__main__":
    sys.exit(main())
