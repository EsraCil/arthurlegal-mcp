"""Denetim logu (denetim.py) ve mcpcore'un isteğe bağlı kancaları — ağsız, unittest.

    python3 -m unittest tests.test_denetim -v        # depo kökünden

Sahte araçların döndürdüğü sözlükler gerçek arka ucun anahtarlarını taşır
(arthur-tr-hukuk-mcp/sources/bedesten_mevzuat.py: article → _kalemleri_coz/_kalem/_kanun_blogu,
_eski_madde, _dugum_icinde, get → textx.paginate). GercekArkaUcTest aynısını arka ucun KENDİSİNİ
canlı yanıtlardan kısaltılmış fikstürle (tests/fixtures/smk_m120.json) çalıştırarak doğrular:
arka uç anahtar değiştirirse log sessizce boşalmasın, bu test kırılsın.

Kayıtlar her zaman stderr'e yazılır; testler stdout'un boş kaldığını da denetler.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import http.client
import importlib.util
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import types
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if sys.path[:1] != [KOK]:
    sys.path.insert(0, KOK)

import mcpcore  # noqa: E402
import denetim  # noqa: E402
from mcpcore import Dispatcher, McpError, Tool  # noqa: E402

SERVER_PY = os.path.join(KOK, "server.py")
TR_DIZINI = os.path.join(KOK, "arthur-tr-hukuk-mcp")
FIKSTUR = os.path.join(TR_DIZINI, "tests", "fixtures", "smk_m120.json")
# "Kapalıyken yanıtlar bugünküyle aynı" iddiasının tabanı: master'ın (dc1a16d) kök mcpcore.py'si,
# değiştirilmeden. Arka uçlardaki kopyalar MCP Apps, routes ve statik dosyalardan önceki sürümdür,
# taban olamazlar. Kopyanın değişmediğini git blob özeti (satır sonundan bağımsız) denetler.
TABAN_MCPCORE = os.path.join(KOK, "tests", "fixtures", "mcpcore_taban.py")
TABAN_BLOB = "198960e197f71a5e6e9d8cbcae02043c558b0a8f"   # git rev-parse dc1a16d:mcpcore.py
FLY_TOML = os.path.join(KOK, "fly.toml")

TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
KAYIT_ONEKI = '{"olay":'
CAGRI_ALANLARI = ["olay", "ts", "session_id", "request_id", "tool", "params", "resolved",
                  "pages", "mulga", "status", "duration_ms", "sure_siniri_asildi",
                  "backend_version", "returned", "kimlikler"]
IPTAL_ALANLARI = ["olay", "ts", "session_id", "request_id", "reason"]
# server.py'nin denetim anahtarının stderr satırları.
ACIK_SATIRLARI = ("arthurlegal-mcp: denetim logu AÇIK (AUDIT_LOG=1)\n"
                  "arthurlegal-mcp: /directory/mcp sunulmuyor (denetim logu açıkken yalnız /mcp)\n")
CANLI_UYARISI = ("arthurlegal-mcp: AUDIT_LOG=1 canlı uygulamada yok sayıldı "
                 "(FLY_APP_NAME=arthurlegal-mcp); denetim logu KAPALI\n")


# --------------------------------------------------------------------------- #
# Gerçek biçimli sahte sonuçlar                                                #
# --------------------------------------------------------------------------- #
KANUN_BLOGU = {   # _kanun_blogu(); Bedesten mevzuatNo'yu int verir
    "mevzuat_id": "104221", "number": 6769, "title": "SINAİ MÜLKİYET KANUNU", "type": "KANUN",
    "tertip": 5, "rg_date": "2017-01-10", "rg_number": "29944", "mukerrer": "HAYIR",
    "citation_base": "SINAİ MÜLKİYET KANUNU (Kanun No. 6769, RG 10.01.2017/29944)",
    "citation_missing": [],
    "source_url": "https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=6769&MevzuatTur=1&MevzuatTertip=5",
}


def _kalem(madde_no, metin, durum="text", baslik=None, **ek):
    """_kalem() çıktısı (ok=True)."""
    k = {"requested": madde_no, "ok": True, "kind": "madde", "madde_no": madde_no,
         "madde_no_int": None, "suffix": None, "paragraph_hint": None, "heading": baslik,
         "heading_source": "tree" if baslik else None, "text": metin, "truncated": False,
         "chars": len(metin), "status": durum, "annotations": [], "footnotes": {}, "range": None,
         "madde_id": "1651731", "host_madde_id": "1651731", "source": "madde_node",
         "header": metin.split("(")[0].strip(), "fetched_at": "2026-10-08T12:00:00Z",
         "cache_age_s": 0.0,
         "citation": KANUN_BLOGU["citation_base"] + " m. " + madde_no}
    k.update(ek)
    return k


def _hata_kalemi(istenen, kod="NOT_FOUND"):
    """_hata_kalemi() çıktısı (ok=False; madde_no ve status yok)."""
    return {"requested": istenen, "ok": False, "error_code": kod,
            "message": "'%s' bu mevzuatta bulunamadı. EN YAKIN MADDEYİ ATIF OLARAK KULLANMAYIN." % istenen,
            "retry": False, "max_madde_no": 192}


def _madde_sonucu(kalemler, law=KANUN_BLOGU):
    """article() çıktısı."""
    tamam = all(k.get("ok") for k in kalemler)
    out = {"law": law, "complete": tamam, "fetched_at": "2026-10-08T12:00:00Z", "requests": 3,
           "items": kalemler, "note": "Her maddenin citation alanını BİREBİR kullanın…"}
    if not tamam:
        out["incomplete_note"] = "Bazı maddeler getirilemedi (items[].error_code)."
    return out


def _eski_sonuc(metin, dugumdekiler, **ek):
    """_eski_madde() çıktısı (madde_id tek başına)."""
    out = {"madde_id": "1651731", "text": metin, "mime_type": "text/html",
           "fetched_at": "2026-10-08T12:00:00Z", "cache_age_s": 0.0,
           "articles_in_node": dugumdekiler}
    out.update(ek)
    return out


def _sayfa_sonucu(metin, sayfa=1, toplam=1, **ek):
    """get() çıktısı: textx.paginate + kimlik alanları."""
    out = {"page": sayfa, "total_pages": toplam, "chars": len(metin) * toplam,
           "has_more": sayfa < toplam, "text": metin, "mevzuat_id": "102993",
           "mime_type": "text/html", "fetched_at": "2026-10-08T12:00:00Z", "cache_age_s": 0.0}
    out.update(ek)
    return out


def _arac(ad, sonuc=None, hata=None, tool_sinifi=Tool):
    def handler(args):
        if hata is not None:
            raise hata
        return sonuc(args) if callable(sonuc) else sonuc
    return tool_sinifi(ad, "açıklama: " + ad, {"type": "object", "properties": {}}, handler)


# --------------------------------------------------------------------------- #
# HTTP yardımcıları: gerçek ThreadingHTTPServer, ham soket (başlıklar bayt bayt)  #
# --------------------------------------------------------------------------- #
@contextlib.contextmanager
def _sunucu(handler_sinifi):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_sinifi)
    is_parcacigi = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05},
                                    daemon=True)
    is_parcacigi.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        is_parcacigi.join(5)


def _ham_istek(port, yontem, yol, govde=None, basliklar=()):
    if govde is None:
        veri = b""
    elif isinstance(govde, bytes):
        veri = govde
    else:
        veri = json.dumps(govde, ensure_ascii=False).encode("utf-8")
    satirlar = ["%s %s HTTP/1.1" % (yontem, yol), "Host: 127.0.0.1", "Connection: close"]
    if govde is not None:
        satirlar += ["Content-Type: application/json", "Content-Length: %d" % len(veri)]
    satirlar += ["%s: %s" % b for b in basliklar]
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(("\r\n".join(satirlar) + "\r\n\r\n").encode("ascii") + veri)
        parcalar = []
        while True:
            parca = s.recv(65536)
            if not parca:
                break
            parcalar.append(parca)
    return b"".join(parcalar)


def _ayir(ham):
    bas, _, govde = ham.partition(b"\r\n\r\n")
    satirlar = bas.decode("latin-1").split("\r\n")
    basliklar = {}
    for satir in satirlar[1:]:
        k, _, v = satir.partition(":")
        basliklar[k.strip().lower()] = v.strip()
    return int(satirlar[0].split()[1]), basliklar, govde


def _tarihsiz(ham):
    return re.sub(rb"\r\nDate: [^\r\n]*", b"\r\nDate: -", ham)


def _rpc(msg_id, method, **params):
    m = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        m["id"] = msg_id
    if params:
        m["params"] = params
    return m


# --------------------------------------------------------------------------- #
class _Temel(unittest.TestCase):
    """mcpcore ve denetim genellerini her testten sonra eski hâline getirir."""

    def setUp(self):
        self._eski = (mcpcore.SESSION_IDS, mcpcore.BILDIRIM_KANCASI, denetim._AYAR)
        mcpcore._baglam.__dict__.clear()

    def tearDown(self):
        mcpcore.SESSION_IDS, mcpcore.BILDIRIM_KANCASI, denetim._AYAR = self._eski
        mcpcore._baglam.__dict__.clear()

    def ac(self, tools, surumler=None):
        return denetim.etkinlestir(tools, {"tr": "0.5.1", "tkgm": "0.5.2"} if surumler is None
                                   else surumler, "1.0.1")

    @contextlib.contextmanager
    def yakala(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            yield out, err

    @staticmethod
    def kayitlar(akis):
        """Tüketicinin yaptığı gibi: yalnız '{"olay":' ile başlayan satırlar."""
        return [json.loads(s) for s in akis.getvalue().splitlines() if s.startswith(KAYIT_ONEKI)]

    @staticmethod
    def cagir(tools, ad, args, msg_id=1):
        return Dispatcher(tools, "arthurlegal-mcp", "1.0.1").handle(
            _rpc(msg_id, "tools/call", name=ad, arguments=args))

    def tek_cagri(self, ad, sonuc=None, hata=None, args=None, msg_id=1):
        """Tek sahte aracı açık denetimle çağırır; (kayıt, JSON-RPC yanıtı) döndürür."""
        tools = self.ac([_arac(ad, sonuc, hata)])
        with self.yakala() as (out, err):
            yanit = self.cagir(tools, ad, {} if args is None else args, msg_id)
        self.assertEqual(out.getvalue(), "")                 # stdout'a hiçbir şey
        self.assertEqual(len(err.getvalue().splitlines()), 1, err.getvalue())
        (kayit,) = self.kayitlar(err)
        return kayit, yanit


# --------------------------------------------------------------------------- #
# A. Anahtar ve sıfır etki                                                     #
# --------------------------------------------------------------------------- #
class KapaliykenTest(_Temel):

    def test_ice_aktarmak_hicbir_seyi_acmaz(self):
        """AUDIT_LOG=1 ortamda olsa bile mcpcore/denetim'i içe aktarmak hiçbir şeyi açmaz."""
        kod = "import mcpcore, denetim; print(mcpcore.SESSION_IDS, mcpcore.BILDIRIM_KANCASI)"
        ortam = dict(os.environ, AUDIT_LOG="1")
        p = subprocess.run([sys.executable, "-c", kod], cwd=KOK, env=ortam,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "False None")
        self.assertEqual(p.stderr, "")

    def _kapi(self):
        """server.py __main__ bloğundaki AUDIT_LOG anahtarı (gerçek kod, AST'den).

        Dış koşul AUDIT_LOG == "1" (değilse denetim yüklenmez bile); içeride canlı uygulama sorulur.
        """
        with open(SERVER_PY, encoding="utf-8") as f:
            agac = ast.parse(f.read(), SERVER_PY)
        ana = [n for n in agac.body if isinstance(n, ast.If) and "__main__" in ast.unparse(n.test)]
        self.assertEqual(len(ana), 1)
        kapilar = [n for n in ana[0].body
                   if isinstance(n, ast.If) and "AUDIT_LOG" in ast.unparse(n.test)]
        self.assertEqual(len(kapilar), 1)
        kapi = kapilar[0]
        self.assertEqual(ast.unparse(kapi.test), "os.environ.get('AUDIT_LOG') == '1'")
        canli = [n for n in kapi.body if isinstance(n, ast.If)]
        self.assertEqual(len(canli), 1)
        self.assertEqual(ast.unparse(canli[0].test), "denetim.canli_uygulamada()")
        icerde = {id(n) for n in ast.walk(kapi)}
        disarida = [n for n in ast.walk(agac) if id(n) not in icerde and (
            (isinstance(n, ast.Name) and n.id == "denetim")
            or (isinstance(n, ast.Import) and any(a.name == "denetim" for a in n.names))
            or (isinstance(n, ast.ImportFrom) and n.module == "denetim"))]
        self.assertEqual(disarida, [], "denetim server.py'de anahtarın dışında anılmamalı")
        return compile(ast.Module(body=[kapi], type_ignores=[]), SERVER_PY, "exec")

    def _kapiyi_calistir(self, deger, uygulama=None):
        """Anahtarı AUDIT_LOG=deger ile çalıştırır; FLY_APP_NAME yalnız uygulama verilirse vardır."""
        h1, h2 = (lambda a: {"ok": 1}), (lambda a: "durum")
        tools = [Tool("tr_mevzuat_getir", "d1", {"type": "object"}, h1),
                 Tool("status", "d2", {"type": "object"}, h2)]
        onceki = list(tools)
        ad_alani = {"os": os, "sys": sys, "_tools": tools, "_loaded": [], "__version__": "1.0.1",
                    "DIRECTORY_PATH": "/directory/mcp"}
        ortam = {k: v for k, v in os.environ.items() if k not in ("AUDIT_LOG", "FLY_APP_NAME")}
        if deger is not None:
            ortam["AUDIT_LOG"] = deger
        if uygulama is not None:
            ortam["FLY_APP_NAME"] = uygulama
        kod = self._kapi()
        with mock.patch.dict(os.environ, ortam, clear=True), self.yakala() as (out, err):
            exec(kod, ad_alani)
        return onceki, (h1, h2), ad_alani["_tools"], out.getvalue(), err.getvalue()

    def test_server_anahtari_kapaliyken_handlerlar_ayni_nesne(self):
        for deger in (None, "", "0", "true", "yes", " 1", "1 ", "01", "2"):
            with self.subTest(AUDIT_LOG=deger):
                onceki, handlerlar, sonra, out, err = self._kapiyi_calistir(deger)
                self.assertEqual(len(sonra), 2)
                for once, simdi, h in zip(onceki, sonra, handlerlar):
                    self.assertIs(simdi, once)
                    self.assertIs(simdi.handler, h)
                self.assertIs(mcpcore.SESSION_IDS, False)
                self.assertIsNone(mcpcore.BILDIRIM_KANCASI)
                self.assertEqual((out, err), ("", ""))

    def test_server_anahtari_yalniz_1_ile_acar(self):
        onceki, handlerlar, sonra, out, err = self._kapiyi_calistir("1")
        for once, simdi, h in zip(onceki, sonra, handlerlar):
            self.assertIsNot(simdi.handler, h)
            self.assertIs(simdi.handler.__wrapped__, h)
            self.assertIs(simdi.name, once.name)
            self.assertIs(simdi.description, once.description)
            self.assertIs(simdi.input_schema, once.input_schema)
        self.assertIs(mcpcore.SESSION_IDS, True)
        self.assertIs(mcpcore.BILDIRIM_KANCASI, denetim._bildirim)
        self.assertEqual(out, "")
        self.assertEqual(err, ACIK_SATIRLARI)

    def _acik(self, onceki, handlerlar, sonra, out, err):
        for once, simdi, h in zip(onceki, sonra, handlerlar):
            self.assertIs(simdi.handler.__wrapped__, h)
            self.assertIs(simdi.name, once.name)
        self.assertIs(mcpcore.SESSION_IDS, True)
        self.assertIs(mcpcore.BILDIRIM_KANCASI, denetim._bildirim)
        self.assertEqual((out, err), ("", ACIK_SATIRLARI))

    def test_canli_uygulamada_acilmaz_tek_satir_uyari(self):
        """(a) AUDIT_LOG=1 + FLY_APP_NAME=arthurlegal-mcp: kayıt kapalı, uyarı yazılır, süreç sürer."""
        onceki, handlerlar, sonra, out, err = self._kapiyi_calistir("1", "arthurlegal-mcp")
        self.assertEqual(len(sonra), 2)
        for once, simdi, h in zip(onceki, sonra, handlerlar):
            self.assertIs(simdi, once)
            self.assertIs(simdi.handler, h)
        self.assertIs(mcpcore.SESSION_IDS, False)
        self.assertIsNone(mcpcore.BILDIRIM_KANCASI)
        self.assertEqual((out, err), ("", CANLI_UYARISI))
        # AUDIT_LOG yokken canlıda uyarı da yok: blok hiç çalışmaz.
        self.assertEqual(self._kapiyi_calistir(None, "arthurlegal-mcp")[3:], ("", ""))

    def test_baska_uygulamada_acilir(self):
        """(b) AUDIT_LOG=1 + FLY_APP_NAME başka bir uygulama: kayıt açık."""
        self._acik(*self._kapiyi_calistir("1", "baska-uygulama"))

    def test_fly_disinda_acilir(self):
        """(c) AUDIT_LOG=1, FLY_APP_NAME tanımsız (Fly dışı, yerel): kayıt açık."""
        self._acik(*self._kapiyi_calistir("1"))

    @unittest.skipUnless(os.path.exists(FLY_TOML), "fly.toml yok")
    def test_canli_uygulama_adi_fly_tomldaki(self):
        """CANLI_UYGULAMA fly.toml'daki adla aynı kalmalı; ad değişirse kapı boşa düşer."""
        with open(FLY_TOML, encoding="utf-8") as f:
            adlar = re.findall(r'(?m)^app\s*=\s*"([^"]+)"', f.read())
        self.assertEqual(adlar, [denetim.CANLI_UYGULAMA])

    def _ana_blogu_calistir(self, ortam_ek):
        """server.py __main__ bloğunun TAMAMI (gerçek kod, AST'den); ağır parçalar sahte.

        build, _warm_resident, build_directory ve run sahtedir: arka uç yüklenmez, ağa çıkılmaz,
        sunucu açılmaz. Döner: run'a verilen araçlar ve anahtar kelimeler, sahte dizin araçları ve
        görünümler, stdout, stderr.
        """
        with open(SERVER_PY, encoding="utf-8") as f:
            agac = ast.parse(f.read(), SERVER_PY)
        (ana,) = [n for n in agac.body
                  if isinstance(n, ast.If) and "__main__" in ast.unparse(n.test)]
        kod = compile(ast.Module(body=ana.body, type_ignores=[]), SERVER_PY, "exec")
        araclar = [Tool("tr_mevzuat_getir", "d1", {"type": "object"}, lambda a: {"ok": 1}),
                   Tool("status", "d2", {"type": "object"}, lambda a: "durum")]
        dizin_araclari = [araclar[0], Tool("status", "d3", {"type": "object"}, lambda a: "dizin")]
        gorunumler = [mcpcore.Resource("ui://arthurlegal/kart.html", "kart", "<p>kart</p>")]
        cagrilar = []
        ad_alani = {
            "os": os, "sys": sys, "__version__": "1.6.0", "Dispatcher": Dispatcher,
            "DIRECTORY_PATH": "/directory/mcp", "INSTRUCTIONS_HEADER": "talimat",
            "_instructions": [], "_tools": araclar, "_loaded": [], "_failed": [],
            "_resources": gorunumler, "build": lambda: None, "_warm_resident": lambda: None,
            "build_directory": lambda: (dizin_araclari, "dizin talimatı", list(gorunumler)),
            "run": lambda tools, **kw: cagrilar.append((list(tools), kw)),
        }
        ortam = {k: v for k, v in os.environ.items() if k not in ("AUDIT_LOG", "FLY_APP_NAME")}
        ortam.update(ortam_ek)
        with mock.patch.dict(os.environ, ortam, clear=True), self.yakala() as (out, err):
            exec(kod, ad_alani)
        self.assertEqual(len(cagrilar), 1)
        return cagrilar[0] + (dizin_araclari, gorunumler, out.getvalue(), err.getvalue())

    def test_acikken_dizin_yolu_sunulmaz_kapaliyken_sunulur(self):
        """(d) server.py'nin run'a verdiği routes: kayıt açıkken boş (yol 404), kapalıyken dizin."""
        durumlar = (({}, False), ({"AUDIT_LOG": "1", "FLY_APP_NAME": "arthurlegal-mcp"}, False),
                    ({"AUDIT_LOG": "1"}, True),
                    ({"AUDIT_LOG": "1", "FLY_APP_NAME": "baska-uygulama"}, True))
        for ortam, acik in durumlar:
            with self.subTest(**ortam):
                mcpcore.SESSION_IDS, mcpcore.BILDIRIM_KANCASI, denetim._AYAR = self._eski
                araclar, kw, dizin_araclari, gorunumler, out, err = self._ana_blogu_calistir(ortam)
                self.assertEqual(out, "")
                self.assertIs(kw["resources"], gorunumler)         # MCP Apps görünümleri aynen
                self.assertEqual([hasattr(t.handler, "__wrapped__") for t in araclar], [acik] * 2)
                if acik:
                    self.assertEqual(kw["routes"], {})
                    self.assertTrue(err.endswith(ACIK_SATIRLARI), err)
                else:                                              # master'daki davranış
                    self.assertEqual(list(kw["routes"]), ["/directory/mcp"])
                    dizin = kw["routes"]["/directory/mcp"]
                    self.assertIs(dizin.tools, dizin_araclari)
                    self.assertEqual((dizin.instructions, dizin.resources),
                                     ("dizin talimatı", gorunumler))
                    self.assertNotIn("sunulmuyor", err)
                with _sunucu(mcpcore._make_handler(Dispatcher(araclar, "a", "1"),
                                                   kw["routes"])) as port:
                    durum, _, _ = _ayir(_ham_istek(port, "POST", "/directory/mcp",
                                                   _rpc(1, "tools/list")))
                self.assertEqual(durum, 404 if acik else 200)

    def test_kapaliyken_oturum_basligi_yok(self):
        tools = [_arac("tr_mevzuat_getir", {"page": 1, "text": "MADDE 1- a"})]
        istekler = [_rpc(1, "initialize", protocolVersion="2025-06-18"),
                    [_rpc(2, "initialize"), _rpc(3, "ping")],
                    _rpc(4, "tools/call", name="tr_mevzuat_getir", arguments={})]
        with self.yakala() as (out, err), \
                _sunucu(mcpcore._make_handler(Dispatcher(tools, "a", "1"))) as port:
            for govde in istekler:
                durum, basliklar, _ = _ayir(_ham_istek(port, "POST", "/mcp", govde))
                self.assertEqual(durum, 200)
                self.assertNotIn("mcp-session-id", basliklar)
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))     # kayıt yok

    @unittest.skipUnless(os.path.exists(TABAN_MCPCORE), "taban mcpcore kopyası yok")
    def test_kapaliyken_yanitlar_ve_basliklar_bayt_bayt_ayni(self):
        with open(TABAN_MCPCORE, "rb") as f:
            veri = f.read().replace(b"\r\n", b"\n")
        self.assertEqual(hashlib.sha1(b"blob %d\0" % len(veri) + veri).hexdigest(), TABAN_BLOB,
                         "taban kopyası master'daki (dc1a16d) mcpcore.py değil")
        spec = importlib.util.spec_from_file_location("mcpcore_taban", TABAN_MCPCORE)
        taban = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(taban)

        cagri = _rpc(4, "tools/call", name="tr_mevzuat_getir", arguments={"mevzuat_id": "1"})
        istekler = [
            ("POST", "/mcp", _rpc(1, "initialize", protocolVersion="2025-06-18"), ()),
            ("POST", "/mcp", _rpc(None, "notifications/initialized"), ()),
            ("POST", "/mcp", _rpc(2, "tools/list"), ()),
            ("POST", "/mcp", cagri, ()),
            ("POST", "/mcp", cagri, (("Mcp-Session-Id", "istemcinin-kimligi"),)),
            ("POST", "/mcp", _rpc(5, "tools/call", name="hata", arguments={}), ()),
            ("POST", "/mcp", [_rpc(6, "initialize"), _rpc(7, "ping"),
                              _rpc(None, "notifications/cancelled", requestId=4)], ()),
            ("POST", "/mcp", _rpc(None, "notifications/cancelled", requestId=4, reason="x"), ()),
            ("POST", "/mcp", b"{bozuk", ()),
            ("GET", "/health", None, ()),
            ("GET", "/", None, ()),
            ("GET", "/mcp", None, ()),
            # master'ın MCP Apps görünümleri (resources) ve ek yolu (routes)
            ("POST", "/mcp", _rpc(8, "resources/list"), ()),
            ("POST", "/mcp", _rpc(9, "resources/read", uri="ui://arthurlegal/kart.html"), ()),
            ("POST", "/directory/mcp", _rpc(10, "initialize", protocolVersion="2025-06-18"), ()),
            ("POST", "/directory/mcp", cagri, ()),
        ]

        def calistir(modul):
            tools = [_arac("tr_mevzuat_getir", {"page": 1, "text": "MADDE 1- (1) Çalışanın önalım"},
                           tool_sinifi=modul.Tool),
                     _arac("hata", hata=modul.McpError("RATE_LIMITED: HTTP 429"),
                           tool_sinifi=modul.Tool)]
            gorunum = modul.Resource("ui://arthurlegal/kart.html", "kart", "<p>kart</p>",
                                     mime_type="text/html")
            disp = modul.Dispatcher(tools, "arthurlegal-mcp", "1.0.1", instructions="talimat",
                                    resources=[gorunum])
            dizin = modul.Dispatcher(tools[:1], "arthurlegal-mcp", "1.0.1", instructions="dizin")
            with _sunucu(modul._make_handler(disp, {"/directory/mcp": dizin})) as port:
                return [_tarihsiz(_ham_istek(port, y, p, g, b)) for y, p, g, b in istekler]

        with self.yakala() as (out, err):
            yeni, eski = calistir(mcpcore), calistir(taban)
        self.assertEqual(len(yeni), len(istekler))
        for i, (a, b) in enumerate(zip(yeni, eski)):
            self.assertEqual(a, b, "istek %d farklı" % i)
            self.assertNotIn(b"mcp-session-id", a.lower())
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))


# --------------------------------------------------------------------------- #
# B. mcpcore kancaları                                                         #
# --------------------------------------------------------------------------- #
class McpcoreKancaTest(_Temel):

    def test_istek_baglami_varsayilan_bos(self):
        self.assertEqual(mcpcore.istek_baglami(), {"session_id": None, "request_id": None})

    def test_bildirim_kancasi_cagrilir_ve_yanit_yine_yok(self):
        gorulen = []
        mcpcore.BILDIRIM_KANCASI = gorulen.append
        msg = _rpc(None, "notifications/cancelled", requestId=3, reason="kullanıcı")
        self.assertIsNone(Dispatcher([], "a", "1").handle(msg))
        self.assertEqual(gorulen, [msg])

    def test_bildirim_kancasi_hatasi_oturumu_bozmaz(self):
        def bozuk(msg):
            raise RuntimeError("kanca")
        mcpcore.BILDIRIM_KANCASI = bozuk
        with self.yakala() as (out, err):
            sonuc = Dispatcher([], "a", "1").handle(_rpc(None, "notifications/cancelled"))
        self.assertIsNone(sonuc)
        self.assertEqual(err.getvalue(), "mcpcore: notification hook failed (RuntimeError)\n")

    def test_istek_kimligi_handler_icinde_gorunur(self):
        gorulen = []
        tools = [_arac("x", lambda a: gorulen.append(mcpcore.istek_baglami()) or "ok")]
        Dispatcher(tools, "a", "1").handle(_rpc("abc-7", "tools/call", name="x", arguments={}))
        self.assertEqual(gorulen, [{"session_id": None, "request_id": "abc-7"}])


# --------------------------------------------------------------------------- #
# HTTP uçtan uca (açık)                                                        #
# --------------------------------------------------------------------------- #
class HttpUctanUcaTest(_Temel):

    def _sunucu_ac(self):
        sonuc = _sayfa_sonucu("MADDE 1- (1) a\nMADDE 2- (1) b", sayfa=1, toplam=3)
        tools = self.ac([_arac("tr_mevzuat_getir", sonuc)])
        return _sunucu(mcpcore._make_handler(Dispatcher(tools, "arthurlegal-mcp", "1.0.1")))

    def test_initialize_kimlik_verir_cagri_ve_iptal_ayni_oturumda(self):
        with self.yakala() as (out, err), self._sunucu_ac() as port:
            durum, bas, _ = _ayir(_ham_istek(port, "POST", "/mcp", _rpc(1, "initialize")))
            self.assertEqual(durum, 200)
            sid = bas.get("mcp-session-id")
            self.assertRegex(sid or "", r"^[0-9a-f]{32}$")
            oturum = (("Mcp-Session-Id", sid),)
            durum, bas, _ = _ayir(_ham_istek(port, "POST", "/mcp",
                                             _rpc(None, "notifications/initialized"), oturum))
            self.assertEqual(durum, 202)
            durum, bas, govde = _ayir(_ham_istek(port, "POST", "/mcp", _rpc(
                5, "tools/call", name="tr_mevzuat_getir",
                arguments={"mevzuat_id": "102993", "page": 1}), oturum))
            self.assertEqual(durum, 200)
            self.assertNotIn("mcp-session-id", bas)   # yalnız initialize yanıtı taşır
            metin = json.loads(govde)["result"]["content"][0]["text"]
            durum, bas, govde = _ayir(_ham_istek(port, "POST", "/mcp", _rpc(
                None, "notifications/cancelled", requestId=5, reason="zaman aşımı"), oturum))
            self.assertEqual((durum, govde, bas.get("content-length")), (202, b"", "0"))
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(len(err.getvalue().splitlines()), 2)     # yalnız iki kayıt satırı
        kayitlar = self.kayitlar(err)
        self.assertEqual([k["olay"] for k in kayitlar], ["cagri", "iptal"])
        cagri, iptal = kayitlar
        self.assertEqual(list(cagri), CAGRI_ALANLARI)
        self.assertEqual((cagri["session_id"], cagri["request_id"]), (sid, 5))
        self.assertEqual(cagri["pages"], {"sayfa": 1, "toplam": 3, "maddeler": "1-2"})
        self.assertEqual(cagri["returned"]["sha256"],
                         hashlib.sha256(metin.encode("utf-8")).hexdigest())
        self.assertEqual(list(iptal), IPTAL_ALANLARI)
        self.assertEqual({k: v for k, v in iptal.items() if k != "ts"},
                         {"olay": "iptal", "session_id": sid, "request_id": 5,
                          "reason": "zaman aşımı"})
        self.assertRegex(iptal["ts"], TS)

    def test_istemcinin_kimligi_korunur_bilinmeyen_reddedilmez(self):
        with self.yakala() as (out, err), self._sunucu_ac() as port:
            durum, bas, _ = _ayir(_ham_istek(port, "POST", "/mcp", _rpc(1, "initialize"),
                                             (("Mcp-Session-Id", "onceki-oturum"),)))
            self.assertEqual(durum, 200)
            self.assertNotIn("mcp-session-id", bas)   # elinde kimlik olana yenisi verilmez
            durum, _, _ = _ayir(_ham_istek(port, "POST", "/mcp", _rpc(
                2, "tools/call", name="tr_mevzuat_getir", arguments={}),
                (("Mcp-Session-Id", "yeniden-baslatma-oncesi"),)))
            self.assertEqual(durum, 200)              # sunucu yeniden başlasa da istemci kırılmaz
            _ayir(_ham_istek(port, "POST", "/mcp", _rpc(
                3, "tools/call", name="tr_mevzuat_getir", arguments={})))
        k1, k2 = self.kayitlar(err)
        self.assertEqual(k1["session_id"], "yeniden-baslatma-oncesi")
        self.assertIsNone(k2["session_id"])           # başlıksız istek kimliksiz kaydedilir
        self.assertEqual(out.getvalue(), "")

    def test_ayni_baglantida_kimlik_sonraki_istege_sizmaz(self):
        """HTTP/1.1 kalıcı bağlantı: iki istek aynı iş parçacığında, bağlam her istekte sıfırlanır."""
        idler = []
        tools = self.ac([_arac("tr_mevzuat_getir",
                               lambda a: idler.append(threading.get_ident()) or "x")])
        with self.yakala() as (out, err), \
                _sunucu(mcpcore._make_handler(Dispatcher(tools, "a", "1"))) as port:
            baglanti = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                for no, ek in ((1, {"Mcp-Session-Id": "A"}), (2, {})):
                    govde = json.dumps(_rpc(no, "tools/call", name="tr_mevzuat_getir",
                                            arguments={}))
                    baglanti.request("POST", "/mcp", body=govde,
                                     headers=dict(ek, **{"Content-Type": "application/json"}))
                    yanit = baglanti.getresponse()
                    yanit.read()
                    self.assertEqual(yanit.status, 200)
            finally:
                baglanti.close()
        self.assertEqual(len(idler), 2)
        self.assertEqual(idler[0], idler[1])          # gerçekten aynı iş parçacığı
        k1, k2 = self.kayitlar(err)
        self.assertEqual([(k1["session_id"], k1["request_id"]), (k2["session_id"], k2["request_id"])],
                         [("A", 1), (None, 2)])
        self.assertEqual(out.getvalue(), "")

    def test_toplu_istekte_initialize_kimlik_alir(self):
        with self.yakala() as (out, err), self._sunucu_ac() as port:
            durum, bas, govde = _ayir(_ham_istek(port, "POST", "/mcp", [
                _rpc(1, "initialize"),
                _rpc(2, "tools/call", name="tr_mevzuat_getir", arguments={})]))
        self.assertEqual(durum, 200)
        sid = bas.get("mcp-session-id")
        self.assertRegex(sid or "", r"^[0-9a-f]{32}$")
        self.assertEqual(len(json.loads(govde)), 2)
        (kayit,) = self.kayitlar(err)
        self.assertEqual((kayit["session_id"], kayit["request_id"]), (sid, 2))

    def test_istemci_metinleri_200_karakterde_kesilir(self):
        uzun_oturum, uzun_kimlik, uzun_neden = "s" * 500, "r" * 500, "n" * 500
        with self.yakala() as (out, err), self._sunucu_ac() as port:
            oturum = (("Mcp-Session-Id", uzun_oturum),)
            _ayir(_ham_istek(port, "POST", "/mcp", _rpc(
                uzun_kimlik, "tools/call", name="tr_mevzuat_getir", arguments={}), oturum))
            _ayir(_ham_istek(port, "POST", "/mcp", _rpc(
                7, "tools/call", name="tr_mevzuat_getir", arguments={}), oturum))
            durum, _, _ = _ayir(_ham_istek(port, "POST", "/mcp", _rpc(
                None, "notifications/cancelled", requestId=uzun_kimlik, reason=uzun_neden),
                oturum))
            self.assertEqual(durum, 202)
        k1, k2, iptal = self.kayitlar(err)
        self.assertEqual((k1["session_id"], k1["request_id"]), ("s" * 200, "r" * 200))
        self.assertEqual(k2["request_id"], 7)           # sayı olduğu gibi
        self.assertEqual((iptal["session_id"], iptal["request_id"], iptal["reason"]),
                         ("s" * 200, "r" * 200, "n" * 200))

    def test_acikken_govdeler_kapaliyla_ayni(self):
        sonuc = _sayfa_sonucu("MADDE 1- a")
        istekler = [_rpc(1, "initialize", protocolVersion="2025-03-26"), _rpc(2, "tools/list"),
                    _rpc(3, "tools/call", name="tr_mevzuat_getir", arguments={"page": 1})]

        def govdeler(tools):
            with _sunucu(mcpcore._make_handler(Dispatcher(tools, "a", "1", "t"))) as port:
                return [_ayir(_ham_istek(port, "POST", "/mcp", g)) for g in istekler]

        kapali = govdeler([_arac("tr_mevzuat_getir", sonuc)])
        with self.yakala():
            acik = govdeler(self.ac([_arac("tr_mevzuat_getir", sonuc)]))
        for (d1, b1, g1), (d2, b2, g2) in zip(kapali, acik):
            self.assertEqual((d1, g1), (d2, g2))
            self.assertTrue(set(b2) - set(b1) <= {"mcp-session-id"})
            self.assertTrue(set(b1) <= set(b2))
        self.assertIn("mcp-session-id", acik[0][1])
        self.assertNotIn("mcp-session-id", acik[1][1])


# --------------------------------------------------------------------------- #
# C. Çağrı kaydı                                                               #
# --------------------------------------------------------------------------- #
class CagriKaydiTest(_Temel):

    def test_her_cagri_tek_gecerli_json_satiri_ve_tum_alanlar(self):
        sonuc = _madde_sonucu([_kalem("120", "MADDE 120- (1) İşverenin iflas etmesi…",
                                      baslik="Çalışanın önalım hakkı")])
        tools = self.ac([_arac("tr_mevzuat_madde_getir", sonuc), _arac("nl_get_act", "Artikel 1"),
                         _arac("status", {"server": "arthurlegal-mcp"})])
        with self.yakala() as (out, err):
            self.cagir(tools, "tr_mevzuat_madde_getir", {"number": "6769", "madde_no": "120"}, 11)
            self.cagir(tools, "nl_get_act", {"id": "BWBR0001854"}, 12)
            self.cagir(tools, "status", {}, 13)
        self.assertEqual(out.getvalue(), "")
        satirlar = err.getvalue().splitlines()
        self.assertEqual(len(satirlar), 3)
        self.assertTrue(all(s.startswith(KAYIT_ONEKI) for s in satirlar))
        k1, k2, k3 = [json.loads(s) for s in satirlar]
        for k in (k1, k2, k3):
            self.assertEqual(list(k), CAGRI_ALANLARI)
            self.assertEqual(k["olay"], "cagri")
            self.assertRegex(k["ts"], TS)
            self.assertIsNone(k["session_id"])
            self.assertIsInstance(k["duration_ms"], int)
            self.assertGreaterEqual(k["duration_ms"], 0)
            self.assertIs(k["sure_siniri_asildi"], False)
        self.assertEqual([k["request_id"] for k in (k1, k2, k3)], [11, 12, 13])
        self.assertEqual([k["tool"] for k in (k1, k2, k3)],
                         ["tr_mevzuat_madde_getir", "nl_get_act", "status"])
        self.assertEqual(k1["params"], {"number": "6769", "madde_no": "120"})
        self.assertEqual(k1["resolved"], [{"kanun": "6769", "madde": "120", "ok": True,
                                           "mulga": False, "durum": "text"}])
        self.assertEqual((k1["pages"], k1["mulga"], k1["status"]), (None, False, "ok"))
        self.assertEqual([k["backend_version"] for k in (k1, k2, k3)], ["0.5.1", "1.0.1", "1.0.1"])
        self.assertEqual(set(k1["returned"]), {"sha256", "ilk"})
        for k in (k2, k3):
            self.assertEqual((k["resolved"], k["pages"], k["mulga"], k["status"]),
                             (None, None, None, "ok"))
            self.assertEqual(set(k["returned"]), {"sha256", "ilk"})

    def test_arac_tanimi_ayni_yalniz_handler_sarili(self):
        h = lambda a: "x"  # noqa: E731
        once = Tool("tr_ictihat_getir", "açıklama", {"type": "object"}, h)
        (sonra,) = self.ac([once])
        self.assertIsNot(sonra, once)
        self.assertIs(type(sonra), type(once))
        self.assertIs(sonra.name, once.name)
        self.assertIs(sonra.description, once.description)
        self.assertIs(sonra.input_schema, once.input_schema)
        self.assertEqual(sonra.public(), once.public())
        self.assertIs(once.handler, h)               # özgün araç değişmedi
        self.assertIs(sonra.handler.__wrapped__, h)

    def test_arac_kopyasi_alt_sinifi_ve_ek_alanlari_korur(self):
        """copy.copy: konumsal yeniden kurulum olsaydı alt sınıfın ek alanı kaybolurdu."""
        class OzelTool(Tool):
            __slots__ = ("ek",)
        once = OzelTool("tr_belge_getir", "d", {"type": "object"}, lambda a: "x")
        once.ek = "işaret"
        (sonra,) = self.ac([once])
        self.assertIs(type(sonra), OzelTool)
        self.assertEqual(sonra.ek, "işaret")
        self.assertIsNot(sonra.handler, once.handler)

    def test_deger_ve_istisna_ayni_nesne_olarak_gecer(self):
        deger = {"items": [], "complete": True}
        hata = McpError("RATE_LIMITED: HTTP 429")
        beklenmeyen = KeyError("x")
        tools = self.ac([_arac("tr_mevzuat_madde_getir", deger),
                         _arac("tr_mevzuat_getir", hata=hata), _arac("nl_x", hata=beklenmeyen)])
        with self.yakala():
            self.assertIs(tools[0].handler({}), deger)
            with self.assertRaises(McpError) as cm:
                tools[1].handler({})
            self.assertIs(cm.exception, hata)
            with self.assertRaises(KeyError) as cm:
                tools[2].handler({})
            self.assertIs(cm.exception, beklenmeyen)
            acik = [self.cagir(tools, t.name, {}) for t in tools]
        kapali = [self.cagir([_arac("tr_mevzuat_madde_getir", deger),
                              _arac("tr_mevzuat_getir", hata=hata),
                              _arac("nl_x", hata=beklenmeyen)], ad, {})
                  for ad in ("tr_mevzuat_madde_getir", "tr_mevzuat_getir", "nl_x")]
        self.assertEqual(acik, kapali)

    def test_handler_istisna_firlatsa_da_kayit_sonra_yazilir(self):
        sira = []

        def handler(args):
            sira.append("handler")
            raise McpError("LAW_NOT_FOUND: 9999 numaralı KANUN bulunamadı.")
        tools = self.ac([Tool("tr_mevzuat_madde_getir", "d", {}, handler)])
        gercek_yaz = denetim._yaz
        with self.yakala() as (_, err), mock.patch.object(
                denetim, "_yaz", side_effect=lambda k: (sira.append("kayit"), gercek_yaz(k))):
            self.cagir(tools, "tr_mevzuat_madde_getir", {"number": "9999", "madde_no": "1"})
        self.assertEqual(sira, ["handler", "kayit"])
        (k,) = self.kayitlar(err)
        self.assertEqual((k["status"], k["resolved"], k["mulga"], k["returned"]),
                         ("error", None, None, None))

    def test_log_hatasi_sonucu_degistirmez(self):
        deger = _madde_sonucu([_kalem("120", "MADDE 120- metin")])
        hata = McpError("BAD_REQUEST: madde_no gerekli")
        # (bozulan iç parça, başarılı çağrıda uyarı satırı, istisnalı çağrıda uyarı satırı):
        # istisna yolu resolved/status/returned hesaplamaz, o parçalar bozuksa kayıt yine yazılır.
        for hedef, basarili, istisnali in (("_yaz", 1, 1), ("_parametreler", 1, 1),
                                           ("_simdi", 1, 1), ("_cozulenler", 1, 0),
                                           ("_durum_sinifla", 1, 0), ("_donen", 1, 0),
                                           ("_kimlikler", 1, 0),
                                           ("_hata_durumu", 0, 1)):
            with self.subTest(bozulan=hedef):
                tools = self.ac([_arac("tr_mevzuat_madde_getir", deger),
                                 _arac("tr_mevzuat_getir", hata=hata)])
                with mock.patch.object(denetim, hedef, side_effect=RuntimeError("bozuk")):
                    with self.yakala() as (out1, err1):
                        self.assertIs(tools[0].handler({"number": "6769"}), deger)
                    with self.yakala() as (out2, err2):
                        with self.assertRaises(McpError) as cm:
                            tools[1].handler({})
                        self.assertIs(cm.exception, hata)
                for out, err, beklenen, arac in ((out1, err1, basarili, "tr_mevzuat_madde_getir"),
                                                 (out2, err2, istisnali, "tr_mevzuat_getir")):
                    self.assertEqual(out.getvalue(), "")
                    satirlar = err.getvalue().splitlines()
                    uyarilar = [s for s in satirlar if not s.startswith(KAYIT_ONEKI)]
                    self.assertEqual(len(uyarilar), beklenen, err.getvalue())
                    self.assertLessEqual(len(satirlar), 1)          # çağrı başına en çok bir satır
                    for s in uyarilar:
                        self.assertEqual(s, "denetim: kayit yazilamadi (%s: RuntimeError)" % arac)
                        s.encode("ascii")

    def test_yazilamayan_kayit_cagriyi_bozmaz(self):
        class KotuAkis(io.StringIO):
            """Kayıt satırını kodlayamaz (ASCII akış gibi), kısa ASCII uyarıyı yazar."""
            def write(self, s):
                if s.startswith(KAYIT_ONEKI):
                    raise UnicodeEncodeError("ascii", s, 0, 1, "ordinal not in range")
                return super().write(s)
        deger = "Çalışanın önalım hakkı"
        tools = self.ac([_arac("tr_mevzuat_getir", deger)])
        akis = KotuAkis()
        with contextlib.redirect_stderr(akis):
            self.assertIs(tools[0].handler({}), deger)
        self.assertEqual(akis.getvalue(),
                         "denetim: kayit yazilamadi (tr_mevzuat_getir: UnicodeEncodeError)\n")

    def test_sure_siniri(self):
        for bas, son, ms, asildi in ((1000.0, 1100.0, 100000, True),
                                     (0.0, 99.9994, 99999, False), (5.0, 5.25, 250, False)):
            with self.subTest(ms=ms):
                tools = self.ac([_arac("tr_mevzuat_getir", "x")])
                saat = types.SimpleNamespace(monotonic=mock.Mock(side_effect=[bas, son]))
                with self.yakala() as (_, err), mock.patch.object(denetim, "time", saat):
                    tools[0].handler({})
                (k,) = self.kayitlar(err)
                self.assertEqual((k["duration_ms"], k["sure_siniri_asildi"]), (ms, asildi))

    def test_satir_ayiricilar_kayiti_bolmez(self):
        metin = "MADDE 1- a b c\x85d"
        k, _ = self.tek_cagri("tr_mevzuat_getir", metin)
        self.assertEqual(k["returned"]["ilk"], metin)


# --------------------------------------------------------------------------- #
# Maskeleme                                                                    #
# --------------------------------------------------------------------------- #
class ParametreTest(_Temel):
    """Maskeleme yok: test uygulamasında arama metni dahil her argüman olduğu gibi yazılır."""

    def test_parametreler_oldugu_gibi(self):
        args = {"query": "müvekkil Ahmet", "keyword": "kira", "initial_keyword": "önalım",
                "params": {"query": "iç", "filtre": [{"text": "abc", "yil": 2020}, "düz"]},
                "number": "6769", "madde_no": ["120", "geçici 1"], "page": 2}
        k, _ = self.tek_cagri("tr_ictihat_ara", {"results": []}, args=args)
        self.assertEqual(k["params"], args)

    def test_tkgm_parametreleri_oldugu_gibi(self):
        args = {"il": "Ankara", "ilce": "Çankaya", "ada": 101, "parsel": [1, 2], "onay": True,
                "bos": None}
        k, _ = self.tek_cagri("tkgm_parsel_sorgu", {"ok": True}, args=args)
        self.assertEqual(k["params"], args)
        self.assertEqual(k["backend_version"], "0.5.2")

    def test_kayit_handlerin_argumanina_dokunmaz(self):
        alinan = []
        args = {"query": "müvekkil", "params": {"query": "iç"}, "page": 1}
        kopya = copy.deepcopy(args)
        tools = self.ac([_arac("tr_ictihat_ara", lambda a: alinan.append(a) or {"results": []})])
        with self.yakala():
            self.cagir(tools, "tr_ictihat_ara", args)
        self.assertIs(alinan[0], args)
        self.assertEqual(args, kopya)

    def test_params_handlerdan_onceki_hal(self):
        def degistiren(a):
            a.pop("number")
            a["page"] = 99
            a["ic"]["liste"].append("sonradan")
            return _sayfa_sonucu("MADDE 1- a")
        k, _ = self.tek_cagri("tr_mevzuat_getir", degistiren,
                              args={"mevzuat_id": "1", "number": "2004", "page": 1,
                                    "ic": {"liste": ["once"]}})
        self.assertEqual(k["params"], {"mevzuat_id": "1", "number": "2004", "page": 1,
                                       "ic": {"liste": ["once"]}})
        self.assertEqual(k["resolved"], [{"kanun": "2004", "mevzuat_id": "102993"}])


# --------------------------------------------------------------------------- #
class DurumTest(_Temel):

    def test_sozluk_sonuclar(self):
        vakalar = [
            ("upstream_blocked", {"upstream_blocked": True, "unavailable": True, "error": "x"}),
            ("unavailable", {"unavailable": True, "error": "x", "kurum": "kik"}),
            ("error", {"error": "HTTP 404 from …: Record not found", "error_code": "UPSTREAM_ERROR",
                       "retry": False}),
            ("error", {"error": "LAW_NOT_FOUND: 429 numaralı KANUN bulunamadı.",
                       "error_code": "LAW_NOT_FOUND", "retry": False}),
            # Bedesten hız sınırının iki gerçek sözlük biçimi (bedesten_mevzuat._hata,
            # bedesten_ictihat._http_error) ve küçük harfli kod:
            ("rate_limited", {"error": "Bedesten hız sınırı (10 istek / 30 sn, sunucu genelinde "
                                       "ortak): HTTP 429 from https://bedesten.adalet.gov.tr/…",
                              "error_code": "RATE_LIMITED", "retry": True, "retry_after_s": 30.0}),
            ("rate_limited", {"error": "Bedesten hız sınırı (10 istek / 30 sn). Birkaç saniye "
                                       "sonra tekrar deneyin.", "retry": True}),
            ("rate_limited", {"error": "kota", "error_code": "rate_limited"}),
            ("upstream_blocked", {"upstream_blocked": True, "error_code": "RATE_LIMITED"}),
            ("partial", _madde_sonucu([_kalem("120", "MADDE 120- a"), _hata_kalemi("250")])),
            # Kalem düzeyindeki RATE_LIMITED üst düzey durum değildir: bazı maddeler geldi.
            ("partial", _madde_sonucu([_kalem("120", "MADDE 120- a"),
                                       _hata_kalemi("121", "RATE_LIMITED")])),
            ("partial", {"complete": False, "items": []}),
            ("partial", {"items": [{"ok": True}, {"ok": False}]}),
            ("ok", _madde_sonucu([_kalem("120", "MADDE 120- a")])),
            ("ok", {"upstream_blocked": False, "unavailable": False, "results": []}),
            ("ok", {"items": [{"madde": 1}]}),
            ("ok", "düz metin"),
            ("ok", [1, 2]),
            ("ok", None),
        ]
        for beklenen, sonuc in vakalar:
            with self.subTest(beklenen=beklenen, sonuc=str(sonuc)[:60]):
                k, _ = self.tek_cagri("tr_mevzuat_madde_getir", sonuc)
                self.assertEqual(k["status"], beklenen)

    def test_istisnalar(self):
        vakalar = [
            ("error", McpError("LAW_NOT_FOUND: 429 numaralı KANUN bulunamadı.")),
            ("error", McpError("TKGM sunucu hatası döndürdü (HTTP 503).")),
            ("error", ValueError("beklenmeyen")),
            ("rate_limited", McpError(
                "RATE_LIMITED: Bedesten hız sınırı (10 istek / 30 sn, sunucu genelinde ortak): HTTP 429 "
                "from https://bedesten.adalet.gov.tr/mevzuat/searchDocuments: (retry=True, "
                "retry_after_s=30.0).")),
            ("rate_limited", McpError("HttpError: HTTP 429 from local rate limiter: Upstream rate "
                                      "limit: retry in 4 s")),
            ("rate_limited", McpError("HTTP Error 429: Too Many Requests")),
            ("rate_limited", McpError("rate limit exceeded")),
            ("rate_limited", McpError("Parsel Sorgu yavaşlamamızı istedi (HTTP 429).")),
            ("rate_limited", McpError("Bedesten hız sınırı aşıldı")),
        ]
        for beklenen, hata in vakalar:
            with self.subTest(hata=str(hata)[:50]):
                k, yanit = self.tek_cagri("tr_mevzuat_madde_getir", hata=hata)
                self.assertEqual(k["status"], beklenen)
                self.assertTrue(yanit["result"]["isError"])
                self.assertEqual((k["resolved"], k["pages"], k["mulga"], k["returned"]),
                                 (None, None, None, None))


# --------------------------------------------------------------------------- #
# resolved / pages / mulga                                                     #
# --------------------------------------------------------------------------- #
class CozumlemeTest(_Temel):

    def test_madde_getir_kalemlerden(self):
        sonuc = _madde_sonucu([_kalem("120", "MADDE 120- (1) İşverenin iflas etmesi…"),
                               _kalem("geçici 1", "GEÇİCİ MADDE 1- (1) …"),
                               _hata_kalemi("250"), _hata_kalemi("ek 9", "RATE_LIMITED")])
        k, _ = self.tek_cagri("tr_mevzuat_madde_getir", sonuc,
                              args={"number": "9999", "madde_no": ["120", "geçici 1", "250", "ek 9"]})
        self.assertEqual(k["resolved"], [                                       # law.number, args değil
            {"kanun": "6769", "madde": "120", "ok": True, "mulga": False, "durum": "text"},
            {"kanun": "6769", "madde": "geçici 1", "ok": True, "mulga": False, "durum": "text"},
            {"kanun": "6769", "madde": "250", "ok": False, "mulga": None, "durum": None},
            {"kanun": "6769", "madde": "ek 9", "ok": False, "mulga": None, "durum": None}])
        self.assertEqual((k["status"], k["mulga"]), ("partial", False))

    def test_kanun_blogu_yoksa_args_number(self):
        sonuc = {"law": None, "madde_id": "1651731", "complete": True,
                 "fetched_at": "2026-10-08T12:00:00Z",
                 "items": [_kalem("120", "MADDE 120- a", citation=None)],
                 "citation_note": "Bu çağrı kanun künyesini bilmiyor; citation boş."}
        k, _ = self.tek_cagri("tr_mevzuat_madde_getir", sonuc,
                              args={"madde_id": "1651731", "madde_no": "120", "number": 6769})
        self.assertEqual(k["resolved"], [{"kanun": "6769", "madde": "120", "ok": True,
                                          "mulga": False, "durum": "text"}])

    def test_mulga_yapisal_alandan(self):
        sonuc = _madde_sonucu([
            _kalem("25", "MADDE 25- (1) metin"),
            _kalem("12", "MADDE 12 – (Mülga: 6/12/2012-6362/141 md.)", "repealed",
                   status_note="Madde MÜLGA (yürürlükten kaldırılmış)."),
            _kalem("13", "MADDE 13 – (İptal: Anayasa Mahkemesinin …)", "annulled"),
            _kalem("14", "MADDE 14- (… ile ilgili olup yerine işlenmiştir.)",
                   "incorporated_elsewhere")])
        k, _ = self.tek_cagri("tr_mevzuat_madde_getir", sonuc)
        self.assertEqual([r["mulga"] for r in k["resolved"]], [False, True, False, False])
        self.assertIs(k["mulga"], True)
        # durum ham değerdir: AYM iptali ve işlenmiş hüküm mülga sayılmadan ayrıca görünür.
        self.assertEqual([r["durum"] for r in k["resolved"]],
                         ["text", "repealed", "annulled", "incorporated_elsewhere"])

    def test_status_yoksa_mulga_ve_durum_null_metne_bakilmaz(self):
        kalem = _kalem("12", "MADDE 12 – (Mülga: 6/12/2012-6362/141 md.)")
        del kalem["status"]
        k, _ = self.tek_cagri("tr_mevzuat_madde_getir", _madde_sonucu([kalem]))
        self.assertEqual((k["resolved"][0]["mulga"], k["resolved"][0]["durum"]), (None, None))
        self.assertIsNone(k["mulga"])
        self.assertFalse(hasattr(denetim, "_mulga_tahmini"))     # metin sezgisi kaldırıldı

    def test_durum_ham_deger_yoksa_null(self):
        sonuc = _madde_sonucu([_kalem("13", "MADDE 13 – (İptal: …)", "annulled"),
                               _kalem("14", "MADDE 14- metin", None), _hata_kalemi("250")])
        k, _ = self.tek_cagri("tr_mevzuat_madde_getir", sonuc)
        self.assertEqual([(r["madde"], r["mulga"], r["durum"]) for r in k["resolved"]],
                         [("13", False, "annulled"), ("14", None, None), ("250", None, None)])
        self.assertIs(k["mulga"], False)
        k, _ = self.tek_cagri("tr_mevzuat_getir", _sayfa_sonucu("MADDE 1- a"))
        self.assertNotIn("durum", k["resolved"][0])     # yalnız madde_getir kalemlerinde

    def test_mulga_hic_status_yoksa_null(self):
        sonuc = _madde_sonucu([_hata_kalemi("1", "BUDGET_EXHAUSTED"), _hata_kalemi("2")])
        k, _ = self.tek_cagri("tr_mevzuat_madde_getir", sonuc)
        self.assertEqual((k["mulga"], k["status"]), (None, "partial"))

    def test_eski_madde_id_tek_madde(self):
        metin = "Çalışanın önalım hakkı\n\nMADDE 120- (1) İşverenin iflas etmesi…"
        k, _ = self.tek_cagri("tr_mevzuat_madde_getir", _eski_sonuc(metin, ["120"]),
                              args={"madde_id": "1651731"})
        self.assertEqual(k["resolved"], [{"kanun": None, "madde": "120", "ok": True,
                                          "mulga": None, "durum": None}])
        sonuc = _eski_sonuc(metin, ["120"], mevzuat_id="104221", madde_no="120",
                            heading="Çalışanın önalım hakkı",
                            citation="SINAİ MÜLKİYET KANUNU (Kanun No. 6769, RG 10.01.2017/29944) m. 120")
        k, _ = self.tek_cagri("tr_mevzuat_madde_getir", sonuc, args={"madde_id": "1651731"})
        self.assertEqual(k["resolved"], [{"kanun": "6769", "madde": "120", "ok": True,
                                          "mulga": None, "durum": None}])
        self.assertEqual((k["status"], k["mulga"]), ("ok", None))

    def test_eski_madde_id_mulga_metni_olsa_da_null_ve_cok_maddeli_dugum(self):
        k, _ = self.tek_cagri("tr_mevzuat_madde_getir",
                              _eski_sonuc("MADDE 25 – (Mülga: 22/5/2003 - 4857/110 md.)", ["25"]),
                              args={"madde_id": "9", "number": "1475"})
        self.assertEqual(k["resolved"], [{"kanun": "1475", "madde": "25", "ok": True,
                                          "mulga": None, "durum": None}])
        self.assertIsNone(k["mulga"])
        metin = ("Yürürlük\n\nMADDE 191- (1) Bu Kanun yayımı tarihinde yürürlüğe girer.\n\n"
                 "GEÇİCİ MADDE 1- (Mülga: 1/1/2020-7000/1 md.)\n\nGEÇİCİ MADDE 2- (1) metin")
        k, _ = self.tek_cagri("tr_mevzuat_madde_getir",
                              _eski_sonuc(metin, ["191", "geçici 1", "geçici 2"],
                                          note="Bu düğüm 3 madde içeriyor…"))
        self.assertEqual([r["madde"] for r in k["resolved"]], ["191", "geçici 1", "geçici 2"])
        self.assertEqual({(r["mulga"], r["durum"]) for r in k["resolved"]}, {(None, None)})
        self.assertIsNone(k["mulga"])

    def test_eski_madde_id_hata(self):
        hata = {"error": "HTTP 404 from …: Record not found", "error_code": "UPSTREAM_ERROR",
                "retry": False}
        k, _ = self.tek_cagri("tr_mevzuat_madde_getir", hata, args={"madde_id": "1"})
        self.assertEqual(k["resolved"], [{"kanun": None, "madde": None, "ok": False,
                                          "mulga": None, "durum": None}])
        self.assertEqual((k["status"], k["mulga"]), ("error", None))

    def test_mevzuat_getir_sayfa_ve_kimlik(self):
        metin = ("işlenmiştir.\n\nBorca itiraz\nMadde 62 – (Değişik: 18/2/1965 - 538/36 md.)\n"
                 "İtirazın …\nMADDE 63- …\nEK MADDE 3 – …\nGEÇİCİ MADDE 1 – …\n"
                 "bu Kanunun madde 9 - hükmü saklıdır.")
        sonuc = _sayfa_sonucu(metin, sayfa=4, toplam=36,
                              citation="İCRA VE İFLAS KANUNU (Kanun No. 2004, RG sayı 2128)")
        k, _ = self.tek_cagri("tr_mevzuat_getir", sonuc, args={"mevzuat_id": "102993", "page": 4})
        self.assertEqual(k["pages"], {"sayfa": 4, "toplam": 36, "maddeler": "62-geçici 1"})
        self.assertEqual(k["resolved"], [{"kanun": "2004", "mevzuat_id": "102993"}])
        self.assertEqual((k["mulga"], k["status"]), (None, "ok"))

    def test_mevzuat_getir_baslik_yok_kunye_yok_ve_hata(self):
        k, _ = self.tek_cagri("tr_mevzuat_getir", _sayfa_sonucu("…devamı, başlık yok.", 2, 3),
                              args={"mevzuat_id": "102993", "page": 2})
        self.assertEqual(k["pages"], {"sayfa": 2, "toplam": 3, "maddeler": None})
        self.assertEqual(k["resolved"], [{"kanun": None, "mevzuat_id": "102993"}])
        hata = {"error": "Bedesten hız sınırı (10 istek / 30 sn, sunucu genelinde ortak): …",
                "error_code": "RATE_LIMITED", "retry": True, "retry_after_s": 30.0}
        k, _ = self.tek_cagri("tr_mevzuat_getir", hata, args={"mevzuat_id": 104221, "page": 2})
        self.assertIsNone(k["pages"])
        self.assertEqual(k["resolved"], [{"kanun": None, "mevzuat_id": "104221"}])
        self.assertEqual(k["status"], "rate_limited")

    def test_madde_basliklari(self):
        metin = ("BİRİNCİ KISIM\nMadde 12 – (1) a\nMADDE 13- b\n  EK MADDE 3 – c\n"
                 "GEÇİCİ MADDE 1 – d\nMADDE 169/A – e\nMükerrer Madde 5 – f\n"
                 "MADDE 166 ila 184- (… ile ilgili olup yerine işlenmiştir.)\n"
                 "Geçici Madde 2 — g\nbkz. madde 9 - eski\n4857 sayılı Kanunun 5 inci maddesi")
        self.assertEqual(denetim._basliklar(metin),
                         ["12", "13", "ek 3", "geçici 1", "169/a", "mükerrer 5", "166 ila 184",
                          "geçici 2"])
        self.assertEqual(denetim._madde_araligi(metin), "12-geçici 2")
        self.assertEqual(denetim._basliklar("Madde 1. Bu kanun …\nMadde 2: …"), ["1", "2"])
        self.assertEqual(denetim._madde_araligi("MADDE 7 – tek"), "7-7")
        self.assertIsNone(denetim._madde_araligi("başlıksız metin"))
        self.assertIsNone(denetim._madde_araligi(None))


@unittest.skipUnless(os.path.exists(FIKSTUR), "arthur-tr-hukuk-mcp fikstürü yok")
class GercekArkaUcTest(_Temel):
    """bedesten_mevzuat/bedesten_ictihat'ın kendisi, sahte Bedesten ve canlıdan kısaltılmış fikstürle."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, TR_DIZINI)
        try:
            import textx
            from sources import bedesten_ictihat as ic
            from sources import bedesten_mevzuat as mv
        finally:
            sys.path.remove(TR_DIZINI)
        cls.mv, cls.ic, cls.textx = mv, ic, textx
        with open(FIKSTUR, encoding="utf-8") as f:
            cls.fx = json.load(f)

    def setUp(self):
        super().setUp()
        mv, fx = self.mv, self.fx

        def call(path, data, paging=False):
            if path == "/searchDocuments":
                return fx["search"]
            if path == "/mevzuatMaddeTree":
                return fx["tree"]
            if path == "/getDocumentContent":
                anahtar = "%s_%s" % (data.get("documentType", "").lower(), data.get("id"))
                if anahtar in fx:
                    return fx[anahtar]
            raise mv.HttpError(404, "sahte" + path, b"Record not found")

        eski_call, eski_kanun = mv._call, dict(mv._MADDE_KANUN)
        mv._call = call
        mv._CACHE.clear()
        mv._MADDE_KANUN.clear()

        def geri():
            mv._call = eski_call
            mv._CACHE.clear()
            mv._MADDE_KANUN.clear()
            mv._MADDE_KANUN.update(eski_kanun)
        self.addCleanup(geri)
        self.tools = self.ac([Tool("tr_mevzuat_madde_getir", "d", mv.MADDE_SCHEMA, mv.article),
                              Tool("tr_mevzuat_getir", "d", mv.GET_SCHEMA, mv.get),
                              Tool("tr_ictihat_getir", "d", self.ic.GET_SCHEMA, self.ic.get)])

    def calistir(self, ad, args, msg_id=1):
        with self.yakala() as (out, err):
            yanit = self.cagir(self.tools, ad, args, msg_id)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(len(err.getvalue().splitlines()), 1, err.getvalue())
        (k,) = self.kayitlar(err)
        return k, yanit

    def test_baslik_deseni_ve_katlama_arka_uctakiyle_ayni(self):
        """Kayma testi: pages aralığını çıkaran desen ve katlama arka ucun kopyası kalmalı."""
        mv = self.mv
        self.assertEqual(denetim._BASLIK.pattern, mv._BASLIK_SATIRI.pattern)
        self.assertEqual(denetim._BASLIK.flags, mv._BASLIK_SATIRI.flags)
        self.assertEqual(denetim._BASLIK_GEVSEK.pattern, mv._BASLIK_SATIRI_GEVSEK.pattern)
        self.assertEqual(denetim._BASLIK_GEVSEK.flags, mv._BASLIK_SATIRI_GEVSEK.flags)
        self.assertEqual(denetim._KATLA, self.textx._FOLD_1)
        self.assertEqual(dict(denetim._TUR_ETIKETI, madde=""), mv._KIND_TR)

    def test_madde_getir_number_ve_madde_no(self):
        k, yanit = self.calistir("tr_mevzuat_madde_getir",
                                 {"number": "6769", "madde_no": ["120", "250"]})
        self.assertEqual(k["resolved"], [
            {"kanun": "6769", "madde": "120", "ok": True, "mulga": False, "durum": "text"},
            {"kanun": "6769", "madde": "250", "ok": False, "mulga": None, "durum": None}])
        self.assertEqual((k["status"], k["mulga"], k["pages"]), ("partial", False, None))
        metin = yanit["result"]["content"][0]["text"]      # modelin aldığı metnin kendisi
        self.assertEqual(k["returned"], {"sha256": hashlib.sha256(metin.encode("utf-8")).hexdigest(),
                                         "ilk": metin[:300]})

    def test_eski_madde_id_soguk_ve_sicak_onbellek(self):
        k, _ = self.calistir("tr_mevzuat_madde_getir", {"madde_id": "1651731"})
        self.assertEqual(k["resolved"], [{"kanun": None, "madde": "120", "ok": True,
                                          "mulga": None, "durum": None}])
        self.calistir("tr_mevzuat_madde_getir", {"number": "6769", "madde_no": "120"})
        k, _ = self.calistir("tr_mevzuat_madde_getir", {"madde_id": "1651731"})
        self.assertEqual(k["resolved"], [{"kanun": "6769", "madde": "120", "ok": True,
                                          "mulga": None, "durum": None}])
        self.assertEqual(k["status"], "ok")

    def test_hiz_siniri_iki_gercek_sozluk_bicimi(self):
        """Bedesten 429'u istisna değil sözlük döner; ikisi de rate_limited yazılmalı."""
        mv, ic = self.mv, self.ic

        def sinirli(path, data, paging=False):
            raise mv.HttpError(429, "sahte" + path, b"Too Many Requests")
        mv._call = sinirli                                       # geri() setUp'ta kayıtlı
        k, yanit = self.calistir("tr_mevzuat_getir", {"mevzuat_id": "104221"})
        sonuc = json.loads(yanit["result"]["content"][0]["text"])
        self.assertEqual(sonuc["error_code"], "RATE_LIMITED")    # bedesten_mevzuat._hata biçimi
        self.assertEqual((k["status"], k["pages"]), ("rate_limited", None))

        hata = mv.HttpError(429, "sahte/emsal-karar/getDocumentContent", b"Too Many Requests")
        with mock.patch.object(ic._http, "post_json", side_effect=hata):
            k, yanit = self.calistir("tr_ictihat_getir", {"document_id": "123"})
        sonuc = json.loads(yanit["result"]["content"][0]["text"])
        self.assertNotIn("error_code", sonuc)                    # bedesten_ictihat._http_error biçimi
        self.assertIn("hız sınırı", sonuc["error"])
        self.assertEqual(k["status"], "rate_limited")

    def test_mevzuat_getir(self):
        k, _ = self.calistir("tr_mevzuat_getir", {"mevzuat_id": "104221", "page": 1,
                                                  "page_chars": 600})
        self.assertEqual(k["pages"], {"sayfa": 1, "toplam": 1, "maddeler": "119-121"})
        self.assertEqual(k["resolved"], [{"kanun": None, "mevzuat_id": "104221"}])
        self.calistir("tr_mevzuat_madde_getir", {"number": "6769", "madde_no": "120"})
        k, _ = self.calistir("tr_mevzuat_getir", {"mevzuat_id": "104221"})
        self.assertEqual(k["resolved"], [{"kanun": "6769", "mevzuat_id": "104221"}])
        k, _ = self.calistir("tr_mevzuat_getir", {"mevzuat_id": "999"})
        self.assertEqual((k["status"], k["pages"]), ("error", None))

    def test_mcperror_yolu(self):
        k, yanit = self.calistir("tr_mevzuat_madde_getir", {"number": "abc", "madde_no": "1"})
        self.assertTrue(yanit["result"]["isError"])
        self.assertEqual((k["status"], k["resolved"], k["returned"]), ("error", None, None))


# --------------------------------------------------------------------------- #
# returned                                                                     #
# --------------------------------------------------------------------------- #
class DonenMetinTest(_Temel):
    """returned bütün araçlarda; kimlikler sonuç listelerindeki kayıtların kimlik alanları."""

    ARAMA = ("tr_mevzuat_ara", "tr_mevzuat_icinde_ara", "tr_ictihat_ara",
             "tr_ictihat_semantik_ara", "tr_aym_ara", "tr_uyusmazlik_ara", "tr_resmi_gazete_tara",
             "tr_resmi_gazete_fihrist", "tr_kurum_karari_ara", "tr_spk_bulten_icinde_ara",
             "tr_semantik_ara")
    GETIR = ("tr_mevzuat_getir", "tr_mevzuat_madde_getir", "tr_mevzuat_gerekce",
             "tr_mevzuat_icindekiler", "tr_ictihat_getir", "tr_aym_getir",
             "tr_resmi_gazete_getir", "tr_uyusmazlik_getir", "tr_belge_getir",
             "tr_kurum_karari_getir")

    @unittest.skipUnless(os.path.exists(os.path.join(TR_DIZINI, "server.py")), "tr arka ucu yok")
    def test_test_edilen_araclar_gercek(self):
        # Ağ geçidi tr arka ucunun build_tools() içindeki Tool("ad", …) adlarını tr_ önekiyle sunar.
        with open(os.path.join(TR_DIZINI, "server.py"), encoding="utf-8") as f:
            agac = ast.parse(f.read())
        gercek = {"tr_" + n.args[0].value for n in ast.walk(agac)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id == "Tool" and n.args and isinstance(n.args[0], ast.Constant)}
        self.assertLessEqual(set(self.GETIR) | set(self.ARAMA), gercek)

    def test_sha256_modelin_aldigi_metnin_kendisi(self):
        ozel = {"tr_mevzuat_getir": {"text": "MADDE 1- " + "ç" * 1000, "page": 1},
                "tr_ictihat_getir": "düz metin sonucu", "tr_aym_getir": {"a": 1},
                "tr_resmi_gazete_getir": [1, "b"],
                "tr_ictihat_ara": {"query": "müvekkil Ahmet", "results": []}}
        for ad in sorted(set(self.GETIR) | set(self.ARAMA) | {"nl_get_act", "tkgm_rapor", "status"}):
            sonuc = ozel.get(ad, {"text": "belge metni", "page": 1})
            with self.subTest(arac=ad):
                k, yanit = self.tek_cagri(ad, sonuc)
                metin = yanit["result"]["content"][0]["text"]
                self.assertEqual(metin, mcpcore._as_text(sonuc))
                self.assertEqual(k["returned"]["sha256"],
                                 hashlib.sha256(mcpcore._as_text(sonuc).encode("utf-8")).hexdigest())
                self.assertEqual(k["returned"]["ilk"], metin[:300])
                self.assertLessEqual(len(k["returned"]["ilk"]), 300)

    def test_arama_sonucunun_kimlikleri(self):
        bedesten = {"query": "kira", "total": 2, "results": [
            {"document_id": "123", "court": "Yargıtay", "chamber": "3. HD", "esas_no": "2024/1",
             "karar_no": "2024/9", "karar_tarihi": "01.02.2024",
             "citation": "Yargıtay 3. HD, E. 2024/1, K. 2024/9, T. 01.02.2024",
             "source_url": "https://mevzuat.adalet.gov.tr/ictihat/123"},
            {"document_id": "456", "esas_no": "2023/5", "karar_no": "2023/7", "citation": "c2"}],
            "denenenler": [{"mod": "ifade", "toplam": 2}]}
        k, _ = self.tek_cagri("tr_ictihat_ara", bedesten)
        self.assertEqual(k["kimlikler"], [
            {"document_id": "123", "esas_no": "2024/1", "karar_no": "2024/9",
             "citation": "Yargıtay 3. HD, E. 2024/1, K. 2024/9, T. 01.02.2024"},
            {"document_id": "456", "esas_no": "2023/5", "karar_no": "2023/7", "citation": "c2"}])

        rekabet = {"total": 1, "results": [{"karar_id": "g-1", "title": "Acme A.Ş. devralma",
                                            "decision_number": "24-01/2", "citation": "RK c"}]}
        k, _ = self.tek_cagri("tr_kurum_karari_ara", rekabet)
        self.assertEqual(k["kimlikler"], [{"karar_id": "g-1", "citation": "RK c"}])

        semantik = {"retrieval": {"ranking": "semantic"},
                    "results": [{"ref": "rekabet:g-1", "title": "t", "citation": "RK c",
                                 "score": 0.5}]}
        k, _ = self.tek_cagri("tr_semantik_ara", semantik)
        self.assertEqual(k["kimlikler"], [{"ref": "rekabet:g-1", "citation": "RK c"}])

    def test_kimlik_yoksa_null_ve_tek_belge_sayilmaz(self):
        for ad, sonuc in (("tr_ictihat_ara", {"results": []}),
                          ("tr_ictihat_getir", {"document_id": "123", "text": "metin"}),
                          ("status", {"by_kurum": {"rekabet": {"docs": 3}}}),
                          ("tr_ictihat_getir", "düz metin")):
            with self.subTest(arac=ad, sonuc=str(sonuc)[:30]):
                k, _ = self.tek_cagri(ad, sonuc)
                self.assertIsNone(k["kimlikler"])

    def test_kimlik_azami(self):
        sonuc = {"results": [{"id": str(i)} for i in range(denetim.KIMLIK_AZAMI + 20)]}
        k, _ = self.tek_cagri("tr_aym_ara", sonuc)
        self.assertEqual(len(k["kimlikler"]), denetim.KIMLIK_AZAMI)
        self.assertEqual(k["kimlikler"][0], {"id": "0"})

    def test_istisnada_null(self):
        k, _ = self.tek_cagri("tr_mevzuat_getir", hata=McpError("x"))
        self.assertIsNone(k["returned"])
        self.assertIsNone(k["kimlikler"])


# --------------------------------------------------------------------------- #
class AkisTest(_Temel):

    def test_kayit_stderrde_tek_write_cagrisi_stdout_bos(self):
        class Kaydeden(io.StringIO):
            def __init__(self):
                super().__init__()
                self.yazilar, self.flushlar = [], 0

            def write(self, s):
                self.yazilar.append(s)
                return super().write(s)

            def flush(self):
                self.flushlar += 1
        tools = self.ac([_arac("tr_mevzuat_getir", "x"), _arac("nl_get_act", "y")])
        akis, out = Kaydeden(), io.StringIO()
        with contextlib.redirect_stderr(akis), contextlib.redirect_stdout(out):
            self.cagir(tools, "tr_mevzuat_getir", {})
            self.cagir(tools, "nl_get_act", {})
            denetim._bildirim(_rpc(None, "notifications/cancelled", requestId=1))
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(len(akis.yazilar), 3)                    # kayıt başına TEK write
        for yazi in akis.yazilar:
            self.assertTrue(yazi.startswith(KAYIT_ONEKI) and yazi.endswith("\n"))
            self.assertEqual(yazi.count("\n"), 1)
            json.loads(yazi)
        self.assertEqual(akis.flushlar, 3)

    def test_stdio_protokol_kanali_temiz_kayitlar_stderrde(self):
        tools = self.ac([_arac("tr_mevzuat_getir", _sayfa_sonucu("MADDE 1- a"))])
        satirlar = [_rpc(1, "initialize"), _rpc(2, "tools/call", name="tr_mevzuat_getir",
                                                arguments={"mevzuat_id": "1"}),
                    _rpc(None, "notifications/cancelled", requestId=2, reason="iptal")]
        giris = io.StringIO("".join(json.dumps(s) + "\n" for s in satirlar))
        with self.yakala() as (out, err), mock.patch.object(sys, "stdin", giris):
            mcpcore.run_stdio(Dispatcher(tools, "arthurlegal-mcp", "1.0.1"))
        yanitlar = [json.loads(s) for s in out.getvalue().splitlines()]
        self.assertEqual([y["id"] for y in yanitlar], [1, 2])
        self.assertTrue(all(set(y) <= {"jsonrpc", "id", "result", "error"} for y in yanitlar))
        kayitlar = self.kayitlar(err)
        self.assertEqual([(k["olay"], k["session_id"], k["request_id"]) for k in kayitlar],
                         [("cagri", None, 2), ("iptal", None, 2)])


# --------------------------------------------------------------------------- #
# Sürüm ve iptal kancası                                                       #
# --------------------------------------------------------------------------- #
class SurumVeIptalTest(_Temel):

    def test_arka_uc_surumleri(self):
        moduller = {"srv_deneme": types.ModuleType("srv_deneme"),
                    "eskipkg": types.ModuleType("eskipkg"),
                    "eskipkg.mcp_server": types.ModuleType("eskipkg.mcp_server"),
                    "digerpkg": types.ModuleType("digerpkg"),
                    "digerpkg.mcp_server": types.ModuleType("digerpkg.mcp_server"),
                    "surumsuz": types.ModuleType("surumsuz")}
        moduller["srv_deneme"].__version__ = "0.5.1"
        moduller["eskipkg.mcp_server"].SERVER_VERSION = "0.1.0"
        moduller["digerpkg"].__version__ = "2.3.4"
        yuklenenler = [{"prefix": "tr", "module": "srv_deneme"},
                       {"prefix": "scholar", "module": "eskipkg.mcp_server"},
                       {"prefix": "az", "module": "digerpkg.mcp_server"},
                       {"prefix": "xx", "module": "surumsuz"},
                       {"prefix": "yy", "module": "yuklenmemis_modul"},
                       {"prefix": "de", "label": "vekil", "mode": "proxy → …"}]
        with mock.patch.dict(sys.modules, moduller):
            surumler = denetim.arka_uc_surumleri(yuklenenler)
        self.assertEqual(surumler, {"tr": "0.5.1", "scholar": "0.1.0", "az": "2.3.4"})
        tools = self.ac([_arac(ad, "x") for ad in ("tr_mevzuat_getir", "scholar_search",
                                                   "az_get_act", "de_search", "xx_a", "status")],
                        surumler=surumler)
        with self.yakala() as (_, err):
            for t in tools:
                self.cagir(tools, t.name, {})
        self.assertEqual([k["backend_version"] for k in self.kayitlar(err)],
                         ["0.5.1", "0.1.0", "2.3.4", "1.0.1", "1.0.1", "1.0.1"])

    def test_iptal_kancasi_yalniz_cancelled(self):
        self.ac([])
        mcpcore._baglam.session_id = "s1"
        with self.yakala() as (out, err):
            denetim._bildirim(_rpc(None, "notifications/initialized"))
            denetim._bildirim(_rpc(None, "notifications/progress", progressToken=1))
            denetim._bildirim(_rpc(None, "notifications/cancelled", requestId="r-9"))
            denetim._bildirim({"jsonrpc": "2.0", "method": "notifications/cancelled",
                               "params": "bozuk"})
            denetim._bildirim("sözlük değil")
        self.assertEqual(len(err.getvalue().splitlines()), 2)
        kayitlar = self.kayitlar(err)
        self.assertEqual([(k["session_id"], k["request_id"], k["reason"]) for k in kayitlar],
                         [("s1", "r-9", None), ("s1", None, None)])
        self.assertEqual(out.getvalue(), "")

    def test_iptal_kaydinda_istemci_metinleri_kesilir(self):
        mcpcore._baglam.session_id = "s" * 300
        with self.yakala() as (_, err):
            denetim._bildirim(_rpc(None, "notifications/cancelled", requestId="r" * 300,
                                   reason="n" * 300))
            denetim._bildirim(_rpc(None, "notifications/cancelled", requestId=12345,
                                   reason={"tur": "sözlük"}))
        k1, k2 = self.kayitlar(err)
        self.assertEqual((k1["session_id"], k1["request_id"], k1["reason"]),
                         ("s" * 200, "r" * 200, "n" * 200))
        self.assertEqual((k2["request_id"], k2["reason"]), (12345, {"tur": "sözlük"}))

    def test_iptal_kancasi_hatasi_tek_satir(self):
        with self.yakala() as (out, err), mock.patch.object(
                denetim, "_yaz", side_effect=RuntimeError("x")):
            denetim._bildirim(_rpc(None, "notifications/cancelled", requestId=1))
        self.assertEqual(err.getvalue(), "denetim: kayit yazilamadi (iptal: RuntimeError)\n")


if __name__ == "__main__":
    unittest.main()
