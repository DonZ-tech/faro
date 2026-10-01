import os, tempfile
from pathlib import Path
from datetime import date
os.environ["FARO_CONFIG_DIR"] = tempfile.mkdtemp()
os.environ["FARO_DATA_DIR"] = tempfile.mkdtemp()
os.environ["FARO_CAMPAIGNS_DIR"] = tempfile.mkdtemp()

from faro import textnorm, pseudo, evidence
from faro.campaign import Campaign, KeyDate
from faro.db import connect, upsert_account, add_edge
from faro.paths import campaign_data, ensure_dirs
from faro.analyze import bursts, dupes, graph


def test_normalize_arabic_and_urls():
    t = "شاهد الحريق سبتة https://t.me/x/1 #سبتة"
    n = textnorm.normalize(t)
    assert "https" not in n and "سبته" in n
    assert textnorm.urls(t) == ["https://t.me/x/1"]
    assert textnorm.guess_lang(t) == "ar"
    assert textnorm.guess_lang("l7rig sebta ghda") == "arabizi"


def test_pseudonym_stable_and_case_insensitive():
    a = pseudo.pseudonym("telegram", "@Canal_X")
    b = pseudo.pseudonym("telegram", "canal_x")
    assert a == b and a.startswith("te_")
    assert pseudo.pseudonym("tiktok", "canal_x") != a


def test_evidence_roundtrip(tmp_path):
    p, h = evidence.store_raw(tmp_path, "telegram", "c.messages", {"a": 1})
    assert p.exists() and len(h) == 64
    res = evidence.verify_raw(tmp_path)
    assert res and all(ok for _, ok in res)
    p.write_bytes(b"tampered")
    assert not all(ok for _, ok in evidence.verify_raw(tmp_path))


def test_campaign_roundtrip():
    ensure_dirs()
    c = Campaign(name="t1", title="T", created=date(2026, 8, 28))
    c.lexicon.terms["es"] = ["ceuta"]
    c.key_dates.append(KeyDate(date=date(2026, 9, 23), label="conv"))
    c.save()
    c2 = Campaign.load("t1")
    assert c2.lexicon.all_terms() == ["ceuta"] and c2.key_dates[0].label == "conv"
    assert "t1" in Campaign.list_names()


def _seed_posts(name):
    ensure_dirs()
    c = Campaign(name=name, title="T", created=date(2026, 8, 28)); c.save()
    data = campaign_data(name)
    now = "2026-08-28T10:00:00+00:00"
    text = "convocatoria general para entrar a ceuta el dia 23 de septiembre a las 6 de la mañana"
    with connect(data) as con:
        for i, acc in enumerate(["a", "b", "c", "d"]):
            ps = pseudo.pseudonym("telegram", acc)
            upsert_account(con, ps, "telegram", acc, now, kind="channel")
            con.execute("INSERT INTO posts(id,platform,account,posted_at,captured_at,text,text_norm,lang) VALUES (?,?,?,?,?,?,?,?)",
                        (f"telegram:{i}", "telegram", ps, f"2026-08-28T10:0{i}:00+00:00", now, text, textnorm.normalize(text + (" ya" if i == 3 else "")), "es"))
        add_edge(con, pseudo.pseudonym("telegram", "a"), pseudo.pseudonym("telegram", "b"), "forward", now)
    return c


def test_bursts_and_dupes_and_graph():
    c = _seed_posts("t2")
    b = bursts.text_bursts(c, window_min=10, min_accounts=3)
    assert b and b[0]["n_accounts"] == 3
    d = dupes.text_near_dupes(c, threshold=0.6)
    assert any(p["sim"] > 0.6 for p in d)
    G = graph.build(c)
    assert G.number_of_edges() >= 1
    r = graph.rank(G)
    assert r and "pagerank" in r[0]


def test_arabic_evasion_fragmentation():
    assert textnorm.normalize("الهجـ..ـمة") == textnorm.normalize("الهجمة")
    assert "الهجمه" in textnorm.normalize("الهجـ..ـمة")


def test_onion_parse_results_filters_engine_and_search_links():
    from faro.collect import onion
    host = "a" * 56 + ".onion"
    engine = "http://" + "b" * 56 + ".onion/search/?q=x"
    html = (f'<a href="http://{host}/foro">Foro de pruebas</a>'
            f'<a href="{engine}">El buscador</a>'
            f'<a href="http://{host}/foro/">Foro de pruebas dup</a>'
            '<a href="https://example.com">clearnet</a>')
    res = onion.parse_results(html, engine)
    assert len(res) == 1 and res[0]["title"] == "Foro de pruebas"
