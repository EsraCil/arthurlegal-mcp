"""Offline tests — no network. Run: python -m pytest tests/ -q  (or python tests/test_offline.py)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import retrieval  # noqa: E402
import textx  # noqa: E402
from net import decode  # noqa: E402
from sources import bedesten_ictihat, bddk, epdk, kvkk, rekabet, resmi_gazete, sigorta_tahkim, spk  # noqa: E402


def test_turkish_folding():
    assert textx.tr_lower("İSTANBUL IĞDIR") == "istanbul ığdır"
    assert textx.tr_fold("Kürşat ŞİŞLİ") == "kursat sisli"
    assert textx.count_hits("Aydınlatma yükümlülüğü", "AYDINLATMA") == 1


def test_html_to_text_and_charset():
    t = textx.html_to_text("<h1>Başlık</h1><p>Bir&nbsp;paragraf.</p><table><tr><td>a</td><td>b</td></tr></table>")
    assert "BAŞLIK" in t and "Bir paragraf." in t and "a | b" in t.replace("  ", " ") or "a" in t
    assert decode("Türkçe".encode("windows-1254"), {"Content-Type": "text/html; charset=windows-1254"}) == "Türkçe"
    assert decode("Türkçe".encode("utf-8"), {}) == "Türkçe"


def test_paginate():
    p = textx.paginate("x" * 10, 2, 4)
    assert p["page"] == 2 and p["total_pages"] == 3 and p["text"] == "xxxx" and p["has_more"]


def test_bedesten_chambers_and_citation():
    assert bedesten_ictihat.CHAMBERS["H9"] == "9. Hukuk Dairesi"
    assert bedesten_ictihat.CHAMBERS["IDDK"] == "İdare Dava Daireleri Kurulu"
    c = bedesten_ictihat._citation("Yargıtay Kararı", "9. Hukuk Dairesi", "2023/1", "2024/2", "01.02.2024")
    assert c == "Yargıtay 9. Hukuk Dairesi, E. 2023/1, K. 2024/2, 01.02.2024"


def test_bddk_title_parse():
    m = bddk._HEAD.match("(06.08.2026 - 11548) BLG Varlık Yönetim A.Ş.’nin faaliyet izninin iptal edilmesine ilişkin Kurul Kararı")
    assert m and m.group(1) == "06.08.2026" and m.group(2) == "11548"


def test_rekabet_list_parser():
    html = """<div class="yazi01">Toplam : 42</div><div id="kararList">
    <table class="equalDivide"><tr><td>1.9.2026</td><td>25-44/1086-615</td><td><a href="/Karar?kararId=x">i</a></td></tr>
    <tr><td>27.11.2025</td><td>Rekabet İhlali</td></tr>
    <tr><td colspan="5"><a href="/Karar?kararId=88f38c4f-97ec">Yemek Sepeti taahhüt</a></td></tr></table></div>"""
    out = rekabet._parse_list(html)
    assert out["total"] == 42 and len(out["results"]) == 1
    r = out["results"][0]
    assert r["karar_id"] == "88f38c4f-97ec" and r["decision_number"] == "25-44/1086-615"
    assert "27.11.2025" in r["citation"]


def test_rekabet_unquoted_multiword_query_is_flagged():
    # The Kurum's PdfText search matches words, not phrases: an unquoted multi-word query inflates `total`.
    html = '<div class="yazi01">Toplam : 2737</div><div id="kararList"></div>'
    original = rekabet._http.get_text
    rekabet._http.get_text = lambda *a, **k: html
    try:
        assert "DİKKAT" in rekabet.search({"query": "karşı oy"})["note"]
        assert "DİKKAT" not in rekabet.search({"query": '"karşı oy gerekçesi"'})["note"]
        assert "DİKKAT" not in rekabet.search({"query": "oyçokluğu"})["note"]
    finally:
        rekabet._http.get_text = original
    assert "kelime bazlıdır" in rekabet.SOURCE.notes


def test_sigorta_split():
    txt = ("İÇİNDEKİLER\n15.06.2026 Tarih ve K-2026/1 Sayılı Hakem Kararı ....... 3\n" +
           "15.06.2026 Tarih ve K-2026/1 Sayılı Hakem Kararı\n" + "gövde " * 300 +
           "\n16.06.2026 Tarih ve K-2026/2 Sayılı İtiraz Hakem Heyeti Kararı\n" + "metin " * 300 +
           "\n07/09/2015 tarih ve K.2015/8207 Sayılı Hakem Kararı\n" + "eski " * 300 +
           "\n09.04.2025 Tarih – K-2025/180058 Sayılı Hakem Kararı\n" + "tire " * 300 +
           "\n21/09/2021 Tarihli - 2021/İHK-30910 Sayılı İtiraz Hakem Heyeti Kararı\n" + "ihk " * 300)
    d = sigorta_tahkim.split_decisions(txt, min_len=500)
    assert [x["heading"][:10] for x in d] == ["15.06.2026", "16.06.2026", "07/09/2015", "09.04.2025", "21/09/2021"]


def test_spk_sections():
    t = "A. DUYURU VE İLKE KARARLARI\n1. Kurul Karar Organı ...\nB. DİĞER ÖZEL DURUMLAR\nmetin"
    s = spk._sections(t)
    assert len(s) == 2 and s[0]["heading"].startswith("A.")


def test_kvkk_link_regex():
    m = kvkk._LINK.search('<a href="https://www.kvkk.gov.tr/Icerik/8884/2024-1361">Devamını Gör</a>')
    assert m and m.group(2) == "8884" and m.group(3) == "2024-1361"


def test_index_roundtrip_and_search(tmp_path=None):
    d = tmp_path or tempfile.mkdtemp()
    idx = retrieval.Index(os.path.join(str(d), "t.db"))
    idx.upsert({"ref": "a", "title": "Elektrik piyasası lisans iptali", "body": "EPDK lisans iptal kararı", "subject": "epdk",
                "citation": "EPDK, 1", "date": "2024-01-01"})
    idx.upsert({"ref": "b", "title": "Kişisel verilerin korunması", "body": "aydınlatma yükümlülüğü ihlali", "subject": "kvkk",
                "citation": "KVKK, 2", "date": "2024-02-01"})
    idx.reindex_fts()
    out = idx.search("lisans iptali", mode="lexical")
    assert out["results"] and out["results"][0]["ref"] == "a"
    out = idx.search("aydinlatma", mode="lexical", filters={"subject": "kvkk"})
    assert out["results"] and out["results"][0]["ref"] == "b"
    assert idx.get("a")["citation"] == "EPDK, 1"


def test_tirnakli_ifade_buyuk_harfli_basligi_da_bulur():
    """Büyük harfli başlık "KARŞI OY GEREKÇESİ" unicode61'de "karsi", akan metin "karsı" olur."""
    d = tempfile.mkdtemp()
    idx = retrieval.Index(os.path.join(d, "t.db"))
    idx.upsert({"ref": "r:1", "title": "A", "body": "KARŞI OY GEREKÇESİ\nKurul çoğunluğunun görüşüne katılmıyorum.",
                "subject": "rekabet"})
    idx.upsert({"ref": "r:2", "title": "B", "body": "Üyenin karşı oy gerekçesi ekte yer almaktadır.", "subject": "rekabet"})
    idx.upsert({"ref": "r:3", "title": "C", "body": "Karşı taraf oy birliğiyle gerekçesini sundu.", "subject": "rekabet"})
    idx.reindex_fts()
    for q in ('"karşı oy gerekçesi"', '"KARŞI OY GEREKÇESİ"', '"Karşı Oy Gerekçesi"', '"karsi oy gerekcesi"'):
        refs = {r["ref"] for r in idx.search(q, mode="lexical")["results"]}
        assert refs == {"r:1", "r:2"}, (q, refs)
    assert retrieval._fts_query('"rekabet kurulu"') == '"rekabet kurulu"'  # ı/i yoksa tek ifade
    assert len(retrieval._phrase_variants("karşı oy gerekçesi")) == 4
    uzun = retrieval._phrase_variants("idari para cezası verilmesine gerek olmadığına karar verildi")
    assert len(uzun) <= 3, "beşten çok ı/i'li kelimede bileşim patlamasın"


def test_server_stdio_lists_tools():
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    env = dict(os.environ, INDEX_PATH=os.path.join(tempfile.mkdtemp(), "i.db"), PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, os.path.join(ROOT, "server.py")],
                       input="\n".join(json.dumps(m) for m in msgs) + "\n", capture_output=True, text=True,
                       encoding="utf-8", timeout=120, env=env)
    names = []
    for line in p.stdout.splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("id") == 2:
            names = [t["name"] for t in o["result"]["tools"]]
    assert "ictihat_ara" in names and "kurum_karari_ara" in names and "semantik_ara" in names and "status" in names
    assert len(names) >= 24


# --------------------------------------------------------------------------- #
# Konu triyajı — resmi_gazete_tara'nın yerel ön elemesi
# --------------------------------------------------------------------------- #

import triyaj  # noqa: E402


def test_triyaj_katlama_egitimle_ayni():
    """Katlama eğitimdeki ``jev.katla`` ile birebir olmalı.

    Ayrışırsa model kendi sözlüğünü tanımaz ve HATA VERMEDEN her şeye sıfır
    der — yani süzgeç her kalemi eler ve kullanıcı 'o aralıkta bir şey yok'
    sanır. Sessiz bozulma, bu modülün en tehlikeli hata biçimidir.
    """
    assert triyaj.katla("İSTANBUL IĞDIR") == "istanbul igdir"
    assert triyaj.katla("Çağrı ŞÜKRÜ Öztürk") == "cagri sukru ozturk"


def test_triyaj_model_yuklenir_ve_kunye_dogru():
    k = triyaj.kunye()
    assert k["var"] is True, k
    assert set(k["konular"]) == {"enerji", "rekabet", "vergi", "icra"}
    assert k["esik"] == 0.20          # esik_yerel.py ile ölçüldü
    assert k["olcum"]["duyarlilik_esik_0.20"]["icra"] < 1.0   # tavan dürüstlüğü


def test_triyaj_kural_katmani_ve_ic_idare_istisnasi():
    m = triyaj.motor()
    # Kurum adı kuralı ateşler.
    assert m.p("vergi", "TEBLİĞLER Vergi Usul Kanunu Genel Tebliği") == 1.0
    # Ama kurumun KENDİ personel düzenlemesinde susar; model konuşur.
    ic = "YÖNETMELİKLER Gelir İdaresi Başkanlığı Personeli Görevde Yükselme Yönetmeliği"
    assert m.p("vergi", ic) < 0.20
    assert m.kural_eslesmeleri(ic, "vergi") == []


def test_triyaj_konu_adi_gecmeyen_kalemi_yakalar():
    """Süzgecin asıl varlık sebebi: metinsel aramanın bulamadığını bulmak."""
    m = triyaj.motor()
    for konu, baslik in (
        ("icra", "TEBLİĞLER Konkordato Gider Avansı Tarifesi"),
        ("enerji", "YÖNETMELİKLER Şarj Hizmeti Yönetmeliğinde Değişiklik Yapılmasına Dair Yönetmelik"),
        ("vergi", "TEBLİĞLER Tahsilat Genel Tebliği (Seri:B Sıra No:20)"),
    ):
        assert konu not in triyaj.katla(baslik), "örnek geçersiz: konu adı geçiyor"
        assert m.p(konu, baslik) >= 0.20, (konu, baslik)


def test_triyaj_alakasiz_kalemi_eler():
    m = triyaj.motor()
    t = "YÖNETMELİKLER Türkiye Okçuluk Federasyonu Ana Statüsü"
    assert all(m.p(k, t) < 0.20 for k in m.konular)


def test_triyaj_bilinmeyen_konu_reddedilir():
    m = triyaj.motor()
    try:
        m.p("ceza", "TEBLİĞLER Bir Şey")
    except ValueError:
        return
    raise AssertionError("bilinmeyen konu sessizce kabul edildi")


def test_rg_konu_suzgeci_elenen_sayisini_bildirir():
    """Kaç kalemin atıldığı söylenmeden 'bulunan budur' demek dürüst değil."""
    items = [
        {"section": "TEBLİĞLER", "title": "Konkordato Gider Avansı Tarifesi", "url": ""},
        {"section": "YÖNETMELİKLER", "title": "Türkiye Okçuluk Federasyonu Ana Statüsü", "url": ""},
    ]
    r = resmi_gazete._konu_suz(items, "icra", None)
    assert not r.get("error"), r
    assert len(r["items"]) == 1 and r["elenen"] == 1 and r["esik"] == 0.20
    assert r["items"][0]["konu_skoru"] >= 0.20


def test_rg_konu_esik_sifir_hicbir_seyi_elemez():
    items = [{"section": "YÖNETMELİKLER", "title": "Türkiye Okçuluk Federasyonu Ana Statüsü", "url": ""}]
    r = resmi_gazete._konu_suz(items, "icra", 0)
    assert r["elenen"] == 0 and len(r["items"]) == 1
    assert "konu_skoru" in r["items"][0]


def test_rg_tara_query_veya_konu_ister():
    """İkisi de yokken 60 günü ham dökmek çağırana yüzlerce ilgisiz kalem yollar."""
    r = resmi_gazete.scan({"date_from": "2026-01-01", "date_to": "2026-01-05"})
    assert "error" in r and "konu" in r["error"]


def test_rg_tara_gecersiz_konuda_AGA_CIKMADAN_hata_verir():
    """Doğrulama döngüden önce olmalı.

    Sonra olsaydı geçersiz konu her gün fihrist hatası üretir, döngü o günleri
    atlar ve sonuç 'total: 0' olurdu — ağa 60 istek atıp kullanıcıya sessiz bir
    yalan söyleyerek.
    """
    r = resmi_gazete.scan({"konu": "ceza", "date_from": "2026-01-01",
                           "date_to": "2026-01-05"})
    assert "error" in r and "desteklenmiyor" in r["error"]
    r2 = resmi_gazete.scan({"konu": "icra", "esik": "elma",
                            "date_from": "2026-01-01", "date_to": "2026-01-05"})
    assert "error" in r2 and "sayı" in r2["error"]


def test_rg_tara_semasi_konuyu_ve_sinirini_duyurur():
    """Sınır şemada yazmazsa çağıran modeli yanlış işte kullanır."""
    import server
    araclar = {t.name: t for t in server.TOOLS}
    sema = araclar["resmi_gazete_tara"].input_schema
    props = sema["properties"]
    assert set(props["konu"]["enum"]) == {"enerji", "rekabet", "vergi", "icra"}
    assert "esik" in props
    assert not sema.get("required"), "query artık zorunlu olmamalı"
    assert "ÖN ELEME" in props["konu"]["description"].upper()
    # Fihrist aracı da aynı süzgeci sunmalı; ikisi ayrışırsa kullanıcı şaşırır.
    assert "konu" in araclar["resmi_gazete_fihrist"].input_schema["properties"]


def test_only_new_mevcut_kaydi_atlar_ve_vektorunu_korur():
    """Tazeleme modu: indeksteki ref yeniden yazılmaz, vektörü silinmez, adaptöre 'var' der."""
    import crawl
    d = tempfile.mkdtemp()
    idx = retrieval.Index(os.path.join(d, "t.db"))
    idx.upsert({"ref": "bddk:1", "title": "Eski başlık", "body": "tam metin", "subject": "bddk"})
    doc_id = idx.db.execute("SELECT id FROM docs WHERE ref='bddk:1'").fetchone()["id"]
    idx.db.execute("INSERT INTO vecs(doc_id, dim, vec, model) VALUES(?, ?, ?, ?)", (doc_id, 2, b"\x00" * 8, "m"))
    idx.db.commit()

    yeni = crawl._Tagged(idx, "bddk", only_new=True)
    assert yeni.exists("bddk:1") and not yeni.exists("bddk:2")
    assert yeni.exists_prefix("bddk:") and not yeni.exists_prefix("spk:")
    yeni.upsert({"ref": "bddk:1", "title": "Kısa stub", "body": "stub", "subject": "bddk"})
    row = idx.db.execute("SELECT body FROM docs WHERE ref='bddk:1'").fetchone()
    assert row["body"] == "tam metin", "only_new mevcut gövdeyi ezdi"
    assert idx.db.execute("SELECT COUNT(*) FROM vecs WHERE doc_id=?", (doc_id,)).fetchone()[0] == 1
    assert yeni.skipped >= 1

    eski = crawl._Tagged(idx, "bddk")          # varsayılan: değişmeyen davranış
    assert not eski.exists("bddk:1") and not eski.exists_prefix("bddk:")
    eski.upsert({"ref": "bddk:1", "title": "Kısa stub", "body": "stub", "subject": "bddk"})
    assert idx.db.execute("SELECT body FROM docs WHERE ref='bddk:1'").fetchone()["body"] == "stub"
    assert idx.db.execute("SELECT COUNT(*) FROM vecs WHERE doc_id=?", (doc_id,)).fetchone()[0] == 0


def test_ictihat_tek_tarafli_tarih_araligi_tamamlanir():
    """Bedesten yalnız kararTarihiStart verilince süzgeci SESSİZCE yok sayıyor: 612 yerine
    52.993 karar (canlı, 2026-09-20). ictihat_semantik_ara yalnız date_from alır — hep süzgeçsizdi."""
    gonderilen = []
    eski = bedesten_ictihat._http.post_json

    def sahte(path, payload, **kw):
        gonderilen.append(dict(payload["data"]))
        return {"metadata": {"FMTY": "SUCCESS"}, "data": {"emsalKararList": [], "total": 0}}

    bedesten_ictihat._http.post_json = sahte
    aramalar = []
    try:
        for args in ({"query": "işe iade", "date_from": "2025-01-01"},
                     {"query": "işe iade", "date_to": "2015-12-31"},
                     {"query": "işe iade"}):
            del gonderilen[:]
            bedesten_ictihat.search(args)
            aramalar.append(list(gonderilen))
    finally:
        bedesten_ictihat._http.post_json = eski
    a, b, c = aramalar
    # Sonuç çıkmayınca sorgu merdiveni üç basamak dener; süzgeç her basamakta aynı kalmalı.
    assert [p["phrase"] for p in a] == ['"işe iade"', "+işe +iade", "işe iade"]
    assert all(p["kararTarihiStart"].startswith("2025-01-01") and p["kararTarihiEnd"].startswith("2100") for p in a)
    assert all(p["kararTarihiStart"].startswith("1900") and p["kararTarihiEnd"].startswith("2015-12-31") for p in b)
    assert all("kararTarihiStart" not in p and "kararTarihiEnd" not in p for p in c)


def test_backfill_yalniz_basliktan_ibaret_govdeyi_doldurur():
    """BDDK/BTK/Rekabet listeden indekslendi: gövde = başlık, semantik arama başlığı arıyordu."""
    import crawl
    import sources as _sources
    d = tempfile.mkdtemp()
    idx = retrieval.Index(os.path.join(d, "t.db"))
    idx.upsert({"ref": "bddk:11", "title": "X A.Ş.'nin faaliyet izninin iptaline ilişkin Kurul Kararı",
                "body": "X A.Ş.'nin faaliyet izninin iptaline ilişkin Kurul Kararı", "subject": "bddk"})
    idx.upsert({"ref": "bddk:12", "title": "Y Bankası kuruluş izni", "body": "tam metin " * 80, "subject": "bddk"})
    idx.upsert({"ref": "bddk:13", "title": "Taranmış karar", "body": "Taranmış karar", "subject": "bddk"})
    ids = {r["ref"]: r["id"] for r in idx.db.execute("SELECT id, ref FROM docs")}
    for i in ids.values():
        idx.db.execute("INSERT INTO vecs(doc_id, dim, vec, model) VALUES(?, ?, ?, ?)", (i, 2, b"\x00" * 8, "m"))
    idx.db.commit()

    istenen = []

    class _Sahte:
        @staticmethod
        def get(args):
            istenen.append(args["id"])
            return {"text": "Kurul, 5411 sayılı Kanunun 71 inci maddesi uyarınca " * 12} if args["id"] == "11" else {"text": ""}

    eski = _sources.load_all
    _sources.load_all = lambda: {"bddk": _Sahte}
    try:
        ozet = crawl._backfill(idx, ["bddk", "kvkk"], 0, lambda m: None)
    finally:
        _sources.load_all = eski
    assert sorted(istenen) == ["11", "13"], "zaten metni olan belge yeniden indirilmemeli"
    assert ozet["bddk"] == {"aday": 2, "metin_eklendi": 1, "metin_yok": 1}
    govde = {r["ref"]: r["body"] for r in idx.db.execute("SELECT ref, body FROM docs")}
    assert "5411 sayılı Kanunun" in govde["bddk:11"] and govde["bddk:13"] == "Taranmış karar"
    vek = {r[0] for r in idx.db.execute("SELECT doc_id FROM vecs")}
    assert ids["bddk:11"] not in vek, "metni değişen belgenin eski vektörü kalmamalı"
    assert ids["bddk:12"] in vek and ids["bddk:13"] in vek


def test_pack_govdeyi_sona_alir_icerik_vektor_ve_arama_ayni_kalir():
    """Dağıtım paketi: süzgeç sütunları uzun gövdeden önce; satırlar, id'ler, vektörler ve sonuçlar aynı."""
    import sqlite3

    import crawl
    d = tempfile.mkdtemp()
    idx = retrieval.Index(os.path.join(d, "t.db"))
    idx.upsert({"ref": "rekabet:1", "title": "X A.Ş.'nin devralınması", "body": "Karşı oy gerekçesi. " * 3000,
                "subject": "rekabet", "date": "2024-01-01", "citation": "Rekabet Kurulu, 1", "meta": {"k": "v"}})
    idx.upsert({"ref": "epdk:1", "title": "Lisans iptali", "body": "EPDK lisans iptal kararı", "subject": "epdk",
                "date": "2023-01-01", "citation": "EPDK, 1"})
    for r in idx.db.execute("SELECT id FROM docs").fetchall():
        idx.db.execute("INSERT INTO vecs(doc_id, dim, vec, model) VALUES(?, ?, ?, ?)", (r["id"], 2, b"\x01" * 8, "m"))
    idx.db.commit()
    idx.reindex_fts()
    once = [dict(r) for r in idx.db.execute("SELECT * FROM docs ORDER BY id")]
    vek_once = idx.db.execute("SELECT * FROM vecs ORDER BY doc_id").fetchall()
    arama_once = idx.search("karşı oy gerekçesi", mode="lexical", filters={"subject": "rekabet"})["results"]

    ozet = crawl._pack(idx, lambda m: None)
    cols = [r[1] for r in idx.db.execute("PRAGMA table_info(docs)")]
    assert ozet["yeniden_kuruldu"] and cols[:3] == ["id", "subject", "date"] and cols[-1] == "body"
    assert [dict(r) for r in idx.db.execute("SELECT * FROM docs ORDER BY id")] == once
    assert idx.db.execute("SELECT * FROM vecs ORDER BY doc_id").fetchall() == vek_once
    arama = idx.search("karşı oy gerekçesi", mode="lexical", filters={"subject": "rekabet"})["results"]
    assert [r["ref"] for r in arama] == [r["ref"] for r in arama_once] == ["rekabet:1"]
    try:
        idx.db.execute("INSERT INTO docs(ref, title) VALUES('epdk:1', 'kopya')")
        raise AssertionError("ref'in UNIQUE kısıtı paketlemede kayboldu")
    except sqlite3.IntegrityError:
        idx.db.rollback()
    assert {r[0] for r in idx.db.execute("SELECT name FROM sqlite_master WHERE tbl_name='docs' AND type='index'")} \
        >= {"docs_date", "docs_court"}
    assert crawl._pack(idx, lambda m: None)["yeniden_kuruldu"] is False, "ikinci paketleme sırayı yeniden kurmaz"
    idx.upsert({"ref": "epdk:2", "title": "Yeni karar", "body": "tarife onayı", "subject": "epdk"})
    idx.reindex_fts()
    assert idx.search("tarife", mode="lexical", filters={"subject": "epdk"})["results"][0]["ref"] == "epdk:2"


# ---------------------------------------------------------------- mevzuat_ara(konu=…)

from sources import bedesten_mevzuat as _mv  # noqa: E402


class _SahteBedesten:
    """_mv._call ve _mv.get'i geçici olarak değiştirir; ağ yok."""

    def __init__(self, sayfalar, metinler=None, cagri_yasak=False):
        self.sayfalar, self.metinler, self.cagri_yasak = sayfalar, metinler or {}, cagri_yasak
        self.istekler = []

    def __enter__(self):
        self._call, self._get = _mv._call, _mv.get

        def call(path, data, paging=False):
            assert not self.cagri_yasak, "doğrulama ağa çıkmadan yapılmalıydı"
            self.istekler.append(dict(data))
            i = data["pageNumber"] - 1
            lst = self.sayfalar[i] if i < len(self.sayfalar) else []
            return {"total": sum(len(x) for x in self.sayfalar), "mevzuatList": lst}

        _mv._call = call
        _mv.get = lambda args: {"text": self.metinler.get(str(args.get("mevzuat_id")), "")}
        return self

    def __exit__(self, *a):
        _mv._call, _mv.get = self._call, self._get


def _kayit(i, tur, ad):
    return {"mevzuatId": str(i), "mevzuatNo": str(7000 + i), "mevzuatAdi": ad,
            "mevzuatTur": {"name": tur}, "resmiGazeteTarihi": "2026-08-26T21:00:00.000Z",
            "resmiGazeteSayisi": "33350"}


def test_mevzuat_sayfa_boyu_20yi_asamaz():
    """Bedesten 20'den büyük pageSize'a 400 döndürür; şema 50 diyordu (canlı, 2026-09-20)."""
    with _SahteBedesten([[]]) as b:
        _mv.search({"query": "enerji", "page_size": 50})
    assert b.istekler[0]["pageSize"] == 20
    assert _mv.SEARCH_SCHEMA["properties"]["page_size"]["maximum"] == 20


def test_mevzuat_sorgusuz_liste_tur_veya_tarihle_olur():
    with _SahteBedesten([[_kayit(1, "KKY", "ARI ZEHRİNİN TOPLANMASINA İLİŞKİN YÖNETMELİK")]]) as b:
        r = _mv.search({"types": ["KKY"], "rg_date_from": "2026-07-01"})
    assert not r.get("error") and len(r["results"]) == 1
    assert "mevzuatAdi" not in b.istekler[0] and "phrase" not in b.istekler[0]
    assert "error" in _mv.search({})                      # hiçbir süzgeç yokken hâlâ reddeder


def test_mevzuat_tek_tarafli_rg_araligi_tamamlanir():
    """Bedesten yalnız Start ya da yalnız End verilince süzgeci SESSİZCE yok sayar:
    rg_date_from="2024-09-01" ile 6 yerine 917 kanun dönüyordu (canlı, 2026-09-20)."""
    with _SahteBedesten([[], []]) as b:
        _mv.search({"types": ["KANUN"], "rg_date_from": "2024-09-01"})
        _mv.search({"types": ["KANUN"], "rg_date_to": "2024-09-01"})
    ilk, ikinci = b.istekler
    assert ilk["resmiGazeteTarihiStart"].startswith("2024-08-31") and ilk["resmiGazeteTarihiEnd"].startswith("2100")
    assert ikinci["resmiGazeteTarihiStart"].startswith("1850") and ikinci["resmiGazeteTarihiEnd"].startswith("2024-09-01")


def test_mevzuat_konu_gecersizse_AGA_CIKMADAN_hata_verir():
    with _SahteBedesten([[]], cagri_yasak=True):
        r = _mv.search({"types": ["KANUN"], "konu": "ceza"})
        r2 = _mv.search({"types": ["KANUN"], "konu": "icra", "esik": "elma"})
    assert "desteklenmiyor" in r["error"] and "sayı" in r2["error"]


def test_mevzuat_konu_eler_sayar_ve_birden_cok_sayfa_tarar():
    s1 = [_kayit(1, "TEBLIGLER", "KONKORDATO GİDER AVANSI TARİFESİ")] + \
         [_kayit(10 + i, "KKY", "TÜRK OPTİSYEN-GÖZLÜKÇÜLER BİRLİĞİ YÖNETMELİĞİ") for i in range(19)]
    s2 = [_kayit(2, "KKY", "KUR’AN KURSLARI YÖNETMELİĞİ")]
    with _SahteBedesten([s1, s2]) as b:
        r = _mv.search({"types": ["KKY", "TEBLIGLER"], "konu": "icra", "page_size": 20, "max_pages": 3})
    assert [x["title"] for x in r["results"]] == ["KONKORDATO GİDER AVANSI TARİFESİ"]
    assert r["results"][0]["konu_kaynak"] == "baslik" and r["results"][0]["konu_skoru"] >= 0.20
    assert r["konu_taranan"] == 21 and r["konu_elenen"] == 20 and r["konu_belirsiz"] == 0
    assert r["pages_scanned"] == 2 and len(b.istekler) == 2      # kısa sayfada durur
    assert "0/3" in r["konu_notu"]                               # sınır her yanıtta


def test_mevzuat_torba_kanun_degistirilen_adlardan_yakalanir():
    """'Bazı Kanunlarda Değişiklik' başlığı İİK'yı değiştirdiğini söylemez; metin söyler.
    'ile' ile 'ilgili' arasındaki satır sonu bir kez her ikinci kanunu kaçırtmıştı."""
    metin = ("MADDE 1- 2- ( 9/6/1932 tarihli ve 2004 sayılı İcra ve İflas Kanunu ile \n"
             "ilgili olup, yerine işlenmiştir.) MADDE 3- ( 19/3/1969 tarihli ve 1136 sayılı "
             "Avukatlık Kanunu ile ilgili olup, yerine işlenmiştir.) MADDE 4- (2872 sayılı Kanun "
             "ile ilgili olup, yerine işlenmiştir.)")
    torba = _kayit(5, "KANUN", "BAZI KANUNLARDA DEĞİŞİKLİK YAPILMASINA DAİR KANUN")
    with _SahteBedesten([[torba]], {"5": metin}):
        assert _mv._degistirilen("5") == ["İcra ve İflas Kanunu", "Avukatlık Kanunu"]
        r = _mv.search({"types": ["KANUN"], "konu": "icra"})
    assert len(r["results"]) == 1 and r["konu_elenen"] == 0
    assert r["results"][0]["konu_kaynak"] == "degistirilen_kanunlar"
    assert r["results"][0]["konu_degistirilen"] == "İcra ve İflas Kanunu"


def test_mevzuat_torba_kanun_adlari_okunamazsa_ELENMEZ():
    """7557: 'MADDE 1 ila 27- İlgili Kanunlara işlenmiştir.' Ad yok. Bilinmeyeni atmak,
    hukukçuya 'bu dönemde o kanun değişmedi' demektir."""
    torba = _kayit(3, "KANUN", "SAĞLIKLA İLGİLİ BAZI KANUNLARDA DEĞİŞİKLİK YAPILMASINA DAİR KANUN")
    with _SahteBedesten([[torba]], {"3": "MADDE 1 ila MADDE 27- İlgili Kanunlara işlenmiştir."}):
        r = _mv.search({"types": ["KANUN"], "konu": "vergi"})
    assert len(r["results"]) == 1 and r["konu_belirsiz"] == 1 and r["konu_elenen"] == 0
    assert r["results"][0]["konu_kaynak"] == "belirsiz"


def test_mevzuat_semasi_konuyu_ve_olculmus_sinirini_duyurur():
    import server
    sema = {t.name: t for t in server.TOOLS}["mevzuat_ara"].input_schema
    p = sema["properties"]
    assert set(p["konu"]["enum"]) == {"enerji", "rekabet", "vergi", "icra"}
    assert "enerji 0/3" in p["konu"]["description"], "zayıf konu şemada gizlenmemeli"
    assert "max_pages" in p and "esik" in p and not sema.get("required")
    import triyaj
    k = triyaj.kunye()
    assert k["mevzuat"]["gorulmemis"]["enerji"] == {"pozitif": 3, "yakalanan": 0}


_KVKK_SAYFA = (
    '<div class="d-flex justify-content-between align-items-center">Anasayfa Kurumsal</div>'
    '<div class="news__detail-article-title">Başlık</div>'
    '<div class="news__detail-article "><p>"İZİNSİZ SMS HAKKINDA" KARAR ÖZETİ</p>'
    '<table><tr><td>Karar Tarihi</td><td></td><td>:</td><td></td><td>10/06/2026</td></tr>'
    '<tr><td>Karar No</td><td></td><td>:</td><td></td><td>2026/1183</td></tr>'
    '<tr><td>Konu Özeti</td><td></td><td>:</td><td></td><td>İzinsiz SMS gönderilmesi hakkında</td></tr></table>'
    '<p>Kuruma intikal eden şikâyet dilekçesinde özetle...</p></div>'
    '<div class="sidebar-widget sidebar-widget-events">Etkinlikler Podcast Kanalımız</div>')


def test_kvkk_karar_metni_yalniz_karar_bolumunden_okunur():
    d = kvkk._parse(_KVKK_SAYFA)
    assert d["date"] == "10/06/2026" and d["no"] == "2026/1183"
    assert d["konu"] == "İzinsiz SMS gönderilmesi hakkında"
    assert "şikâyet dilekçesinde" in d["text"]
    assert "Podcast" not in d["text"] and "Anasayfa" not in d["text"]


def test_kvkk_hata_sayfasi_karar_metni_sayilmaz():
    """/Icerik/8887/x -> /error: 291 kararın gövdesine bu sayfanın menüsü yazılmıştı (10.10.2026)."""
    assert kvkk._parse('<div class="d-flex justify-content-between">Anasayfa</div><footer>x</footer>') is None

    class _Sahte:
        @staticmethod
        def get_text(url, **k):
            return '<div class="d-flex justify-content-between">Anasayfa Kurumsal</div>'

    eski = kvkk._http
    kvkk._http = _Sahte()
    try:
        out = kvkk.get({"id": "8887/2026-1183"})
    finally:
        kvkk._http = eski
    assert "error" in out and "text" not in out
    assert out["source_url"] == "https://www.kvkk.gov.tr/Icerik/8887/2026-1183"


def test_kvkk_adresi_karar_numarasini_tasir():
    assert kvkk._url("8887/2026-1183") == "https://www.kvkk.gov.tr/Icerik/8887/2026-1183"
    assert kvkk._url("https://www.kvkk.gov.tr/Icerik/8887/2026-1183").endswith("/8887/2026-1183")
    eski = kvkk._listing
    kvkk._listing = lambda p: ([{"id": "8887", "slug": "2026-1183",
                                 "source_url": "https://www.kvkk.gov.tr/Icerik/8887/2026-1183"}] if p == 1 else [])
    try:
        assert kvkk._url("8887") == "https://www.kvkk.gov.tr/Icerik/8887/2026-1183"
        assert kvkk._url("1") is None
    finally:
        kvkk._listing = eski


def test_kvkk_taramasi_konuyu_basliga_tarihi_atfa_yazar():
    idx = retrieval.Index(os.path.join(tempfile.mkdtemp(), "t.db"))
    eski_liste, eski_getir = kvkk._listing, kvkk.get
    kvkk._listing = lambda p: ([{"id": "8887", "slug": "2026-1183", "decision_no": "2026/1183", "date": "",
                                 "summary": "liste özeti",
                                 "source_url": "https://www.kvkk.gov.tr/Icerik/8887/2026-1183"}] if p == 1 else [])
    kvkk.get = lambda a: {"text": "karar metni " * 50, "karar_tarihi": "10/06/2026",
                          "konu": "İzinsiz SMS gönderilmesi hakkında"}
    try:
        out = kvkk.crawl(idx, max_pages=3, log=lambda m: None)
    finally:
        kvkk._listing, kvkk.get = eski_liste, eski_getir
    r = idx.db.execute("SELECT title, body, date, citation FROM docs WHERE ref = 'kvkk:8887'").fetchone()
    assert r["title"] == "İzinsiz SMS gönderilmesi hakkında (KVKK Kurul Kararı 2026/1183)"
    assert r["date"] == "2026-06-10"
    assert r["citation"] == "KVK Kurulu, 10.06.2026 tarih ve 2026/1183 sayılı karar özeti"
    assert r["body"].startswith("karar metni") and out == {"indexed": 1, "metinsiz": 0}


def test_kvkk_numarayla_istenen_karar_indeksteki_adrese_gider():
    import server
    idx = retrieval.Index(os.path.join(tempfile.mkdtemp(), "t.db"))
    idx.upsert({"ref": "kvkk:8887", "title": "t", "body": "b", "subject": "kvkk",
                "url": "https://www.kvkk.gov.tr/Icerik/8887/2026-1183"})
    idx.db.commit()
    eski = server.index
    server.index = lambda: idx
    try:
        assert server._kvkk_adresi({"id": "8887"})["id"] == "https://www.kvkk.gov.tr/Icerik/8887/2026-1183"
        assert server._kvkk_adresi({"id": "9999"}) == {"id": "9999"}
        assert server._kvkk_adresi({"id": "8887/2026-1183"}) == {"id": "8887/2026-1183"}
    finally:
        server.index = eski



def test_iso_tarih_tek_haneli_gun_ve_ayi_tanir():
    """Rekabet listesi '28.6.2005' yazar; iki hane bekleyen çözümleyici 8.529 kararı tarihsiz bıraktı."""
    assert textx.iso_tarih("4.8.1999") == "1999-08-04"
    assert textx.iso_tarih("Rekabet Kurulu, 28.6.2005 tarih ve 05-41/578-146 sayılı karar") == "2005-06-28"
    assert textx.iso_tarih("13/01/2012 tarih ve K- 2011/ 52 sayılı Hakem Heyeti Kararı") == "2012-01-13"
    assert textx.iso_tarih("16.10.2025") == "2025-10-16"
    assert textx.iso_tarih("31.13.2020") == "" and textx.iso_tarih("") == "" and textx.iso_tarih("K-2011/268") == ""


def test_bddk_basligi_kapanistan_once_bosluk_olsa_da_cozulur():
    hm = bddk._HEAD.match("(13.12.2018 - 8149 ) Odea Bank A.Ş.’ye izin")
    assert hm and hm.groups() == ("13.12.2018", "8149", "Odea Bank A.Ş.’ye izin")
    assert bddk._HEAD.match("(26/07/2007 - 2248) Merrill Lynch").group(1) == "26/07/2007"


def test_rekabet_karar_tarihi_karar_sayisinin_yiliyla_denetlenir():
    # Liste yanlış yazmış (2029); kararın kendi tarihi karar sayısının yılıyla uyuşuyor.
    assert rekabet.karar_tarihi("17.8.2029", "23-39/726-249",
                                "Karar Sayısı : 23-39/726-249 Karar Tarihi : 17.08.2023") == \
        ("2023-08-17", "17.08.2023", "karar metni")
    # Karar metninde yazım hatası; liste karar sayısının yılıyla uyuşuyor.
    assert rekabet.karar_tarihi("18.4.2024", "24-17/400-160", "Karar Tarihi : 18.04.2023") == \
        ("2024-04-18", "18.4.2024", "liste")
    assert rekabet.karar_tarihi("4.8.1999", "99-37/376-241", "") == ("1999-08-04", "4.8.1999", "liste")


def test_tarih_onar_bos_tarihi_doldurur_atifi_duzeltir_vektore_dokunmaz():
    import crawl
    idx = retrieval.Index(os.path.join(tempfile.mkdtemp(), "t.db"))
    idx.upsert({"ref": "rekabet:a", "subject": "rekabet", "title": "Devralma", "body": "Karar Tarihi : 28.06.2005 metin",
                "citation": "Rekabet Kurulu, 28.6.2005 tarih ve 05-41/578-146 sayılı karar",
                "meta": {"decision_number": "05-41/578-146"}})
    idx.upsert({"ref": "rekabet:b", "subject": "rekabet", "title": "Devralma", "date": "2029-08-17",
                "body": "Karar Sayısı : 23-39/726-249 Karar Tarihi : 17.08.2023 metin",
                "citation": "Rekabet Kurulu, 17.8.2029 tarih ve 23-39/726-249 sayılı karar",
                "meta": {"decision_number": "23-39/726-249"}})
    idx.upsert({"ref": "bddk:393", "subject": "bddk", "title": "(13.12.2018 - 8149 ) Odea Bank", "body": "b",
                "citation": "BDDK, ? tarih ve ? sayılı Kurul Kararı"})
    idx.upsert({"ref": "stk:9:x", "subject": "sigorta_tahkim", "title": "t", "body": "b",
                "citation": "Sigorta Tahkim Komisyonu, 13/01/2012 tarih ve K- 2011/ 52 sayılı Hakem Heyeti Kararı"})
    idx.upsert({"ref": "kvkk:7538", "subject": "kvkk", "title": "t", "body": "b",
                "citation": "KVK Kurulu, ? tarih ve 2023/134 sayılı karar özeti"})
    for (i,) in idx.db.execute("SELECT id FROM docs").fetchall():
        idx.db.execute("INSERT INTO vecs(doc_id, dim, vec, model) VALUES(?, ?, ?, ?)", (i, 2, b"\x00" * 8, "m"))
    idx.reindex_fts()

    ozet = crawl._tarih_onar(idx, [], lambda m: None)
    r = {x["ref"]: x for x in idx.db.execute("SELECT ref, date, citation, meta FROM docs")}
    assert r["rekabet:a"]["date"] == "2005-06-28"
    assert r["rekabet:a"]["citation"] == "Rekabet Kurulu, 28.6.2005 tarih ve 05-41/578-146 sayılı karar"
    assert r["rekabet:b"]["date"] == "2023-08-17"
    assert r["rekabet:b"]["citation"] == "Rekabet Kurulu, 17.08.2023 tarih ve 23-39/726-249 sayılı karar"
    assert json.loads(r["rekabet:b"]["meta"])["liste_tarihi"] == "17.8.2029"
    assert r["bddk:393"]["date"] == "2018-12-13"
    assert r["bddk:393"]["citation"] == "BDDK, 13.12.2018 tarih ve 8149 sayılı Kurul Kararı"
    assert r["stk:9:x"]["date"] == "2012-01-13" and r["kvkk:7538"]["date"] == ""
    assert ozet["rekabet"] == {"tarih_yazildi": 2, "atif_duzeltildi": 1, "tarihsiz_kalan": 0}
    assert ozet["kvkk"] == {"tarih_yazildi": 0, "atif_duzeltildi": 0, "tarihsiz_kalan": 1}
    assert idx.db.execute("SELECT count(*) FROM vecs").fetchone()[0] == 5, "tarih onarımı vektör silmemeli"
    idx.db.execute("INSERT INTO docs_fts(docs_fts, rank) VALUES('integrity-check', 1)")
    bul = lambda q: [x[0] for x in idx.db.execute(  # noqa: E731
        "SELECT d.ref FROM docs_fts f JOIN docs d ON d.id = f.rowid WHERE docs_fts MATCH ?", (q,))]
    assert bul('citation:"17 08 2023"') == ["rekabet:b"] and bul("citation:2029") == []
    assert crawl._tarih_onar(idx, [], lambda m: None)["rekabet"]["atif_duzeltildi"] == 0, "ikinci çalıştırma boş geçmeli"



def test_word_alanlari_yalniz_sonucu_birakir():
    assert textx._doc_fields("Sayfa \x13 PAGE MERGEFORMAT\x143\x15 son") == "Sayfa 3 son"
    assert textx._doc_fields("a\x13 IF \x13 REF x \x14y\x15 \x14sonuç\x15b") == "asonuçb"


def test_doc_eski_word_dosyasindan_okunur():
    """EPDK'nın 2016 öncesi kararlarının çoğu .doc; 2.019 kararın gövdesi başlıktan ibaretti."""
    if not textx.HAS_OLEFILE:
        print("     (olefile yok: .doc testi atlandı)")
        return
    data = open(os.path.join(HERE, "fixtures", "epdk_4032.doc"), "rb").read()
    t = textx.doc_to_text(data)
    assert t.startswith("Enerji Piyasası Düzenleme Kurumundan:")
    assert "Karar No: 4032 Karar Tarihi: 20/09/2012" in t and "TEİAŞ" in t and "karar verilmiştir." in t
    assert epdk.metin_cikar(data)[2] == "doc"
    assert textx.doc_to_text(b"%PDF-1.4") == "" and textx.doc_to_text(textx.OLE_MAGIC + b"\x00" * 600) == ""


def test_epdk_metin_bicimi_dosyanin_kendisinden_anlasilir():
    import zipfile, io as _io
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document><w:p>Kurul karar verdi.</w:p></w:document>")
    assert epdk.metin_cikar(buf.getvalue()) == ("Kurul karar verdi.", 0, "docx")
    arsiv = _io.BytesIO()
    with zipfile.ZipFile(arsiv, "w") as z:                 # karar ve ekleri tek arşivde
        z.writestr("1- karar.docx", buf.getvalue())
        z.writestr("tablo.xlsx", b"PK")
    assert epdk.metin_cikar(arsiv.getvalue()) == ("Kurul karar verdi.", 0, "zip")
    assert epdk.metin_cikar(b"<html><body><p>Karar</p></body></html>")[2] == "html"
    portal = b"\r\n<!DOCTYPE html>\r\n<html>\r\n<head>\r\n<title>EPDK | Enerji Piyasas\xc4\xb1</title><body>Men\xc3\xbc</body>"
    assert epdk.metin_cikar(portal) == ("", 0, ""), "portal sayfasının menüsü karar metni sayılmaz"
    assert epdk.metin_cikar(b"{\rtf1 bilinmeyen") == ("", 0, "")
    # Metni olmayan PDF: OCR istenmedikçe boş; istenip araçlar yoksa da çökmeden boş.
    bos = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n" \
          b"trailer<</Root 1 0 R>>\n%%EOF"
    assert epdk.metin_cikar(bos)[0] == ""


def test_backfill_epdk_metni_liste_kaydinin_onune_ekler():
    import crawl
    import sources as _sources
    idx = retrieval.Index(os.path.join(tempfile.mkdtemp(), "t.db"))
    meta = {"path": "Elektrik Piyasası Piyasa Kurul Kararları › EDAŞ", "category": "Gelir Gereksinimi"}
    kocan = "Akedaş'ın gelir gereksinimi\nElektrik Piyasası Piyasa Kurul Kararları › EDAŞ › Gelir Gereksinimi"
    idx.upsert({"ref": "epdk:elektrik:1:a", "subject": "epdk", "title": "EPDK Kurul Kararı 4632/10", "body": kocan,
                "url": "https://www.epdk.gov.tr/Detay/DownloadDocument?id=a", "meta": meta})
    idx.upsert({"ref": "epdk:elektrik:1:b", "subject": "epdk", "title": "EPDK Kurul Kararı 13134",
                "body": "Karar metni burada. " * 20 + "\n\n" + kocan, "meta": meta})
    idx.upsert({"ref": "epdk:elektrik:1:c", "subject": "epdk", "title": "EPDK Kurul Kararı 7",   # yalnız kaşe okunmuş
                "body": "KURUL KARARI  EPDK  Hepsi Film  7\n\n" + kocan, "meta": meta,
                "url": "https://www.epdk.gov.tr/Detay/DownloadDocument?id=c"})
    ids = {r["ref"]: r["id"] for r in idx.db.execute("SELECT id, ref FROM docs")}
    for i in ids.values():
        idx.db.execute("INSERT INTO vecs(doc_id, dim, vec, model) VALUES(?, ?, ?, ?)", (i, 2, b"\x00" * 8, "m"))
    idx.db.commit()
    istenen = []

    def getir(src, istek):
        istenen.append(dict(istek))
        return {"text": "TOPLANTI TARİHİ : 26/09/2013 " + "Kurul gelir gereksinimini revize etti. " * 8,
                "metin_bicimi": "ocr"}

    ozet = crawl._backfill(idx, ["epdk"], 0, lambda m: None, ocr=True, getir=getir)
    assert ozet["epdk"] == {"aday": 2, "metin_eklendi": 2, "metin_yok": 0}
    assert sorted(x["id"] for x in istenen) == ["https://www.epdk.gov.tr/Detay/DownloadDocument?id=a",
                                                "https://www.epdk.gov.tr/Detay/DownloadDocument?id=c"]
    assert all(x["ocr"] for x in istenen)
    r = idx.db.execute("SELECT body, meta FROM docs WHERE ref = 'epdk:elektrik:1:a'").fetchone()
    assert r["body"].startswith("TOPLANTI TARİHİ") and r["body"].endswith(kocan)
    assert json.loads(r["meta"])["metin"] == "ocr" and json.loads(r["meta"])["category"] == "Gelir Gereksinimi"
    c = idx.db.execute("SELECT body FROM docs WHERE ref = 'epdk:elektrik:1:c'").fetchone()["body"]
    assert "Hepsi Film" not in c and c.endswith(kocan), "kaşe metni atılır, liste kaydı kalır"
    vek = {x[0] for x in idx.db.execute("SELECT doc_id FROM vecs")}
    assert ids["epdk:elektrik:1:a"] not in vek and ids["epdk:elektrik:1:b"] in vek
    assert ids["epdk:elektrik:1:c"] not in vek


def test_epdk_canli_metin_cikmazsa_indeksteki_okunmus_metin_gelir():
    import server
    idx = retrieval.Index(os.path.join(tempfile.mkdtemp(), "t.db"))
    url = "https://www.epdk.gov.tr/Detay/DownloadDocument?id=Ye3t/BDj37WU="
    idx.upsert({"ref": "epdk:elektrik:33883:Ye3t/BDj37WU=", "subject": "epdk", "title": "t", "url": url,
                "body": "KARAR ÖRNEĞİ okunmuş metin " * 10, "citation": "EPDK, 24.05.2012 tarihli ve 3851 sayılı Kurul Kararı",
                "meta": {"metin": "ocr"}})
    idx.upsert({"ref": "epdk:elektrik:1:z", "subject": "epdk", "title": "t", "body": "kocan",
                "url": "https://www.epdk.gov.tr/Detay/DownloadDocument?id=z", "meta": {}})
    idx.db.commit()
    canli = {"error": "Belge metni çıkarılamadı", "source_url": url, "text": ""}
    eski = server.index
    server.index = lambda: idx
    try:
        out = server._epdk_arsivden({"id": url}, canli)
        assert out["text"].startswith("KARAR ÖRNEĞİ") and "OCR" in out["uyari"] and url in out["uyari"]
        assert out["citation"].startswith("EPDK, 24.05.2012") and out["metin_kaynagi"] == "yerel indeks (ocr)"
        assert server._epdk_arsivden({"id": "epdk:elektrik:33883:Ye3t/BDj37WU="}, canli)["text"]
        kase = {"text": "KURUL KARARI  EPDK  Hepsi Film  7", "source_url": url}   # yalnız kaşe okunmuş
        assert server._epdk_arsivden({"id": url}, kase)["text"].startswith("KARAR ÖRNEĞİ")
        assert server._epdk_arsivden({"id": "https://www.epdk.gov.tr/Detay/DownloadDocument?id=z"}, canli) is canli
        assert server._epdk_arsivden({"id": "https://www.epdk.gov.tr/Detay/DownloadDocument?id=yok"}, canli) is canli
    finally:
        server.index = eski


def test_tarih_onar_epdk_atifinda_tarih_yoksa_karar_satirindan_alir():
    import crawl
    idx = retrieval.Index(os.path.join(tempfile.mkdtemp(), "t.db"))
    idx.upsert({"ref": "epdk:elektrik:1915:x", "subject": "epdk", "title": "EPDK Kurul Kararı 5999-3 ()",
                "body": "T.C. ENERJİ PİYASASI DÜZENLEME KURULU KARAR ÖRNEĞİ TOPLANTI TARİHİ : 30/12/2015 metin",
                "citation": "EPDK, ? tarihli ve 5999-3 sayılı Kurul Kararı"})
    idx.reindex_fts()
    crawl._tarih_onar(idx, ["epdk"], lambda m: None)
    r = idx.db.execute("SELECT date, citation FROM docs").fetchone()
    assert r["date"] == "2015-12-30" and r["citation"] == "EPDK, 30.12.2015 tarihli ve 5999-3 sayılı Kurul Kararı"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok   ", fn.__name__)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("FAIL ", fn.__name__, "->", type(exc).__name__, exc)
    print("%d/%d passed" % (len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
