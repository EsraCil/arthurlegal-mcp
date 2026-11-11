#!/usr/bin/env python3
"""tazelik — hesap kartlarının Resmî Gazete nöbetçisi ve tazelik kilidi.

Kartlar `veri/tarife_2026.json`daki, Resmî Gazete eklerinden teyit edilmiş tarifeyle hesaplar. Tarife yıl
içinde değişebilir ve kayıtlı veri sessizce eskir. Bu modül her hesap çağrısında verinin hâlâ güncel olup
olmadığını söyler:

* Bu süreçteki son başarılı tarama 24 saatten eskiyse ya da bugünkü sayı henüz taranmadıysa Resmî
  Gazete'nin günlük fihristini ve o günün mükerrer sayılarını tarar (tembel: yalnız bir hesap aracı
  çağrılınca). Aralık: verinin derlendiği gün ile bugünden 7 gün öncesinin geç olanından bugüne. Bitmiş
  ve taranmış günler yeniden istenmez; süreç yeniden başlarsa en çok 8 gün yeniden taranır.
* Başlığı `izlenen.desenler`den birine uyan ve `izlenen.bilinen`de olmayan yayım ilgili kartı kilitler
  (`yeni_duzenleme`): toplam kesin sonuç olarak verilmez. Bulgu süreç boyunca kalır.
* Resmî Gazete'ye ulaşılamazsa ya da tarama tamamlanmazsa `kontrol_edilemedi`: toplam verilir ama
  "teyit edilmedi" diye işaretlenir.
* Kartın verisinin geçerlilik tarihi (`izlenen.gecerlilik`) geçtiyse kart da `yeni_duzenleme` gibi kilitlenir: yeni
  yılın faiz oranı ve kıdem tavanının Temmuz değişikliği fihristte görünmeyebilir, takvim bunu yakalar.

Nezaket: yalnız https://www.resmigazete.gov.tr; istekler arasında en az 1 sn; süreç başına günde en çok
20 istek; aynı anda tek tarama (kilit); başarısız denemeden sonra 30 dk beklenir. Kullanıcının hesap
girdileri hiçbir yere gönderilmez: istenen tek şey tarihli fihrist sayfalarıdır.

Komut satırı (GitHub nöbetçisi, .github/workflows/tarife-nobetcisi.yml):

    python tazelik.py --gun 3 --cikti sonuc.json     # son 3 günü tarar; çıkış kodu her zaman 0

HESAP_RG_NOBETCI=kapali ortam değişkeni ağı kapatır: kartlar `kontrol_edilemedi` döner.
"""

from __future__ import annotations

import argparse
import html as _html
import http.client
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

RG_HOST = "www.resmigazete.gov.tr"
RG_GIRIS = "https://www.resmigazete.gov.tr/"
TR = timezone(timedelta(hours=3), "TSİ")   # Türkiye saati; 2016'dan beri yaz saati uygulanmıyor

GERIYE_GUN = 7                              # derlenme gününden eski olmamak üzere bugünden geriye
TAZELIK_SURESI = timedelta(hours=24)
YENIDEN_DENEME = timedelta(minutes=30)      # başarısız denemeden ya da yayımlanmamış bugünden sonra
ISTEK_ARALIGI = 1.0                         # sn, iki istek arası en az
GUNLUK_ISTEK = 20                           # süreç başına, Türkiye takvim günü
ISTEK_ZAMAN_ASIMI = 10.0                    # sn, tek istek
TARAMA_SURESI = 25.0                        # sn; sunucu içi taramanın üst sınırı (hesap cevabı bunu bekler)
AZAMI_BAYT = 2_000_000
# Kendini açıkça tanıtır. 07.11.2026'da "(compatible; …; +https://…)" biçimli tanıtımı site cevapsız bıraktı (curl ile
# de 15 sn zaman aşımı); bu kısa biçim aynı dakikada cevap aldı.
UA = "Mozilla/5.0 ArthurLegal-hesap/1.0"

_YOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veri", "tarife_2026.json")

Getirici = Callable[[str, float], Tuple[int, bytes]]


class ErisimHatasi(Exception):
    """Resmî Gazete'ye ulaşılamadı, beklenmeyen cevap geldi ya da tarama sınırı doldu."""


# --------------------------------------------------------------------------- ağ
def rg_adresi(url: str) -> bool:
    """Yalnız https://www.resmigazete.gov.tr/... (başka alan, port ya da kullanıcı adı yok)."""
    try:
        p = urlsplit(url)
        return (p.scheme == "https" and (p.hostname or "").lower() == RG_HOST and p.port in (None, 443)
                and not p.username and not p.password)
    except ValueError:
        return False


def rg_normal(url: str) -> str:
    """Resmî Gazete bağlantısını tek biçime getirir (fihrist kimi bağlantıyı http ile verir)."""
    try:
        p = urlsplit(url)
    except ValueError:
        return ""
    if (p.hostname or "").lower() != RG_HOST or p.scheme not in ("http", "https"):
        return ""
    return "https://%s%s%s" % (RG_HOST, p.path, "?" + p.query if p.query else "")


class _Yonlendirme(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        if not rg_adresi(newurl):
            raise ErisimHatasi("Resmî Gazete dışına yönlendirme reddedildi: %s" % newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_ACICI = urllib.request.build_opener(_Yonlendirme())


def http_getir(url: str, zaman_asimi: float = ISTEK_ZAMAN_ASIMI) -> Tuple[int, bytes]:
    """(durum kodu, gövde). HTTP hata kodları döner; ağ hatası ErisimHatasi olur."""
    if not rg_adresi(url):
        raise ErisimHatasi("Resmî Gazete dışı adres reddedildi: %s" % url)
    istek = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.5",
                                                 "Accept-Language": "tr-TR,tr;q=0.9"})
    try:
        with _ACICI.open(istek, timeout=zaman_asimi) as cevap:
            govde = cevap.read(AZAMI_BAYT + 1)
            if len(govde) > AZAMI_BAYT:
                raise ErisimHatasi("Cevap beklenenden büyük: %s" % url)
            return cevap.status, govde
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except ErisimHatasi:
        raise
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:
        neden = getattr(exc, "reason", None) or exc
        raise ErisimHatasi("%s: %s" % (type(exc).__name__, neden)) from None


def _coz(govde: bytes) -> str:
    """Resmî Gazete sayfaları windows-1254'tür; UTF-8 gelirse o."""
    try:
        return govde.decode("utf-8")
    except UnicodeDecodeError:
        return govde.decode("cp1254", errors="replace")


# --------------------------------------------------------------------------- metin
_KATLA = str.maketrans({"ı": "i", "ç": "c", "ğ": "g", "ö": "o", "ş": "s", "ü": "u",
                        "â": "a", "î": "i", "û": "u", "̇": None})


def katla(metin: Optional[str]) -> str:
    """Türkçe katlama: büyük/küçük harf ve şapka farkı yok, 'Harçlar' = 'harclar'."""
    s = (metin or "").replace("İ", "i").replace("I", "ı").lower().translate(_KATLA)
    return " ".join(s.split())


def _duz(parca: str) -> str:
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", parca)).split())


def _rg_tarihi(g: date) -> str:
    return g.strftime("%d.%m.%Y")


def gun_url(g: date, mukerrer: int = 0) -> str:
    return "https://%s/eskiler/%04d/%02d/%04d%02d%02d%s.htm" % (
        RG_HOST, g.year, g.month, g.year, g.month, g.day, "M%d" % mukerrer if mukerrer else "")


# --------------------------------------------------------------------------- fihrist
_OGE = re.compile(r"<(p|div|h\d|b|strong)(\s[^>]*)?>(.*?)</\1\s*>", re.S | re.I)
_BAGLANTI = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
_SAYI = re.compile(r"tarihli ve (\d+) sayili resmi")
_MUKERRER = re.compile(r"^(\d{8})M(\d{1,2})\.htm$", re.I)


def fihrist_ayristir(metin: str, url: str, gun: date) -> Dict[str, Any]:
    """Günlük fihrist: sayı, kalemler (bölüm, başlık, bağlantı) ve sayfada bağlantısı olan mükerrer sayılar.

    Sayfada "… Tarihli ve N Sayılı Resmî Gazete" başlığı yoksa sayfa fihrist sayılmaz (hata sayfası,
    erişim engeli): ErisimHatasi. Böylece tanınmayan bir sayfa "yeni düzenleme yok" diye okunmaz."""
    govde = re.sub(r"<script.*?</script>|<style.*?</style>", " ", metin or "", flags=re.S | re.I)
    m = _SAYI.search(katla(_duz(govde)))
    if not m:
        raise ErisimHatasi("Fihrist tanınmadı (Resmî Gazete başlığı yok): %s" % url)
    bolum = ""
    kalemler: List[Dict[str, str]] = []
    for _etiket, _ozn, ic in _OGE.findall(govde):
        yazi = _duz(ic)
        if not yazi:
            continue
        if yazi.startswith(("––", "--", "–", "—")):
            b = _BAGLANTI.search(ic)
            kalemler.append({"bolum": bolum, "baslik": yazi.lstrip("–—- ").strip(),
                             "url": rg_normal(urljoin(url, _html.unescape(b.group(1)))) if b else ""})
        elif yazi.isupper() and len(yazi) < 80 and "GAZETE" not in yazi:
            bolum = yazi
    gunu = gun.strftime("%Y%m%d")
    mukerrer = set()
    for href in _BAGLANTI.findall(govde):
        tam = urljoin(url, _html.unescape(href))
        ad = urlsplit(tam).path.rsplit("/", 1)[-1]
        e = _MUKERRER.match(ad)
        if e and e.group(1) == gunu and (urlsplit(tam).hostname or "").lower() == RG_HOST:
            mukerrer.add(int(e.group(2)))
    return {"sayi": m.group(1), "kalemler": kalemler, "mukerrerler": sorted(mukerrer)}


# --------------------------------------------------------------------------- desenler
class _Desen:
    """Başlık deseni (katlanmış başlıkta düzenli ifade). `karar`: bölüm ya da başlıkta da aranır;
    `ayrica`: başlıkta ayrıca bulunmalı; `haric`: varsa eşleşmez."""

    def __init__(self, d: Dict[str, Any]) -> None:
        self.kod = d["kod"]
        self.ad = d.get("ad") or d["kod"]
        self.kartlar = list(d.get("kartlar") or [])
        self.baslik = re.compile(d["baslik"])
        self.karar = re.compile(d["karar"]) if d.get("karar") else None
        self.ayrica = re.compile(d["ayrica"]) if d.get("ayrica") else None
        self.haric = re.compile(d["haric"]) if d.get("haric") else None

    def uyar(self, fbolum: str, fbaslik: str) -> bool:
        if not self.baslik.search(fbaslik):
            return False
        if self.karar and not (self.karar.search(fbolum) or self.karar.search(fbaslik)):
            return False
        if self.ayrica and not self.ayrica.search(fbaslik):
            return False
        return not (self.haric and self.haric.search(fbaslik))


def _kunye(k: Dict[str, Any]) -> str:
    if k.get("kunye"):                      # Resmî Gazete'de yayımlanmayan kaynak (ör. ÇSGB kıdem tavanı tablosu)
        return k["kunye"]
    if not k.get("rg_tarih"):
        return k.get("ad") or k.get("kod") or ""
    y, a, g = k["rg_tarih"].split("-")
    return "RG %s.%s.%s, S. %s%s" % (g, a, y, k.get("rg_sayi"),
                                     " (%s. Mükerrer)" % k["mukerrer"] if k.get("mukerrer") else "")


def _tarih_listesi(tarihler: List[str]) -> str:
    if len(tarihler) <= 1:
        return "".join(tarihler)
    return ", ".join(tarihler[:-1]) + " ve " + tarihler[-1]


# --------------------------------------------------------------------------- nöbetçi
class Nobetci:
    """Bir süreçteki tarama durumu. Sunucu tek örnek kullanır (`nobetci()`); testler sahte getiriciyle kurar."""

    def __init__(self, veri: Dict[str, Any], getir: Optional[Getirici] = http_getir,
                 saat: Callable[[], datetime] = lambda: datetime.now(TR),
                 uyu: Callable[[float], None] = time.sleep, sayac: Callable[[], float] = time.monotonic,
                 tarama_suresi: float = TARAMA_SURESI, gunluk_istek: int = GUNLUK_ISTEK,
                 istek_araligi: float = ISTEK_ARALIGI) -> None:
        izlenen = veri.get("izlenen") or {}
        self._desenler = [_Desen(d) for d in izlenen.get("desenler", [])]
        self._bilinen = list(izlenen.get("bilinen", []))
        self._kart_kaynaklari: Dict[str, List[str]] = dict(izlenen.get("kartlar") or {})
        self._kisa_adlar: Dict[str, str] = dict(izlenen.get("kisa_adlar") or {})
        # Kartın verisinin geçerli olduğu son gün: sonrasında yeni dönem verisi (yeni yılın faiz oranı, yeni kıdem tavanı)
        # işlenene kadar kart kilitlenir. Fihristte görünmeyen değişiklikler (genelge, 31 Aralık oranı) için.
        self._gecerlilik: Dict[str, Dict[str, Any]] = dict(izlenen.get("gecerlilik") or {})
        self._kaynaklar = {k["kod"]: k for k in veri.get("kaynaklar", [])}
        d = veri.get("derlenme_tarihi")
        self._derlenme = date.fromisoformat(d) if d else None
        self._getir = getir
        self._saat = saat
        self._uyu = uyu
        self._sayac = sayac
        self._tarama_suresi = tarama_suresi
        self._gunluk_istek = gunluk_istek
        self._istek_araligi = istek_araligi
        self._kilit = threading.Lock()
        self._gunler: Dict[date, Dict[str, Any]] = {}
        self._son_basarili: Optional[datetime] = None
        self._son_hata: Optional[str] = None
        self._hata_zamani: Optional[datetime] = None
        self._sonraki_deneme: Optional[datetime] = None
        self._son_aralik: Optional[Tuple[date, date]] = None
        self._istekler: Dict[date, int] = {}
        self._son_istek: Optional[float] = None
        self._kurulum_hatasi = None if self._desenler else "izlenen yayım desenleri veride yok"
        if getir is None:
            self._kurulum_hatasi = "Resmî Gazete nöbetçisi bu kurulumda kapalı (HESAP_RG_NOBETCI)"

    # ---- zaman
    def _bugun(self, an: Optional[datetime] = None) -> date:
        return (an or self._saat()).astimezone(TR).date()

    def aralik(self, bugun: date) -> Tuple[date, date]:
        bas = bugun - timedelta(days=GERIYE_GUN)
        if self._derlenme and self._derlenme > bas:
            bas = self._derlenme
        return min(bas, bugun), bugun

    def _gerekli(self, simdi: datetime) -> bool:
        if self._kurulum_hatasi:
            return False
        if self._sonraki_deneme and simdi < self._sonraki_deneme:
            return False
        if self._son_basarili is None or simdi - self._son_basarili >= TAZELIK_SURESI:
            return True
        g = self._gunler.get(self._bugun(simdi))
        return g is None or g["durum"] != "tamam"          # bugünkü sayı henüz taranmadı

    # ---- tarama
    def denetle(self) -> None:
        """Tembel denetim: gerekiyorsa tarar. Aynı anda gelen ikinci çağrı ilkinin taramasını bekler."""
        if not self._gerekli(self._saat()):
            return
        if not self._kilit.acquire(timeout=self._tarama_suresi + ISTEK_ZAMAN_ASIMI + 5):
            return
        try:
            simdi = self._saat()
            if self._gerekli(simdi):                         # bu arada başka bir çağrı taradıysa yeniden tarama
                bas, son = self.aralik(self._bugun(simdi))
                self.tara(bas, son)
        finally:
            self._kilit.release()

    def tara(self, bas: date, son: date) -> bool:
        """bas..son günlerini tarar (bitmiş ve taranmış günler atlanır). Başarılıysa True."""
        basla = self._sayac()
        simdi = self._saat()
        bugun = self._bugun(simdi)
        try:
            g = bas
            while g <= son:
                kayit = self._gunler.get(g)
                if not (kayit and kayit["kesin"]):
                    self._gunu_tara(g, bugun, basla)
                g += timedelta(days=1)
            gunler = [self._gunler[bas + timedelta(days=i)] for i in range((son - bas).days + 1)]
            if gunler and all(k["durum"] == "yok" for k in gunler):
                raise ErisimHatasi("Taranan günlerin hiçbirinde Resmî Gazete fihristi bulunamadı (HTTP 404)")
        except ErisimHatasi as exc:
            self._son_hata = str(exc)
            self._hata_zamani = self._saat()
            self._sonraki_deneme = self._hata_zamani + YENIDEN_DENEME
            return False
        self._son_basarili = self._saat()
        self._son_hata = None
        self._son_aralik = (bas, son)
        bugunku = self._gunler.get(bugun)
        yayimlanmadi = son >= bugun and not (bugunku and bugunku["durum"] == "tamam")
        self._sonraki_deneme = self._son_basarili + YENIDEN_DENEME if yayimlanmadi else None
        return True

    def _iste(self, url: str, basla: float) -> Tuple[int, bytes]:
        if self._getir is None:
            raise ErisimHatasi(self._kurulum_hatasi or "nöbetçi kapalı")
        bugun = self._bugun()
        if self._istekler.get(bugun, 0) >= self._gunluk_istek:
            raise ErisimHatasi("Günlük istek sınırı (%d) doldu; tarama tamamlanmadı" % self._gunluk_istek)
        if self._sayac() - basla > self._tarama_suresi:
            raise ErisimHatasi("Tarama süre sınırını (%d sn) aştı; kalan günler sonraki çağrıda taranır"
                               % self._tarama_suresi)
        if self._son_istek is not None:
            bekle = self._istek_araligi - (self._sayac() - self._son_istek)
            if bekle > 0:
                self._uyu(bekle)
        for eski in [t for t in self._istekler if t < bugun]:
            del self._istekler[eski]
        self._istekler[bugun] = self._istekler.get(bugun, 0) + 1
        try:
            return self._getir(url, ISTEK_ZAMAN_ASIMI)
        finally:
            self._son_istek = self._sayac()

    def _gunu_tara(self, g: date, bugun: date, basla: float) -> None:
        url = gun_url(g)
        durum, govde = self._iste(url, basla)
        if durum == 404:                                     # o gün sayı yok ya da henüz yayımlanmadı
            self._gunler[g] = {"durum": "yok", "kesin": False, "kalemler": [], "url": url}
            return
        if durum != 200:
            raise ErisimHatasi("HTTP %d: %s" % (durum, url))
        ana = fihrist_ayristir(_coz(govde), url, g)
        kalemler = [dict(k, mukerrer=None, fihrist=url) for k in ana["kalemler"]]
        for n in ana["mukerrerler"]:
            murl = gun_url(g, n)
            d2, g2 = self._iste(murl, basla)
            if d2 != 200:
                raise ErisimHatasi("HTTP %d: %s" % (d2, murl))
            kalemler += [dict(k, mukerrer=n, fihrist=murl) for k in fihrist_ayristir(_coz(g2), murl, g)["kalemler"]]
        ilgili = []
        for k in kalemler:
            fb, fk = katla(k["bolum"]), katla(k["baslik"])
            eslesen = [d for d in self._desenler if d.uyar(fb, fk)]
            if eslesen:
                ilgili.append(dict(k, desenler=[d.kod for d in eslesen], desen_adi=eslesen[0].ad,
                                   kartlar=sorted({x for d in eslesen for x in d.kartlar})))
        self._gunler[g] = {"durum": "tamam", "kesin": g < bugun, "sayi": ana["sayi"], "url": url,
                           "mukerrerler": ana["mukerrerler"], "kalem_sayisi": len(kalemler), "kalemler": ilgili}

    # ---- sonuç
    def _bilinen_mi(self, k: Dict[str, Any], g: date) -> bool:
        url = rg_normal(k.get("url") or "")
        for b in self._bilinen:
            if url and rg_normal(b.get("url") or "") == url:
                return True
            if b.get("rg_tarih") == g.isoformat() and katla(b.get("baslik")) == katla(k.get("baslik")):
                return True
        return False

    def bulgular(self, kart: Optional[str] = None) -> List[Dict[str, Any]]:
        """Taranan günlerde, desene uyan ve bilinmeyen yayımlar (eskiden yeniye)."""
        out = []
        for g in sorted(self._gunler):
            kayit = self._gunler[g]
            if kayit["durum"] != "tamam":
                continue
            for k in kayit["kalemler"]:
                if self._bilinen_mi(k, g) or (kart and kart not in k["kartlar"]):
                    continue
                rg = "RG %s, S. %s%s" % (_rg_tarihi(g), kayit["sayi"],
                                          " (%d. Mükerrer)" % k["mukerrer"] if k["mukerrer"] else "")
                out.append({"tarih": g.isoformat(), "rg": rg, "baslik": k["baslik"], "bolum": k["bolum"],
                            "url": k["url"] or k["fihrist"], "desen": k["desen_adi"], "kartlar": k["kartlar"]})
        return out

    def donem_sonu(self, kart: Optional[str], bugun: date) -> List[Dict[str, Any]]:
        """Verisinin geçerlilik tarihi geçmiş kartlar: yeni dönem verisi işlenmeden kesin toplam verilmez."""
        out = []
        for k in ([kart] if kart else sorted(self._gecerlilik)):
            g = self._gecerlilik.get(k)
            if not g or not g.get("son"):
                continue
            son = date.fromisoformat(g["son"])
            if bugun > son:
                ertesi = son + timedelta(days=1)
                out.append({"tarih": ertesi.isoformat(), "rg": "Yeni dönem (%s)" % _rg_tarihi(ertesi),
                            "baslik": "%s %s tarihine kadar; %s" % (g.get("ad") or k, _rg_tarihi(son), g.get("neden") or ""),
                            "bolum": "", "url": g.get("url") or RG_GIRIS, "desen": "Veri dönemi sona erdi",
                            "kartlar": [k], "tur": "donem"})
        return out

    def _kaynak_kodlari(self, kart: Optional[str]) -> List[str]:
        if kart:
            return list(self._kart_kaynaklari.get(kart, []))
        out: List[str] = []
        for kodlar in self._kart_kaynaklari.values():
            out += [k for k in kodlar if k not in out]
        return out

    def tazelik(self, kart: Optional[str] = None, denetle: bool = True) -> Dict[str, Any]:
        """Kartın tazelik kaydı: durum, veri kaynakları, son kontrol, yeni yayımlar, UYARI satırı."""
        if denetle:
            try:
                self.denetle()
            except Exception as exc:  # noqa: BLE001 - nöbetçi hatası hesabı düşürmez, kilide döner
                self._son_hata = "%s: %s" % (type(exc).__name__, exc)
                self._hata_zamani = self._saat()
                self._sonraki_deneme = self._hata_zamani + YENIDEN_DENEME
        simdi = self._saat()
        kaynaklar = [self._kaynaklar[k] for k in self._kaynak_kodlari(kart) if k in self._kaynaklar]
        kunyeler = [_kunye(k) for k in kaynaklar]
        veri_kaynaklari = ["%s, %s" % (self._kisa_adlar.get(k["kod"]) or k.get("ad"), _kunye(k)) for k in kaynaklar]
        yeni = self.donem_sonu(kart, self._bugun(simdi)) + self.bulgular(kart)
        son = self._son_basarili.astimezone(TR) if self._son_basarili else None
        son_yazi = son.strftime("%d.%m.%Y %H:%M") if son else None
        out: Dict[str, Any] = {"durum": "guncel", "toplam": "kesin", "veri_kaynaklari": veri_kaynaklari,
                               "son_kontrol": son.isoformat(timespec="minutes") if son else None}
        if self._son_aralik:
            out["taranan"] = "%s–%s" % (_rg_tarihi(self._son_aralik[0]), _rg_tarihi(self._son_aralik[1]))
        out["yeni"] = yeni
        kaynak_satiri = "Kaynak: " + "; ".join(kunyeler) if kunyeler else "Kaynak: hesap verisi"
        hata_yeni = (self._son_hata is not None and (self._son_basarili is None
                                                      or (self._hata_zamani or simdi) >= self._son_basarili))
        if yeni:
            ilk = yeni[0]
            if ilk.get("tur") == "donem":
                uyari = "UYARI: %s; teyidiniz gerekli: %s" % (ilk["baslik"].rstrip(". "), ilk["url"])
            else:
                uyari = ("UYARI: %s tarihli Resmî Gazete'de bu hesabı etkileyebilecek yeni düzenleme var, teyidiniz "
                         "gerekli: %s" % (_rg_tarihi(date.fromisoformat(ilk["tarih"])), ilk["url"]))
            if len(yeni) > 1:
                uyari += " (ayrıca %d yayım daha: tazelik.yeni)" % (len(yeni) - 1)
            out.update(durum="yeni_duzenleme", toplam="verilmedi", uyari=uyari,
                       satir="%s · son kontrol: %s · yeni düzenleme: %s" % (
                           kaynak_satiri, son_yazi or "yapılamadı", ", ".join(y["rg"] for y in yeni)))
        elif self._kurulum_hatasi or hata_yeni or son is None or simdi - self._son_basarili >= TAZELIK_SURESI:
            tarihler = []
            for k in kaynaklar:
                t = _rg_tarihi(date.fromisoformat(k["rg_tarih"])) if k.get("rg_tarih") else None
                if t and t not in tarihler:
                    tarihler.append(t)
            metin = "resmî metne" if len(tarihler) <= 1 else "resmî metinlere"
            bas = ("Resmî Gazete denetimi bu kurulumda kapalı" if self._kurulum_hatasi
                   else "Resmî Gazete'ye bugün ulaşılamadı")
            dayanak = ("%s tarihli %s" % (_tarih_listesi(tarihler), metin)) if tarihler else "verideki resmî kaynağa"
            out.update(durum="kontrol_edilemedi", toplam="teyit edilmedi",
                       neden=self._kurulum_hatasi or self._son_hata or "son başarılı kontrol 24 saatten eski",
                       uyari="UYARI: %s; tutarlar %s göredir, teyidiniz gerekli: %s" % (bas, dayanak, RG_GIRIS),
                       satir="%s · son kontrol: %s" % (
                           kaynak_satiri, ("%s; bugün yapılamadı (teyit edilmedi)" % son_yazi) if son_yazi
                           else "yapılamadı (teyit edilmedi)"))
        else:
            out.update(uyari=None, satir="%s · son kontrol: %s" % (kaynak_satiri, son_yazi))
        return out


# --------------------------------------------------------------------------- sunucu örneği ve kilit
_ORNEK: Optional[Nobetci] = None
_ORNEK_KILIDI = threading.Lock()


def veri_yukle(yol: str = _YOL) -> Dict[str, Any]:
    try:
        with open(yol, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def nobetci() -> Nobetci:
    """Sürecin tek nöbetçisi (ilk çağrıda kurulur)."""
    global _ORNEK
    with _ORNEK_KILIDI:
        if _ORNEK is None:
            kapali = os.environ.get("HESAP_RG_NOBETCI", "").strip().lower() in ("0", "kapali", "kapalı", "off")
            _ORNEK = Nobetci(veri_yukle(), getir=None if kapali else http_getir)
        return _ORNEK


def kur(ornek: Optional[Nobetci]) -> Optional[Nobetci]:
    """Sürecin nöbetçisini değiştirir (testler sahte getiriciyle). None: sonraki çağrıda yeniden kurulur."""
    global _ORNEK
    with _ORNEK_KILIDI:
        _ORNEK = ornek
    return ornek


KESIN_DEGIL = ("Bu tutarlar kesin değildir: Resmî Gazete'de bu hesabı etkileyebilecek yeni düzenleme var. "
               "Teyit edilene kadar toplam olarak sunmayın; UYARI satırını ve bağlantıyı verin.")


def kilitle(sonuc: Dict[str, Any], kart: Optional[str], kilit: bool = True) -> Dict[str, Any]:
    """Hesap sonucuna tazelik kaydını ekler; durum `yeni_duzenleme` ise toplamı kaldırır.

    guncel             → sonuç olduğu gibi, başında `tazelik`.
    kontrol_edilemedi  → başta UYARI; toplam verilir, `tazelik.toplam` = "teyit edilmedi".
    yeni_duzenleme     → başta UYARI; `toplam_tl` (ve vekâlette `asgari_tl`) null; hesap
                         `teyit_bekleyen_tutarlar` altında, kesin olmadığı yazılı.
    kilit=False (işlem menüsü): tazelik ve UYARI eklenir, sonuç değişmez.
    """
    t = nobetci().tazelik(kart)
    if t["durum"] == "guncel":
        return dict({"tazelik": t}, **sonuc)
    if t["durum"] == "kontrol_edilemedi" or not kilit:
        return dict({"UYARI": t["uyari"], "tazelik": t}, **sonuc)
    out: Dict[str, Any] = {"UYARI": t["uyari"], "tazelik": t, "islem": sonuc.get("islem"), "toplam_tl": None}
    if "asgari_tl" in sonuc:
        out["asgari_tl"] = None
    bekleyen = {k: v for k, v in sonuc.items() if k not in ("islem", "veri")}
    out["teyit_bekleyen_tutarlar"] = dict({"not": KESIN_DEGIL}, **bekleyen)
    if "veri" in sonuc:
        out["veri"] = sonuc["veri"]
    return out


# --------------------------------------------------------------------------- komut satırı
def komut(argv: Optional[List[str]] = None, getir: Optional[Getirici] = None,
          saat: Optional[Callable[[], datetime]] = None, uyu: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """Son N günü tarar (derlenme gününe bakmadan) ve sonucu döndürür; GitHub nöbetçisi kullanır."""
    p = argparse.ArgumentParser(prog="tazelik.py", description="Resmî Gazete tarife nöbetçisi (hesap kartları).")
    p.add_argument("--gun", type=int, default=3, help="bugün dahil kaç gün (1-8)")
    p.add_argument("--cikti", help="sonucu bu JSON dosyasına da yaz")
    a = p.parse_args(argv)
    gun = max(1, min(a.gun, GERIYE_GUN + 1))
    n = Nobetci(veri_yukle(), getir=getir or http_getir, saat=saat or (lambda: datetime.now(TR)), uyu=uyu,
                tarama_suresi=120.0)
    bugun = n._bugun()
    bas = bugun - timedelta(days=gun - 1)
    tamam = n.tara(bas, bugun)
    yeni = n.donem_sonu(None, bugun) + n.bulgular()
    sonuc: Dict[str, Any] = {
        "durum": "yeni_duzenleme" if yeni else ("guncel" if tamam else "kontrol_edilemedi"),
        "aralik": [bas.isoformat(), bugun.isoformat()],
        "zaman": n._saat().astimezone(TR).isoformat(timespec="seconds"),
        "taranan_gunler": {g.isoformat(): {"durum": k["durum"], "sayi": k.get("sayi"),
                                           "mukerrerler": k.get("mukerrerler", []), "kalem": k.get("kalem_sayisi", 0)}
                           for g, k in sorted(n._gunler.items())},
        "istek": sum(n._istekler.values()),
        "yeni": yeni,
    }
    if not tamam:
        sonuc["neden"] = n._son_hata
    if a.cikti:
        d = os.path.dirname(os.path.abspath(a.cikti))
        os.makedirs(d, exist_ok=True)
        with open(a.cikti, "w", encoding="utf-8") as fh:
            json.dump(sonuc, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    return sonuc


if __name__ == "__main__":
    s = komut()
    sys.stdout.write(json.dumps(s, ensure_ascii=False, indent=2) + "\n")
