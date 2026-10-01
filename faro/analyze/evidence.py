"""Grafo de evidencia: nodos y aristas tipados, cada uno con su procedencia.

Nodos: account (seudónimo), post (solo los que casan con el léxico), domain, hashtag, term,
date (fechas clave), entity (documentada por terceros, campaigns/<c>/entities.yml), source (informe).
Aristas: posted, links_to, uses_hashtag, uses_term, active_on, forward, mention, dup_text,
dup_media, documented_by, rotated_to, announces, frames, amplifies, same_as.
Cada arista lleva `evidence`: IDs de post, hashes y/o URL de fuente. Nunca handles reales.
"""
from __future__ import annotations
import json
from pathlib import Path
import networkx as nx
import yaml
from ..campaign import Campaign
from ..db import connect
from ..paths import campaign_data, campaign_dir
from ..textnorm import normalize, hashtags as extract_hashtags


def _load_entities(camp: Campaign) -> dict:
    p = campaign_dir(camp.name) / "entities.yml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}


def build(camp: Campaign, include_posts: bool = True, max_posts: int = 600) -> nx.DiGraph:
    G = nx.DiGraph(campaign=camp.name, title=camp.title)
    ent = _load_entities(camp)
    terms = {normalize(t): t for t in camp.lexicon.all_terms() if normalize(t)}

    # Fuentes y entidades de terceros
    for sid, s in (ent.get("sources") or {}).items():
        G.add_node(f"source:{sid}", type="source", label=s["title"], url=s["url"], date=str(s.get("date", "")))
    def _src(sid: str) -> str:
        nid = f"source:{sid}"
        if nid not in G:  # fuente implícita (p. ej. "corpus" = captación propia de faro)
            G.add_node(nid, type="source", label="Corpus propio (faro)" if sid == "corpus" else sid, url="", date="")
        return nid

    for e in ent.get("entities") or []:
        G.add_node(e["id"], type="entity", label=e["label"], platform=e.get("platform", ""), kind=e.get("kind", ""),
                   role=e.get("role", ""), members=e.get("members") or 0, notes=e.get("notes", ""))
        for sid in e.get("sources", []):
            G.add_edge(e["id"], _src(sid), type="documented_by", evidence=json.dumps({"source": sid}))
    for t in ent.get("terms") or []:
        G.add_node(t["id"], type="term", label=t["label"], meaning=t.get("meaning", ""))
        for sid in t.get("sources", []):
            G.add_edge(t["id"], _src(sid), type="documented_by", evidence=json.dumps({"source": sid}))
    for kd in camp.key_dates:
        G.add_node(f"date:{kd.date}", type="date", label=f"{kd.date} · {kd.label}", date=str(kd.date))
    for r in ent.get("relations") or []:
        for n in (r["src"], r["dst"]):
            if n not in G:
                G.add_node(n, type="date" if n.startswith("date:") else "entity", label=n)
        G.add_edge(r["src"], r["dst"], type=r["kind"], evidence=json.dumps({"sources": r.get("sources", []), "notes": r.get("notes", "")}, ensure_ascii=False))

    # Datos captados por faro
    with connect(campaign_data(camp.name)) as con:
        accounts = {r["pseudo"]: dict(r) for r in con.execute("SELECT pseudo, platform, kind, followers, seed FROM accounts")}
        rows = con.execute("SELECT id, account, platform, posted_at, captured_at, text, text_norm, url, views, raw_sha256 FROM posts ORDER BY posted_at").fetchall()
        n_posts = 0
        for r in rows:
            tn = r["text_norm"] or ""
            hit = [terms[k] for k in terms if k in tn]
            if not hit:
                continue
            acc = r["account"]
            if acc not in G:
                a = accounts.get(acc, {})
                G.add_node(acc, type="account", label=acc, platform=a.get("platform", r["platform"]), kind=a.get("kind") or "",
                           followers=a.get("followers") or 0, seed=int(a.get("seed") or 0), posts=0)
            G.nodes[acc]["posts"] = G.nodes[acc].get("posts", 0) + 1
            ev = {"post": r["id"], "url": r["url"], "posted_at": r["posted_at"], "captured_at": r["captured_at"], "sha256": r["raw_sha256"]}
            if include_posts and n_posts < max_posts:
                G.add_node(r["id"], type="post", label=(r["text"] or "")[:80], platform=r["platform"], posted_at=r["posted_at"],
                           url=r["url"] or "", views=r["views"] or 0, sha256=r["raw_sha256"] or "", terms=", ".join(hit[:6]))
                G.add_edge(acc, r["id"], type="posted", evidence=json.dumps(ev))
                n_posts += 1
                post_node = r["id"]
            else:
                post_node = None
            for h in hit[:6]:
                tid = f"lex:{normalize(h)}"
                if tid not in G:
                    G.add_node(tid, type="term", label=h, meaning="léxico de campaña")
                src = post_node or acc
                if G.has_edge(src, tid):
                    G[src][tid]["weight"] = G[src][tid].get("weight", 1) + 1
                else:
                    G.add_edge(src, tid, type="uses_term", weight=1, evidence=json.dumps(ev))
            for h in extract_hashtags(r["text"]):
                hid = f"hashtag:#{h}"
                if hid not in G:
                    G.add_node(hid, type="hashtag", label=f"#{h}")
                src = post_node or acc
                if G.has_edge(src, hid):
                    G[src][hid]["weight"] += 1
                else:
                    G.add_edge(src, hid, type="uses_hashtag", weight=1, evidence=json.dumps(ev))
            day = (r["posted_at"] or "")[:10]
            dnode = f"date:{day}"
            if dnode in G:
                if G.has_edge(acc, dnode):
                    G[acc][dnode]["weight"] += 1
                else:
                    G.add_edge(acc, dnode, type="active_on", weight=1, evidence=json.dumps(ev))
        # Enlaces a dominios (solo de posts relevantes ya en el grafo)
        for r in con.execute("""SELECT l.post_id, l.url, l.domain, p.account, d.registrar, d.created, d.registrant_country
                                FROM links l JOIN posts p ON p.id=l.post_id LEFT JOIN domains d ON d.domain=l.domain"""):
            if not r["domain"] or (r["post_id"] not in G and r["account"] not in G):
                continue
            did = f"domain:{r['domain']}"
            if did not in G:
                G.add_node(did, type="domain", label=r["domain"], registrar=r["registrar"] or "", created=(r["created"] or "")[:10], country=r["registrant_country"] or "")
            src = r["post_id"] if r["post_id"] in G else r["account"]
            if G.has_edge(src, did):
                G[src][did]["weight"] += 1
            else:
                G.add_edge(src, did, type="links_to", weight=1, evidence=json.dumps({"post": r["post_id"], "url": r["url"]}))
        # Aristas de coordinación ya calculadas
        for s, d, k, w, fs in con.execute("SELECT src, dst, kind, weight, first_seen FROM edges"):
            for n in (s, d):
                if n not in G:
                    a = accounts.get(n, {})
                    G.add_node(n, type="account", label=n, platform=a.get("platform", ""), kind=a.get("kind") or "", followers=a.get("followers") or 0, seed=int(a.get("seed") or 0), posts=0)
            G.add_edge(s, d, type=k, weight=w, evidence=json.dumps({"first_seen": fs, "count": w}))
        # same_as: cuenta captada ↔ entidad documentada (por handle real, sin exponerlo)
        ident = {(r["platform"], r["handle"].lstrip("@").lower()): r["pseudo"] for r in con.execute("SELECT pseudo, platform, handle FROM identities")}
        for e in ent.get("entities") or []:
            key = (e.get("platform"), e["label"].lstrip("@").lower())
            ps = ident.get(key)
            if ps and ps in G:
                G.add_edge(ps, e["id"], type="same_as", evidence=json.dumps({"match": "handle"}))
    return G


def export(G: nx.DiGraph, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    clean = nx.DiGraph(**G.graph)
    for n, d in G.nodes(data=True):
        clean.add_node(n, **{k: ("" if v is None else v) for k, v in d.items()})
    for u, v, d in G.edges(data=True):
        clean.add_edge(u, v, **{k: ("" if x is None else x) for k, x in d.items()})
    paths = {"gexf": out_dir / "evidence.gexf", "graphml": out_dir / "evidence.graphml", "json": out_dir / "evidence.json"}
    nx.write_gexf(clean, paths["gexf"])
    nx.write_graphml(clean, paths["graphml"])
    data = {"campaign": G.graph, "nodes": [{"id": n, **d} for n, d in clean.nodes(data=True)],
            "links": [{"source": u, "target": v, **d} for u, v, d in clean.edges(data=True)]}
    paths["json"].write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    paths["html"] = out_dir / "evidence.html"
    paths["html"].write_text(_viewer(data), encoding="utf-8")
    return paths


def summary(G: nx.DiGraph) -> dict:
    from collections import Counter
    return {"nodes": G.number_of_nodes(), "edges": G.number_of_edges(),
            "node_types": dict(Counter(d.get("type") for _, d in G.nodes(data=True))),
            "edge_types": dict(Counter(d.get("type") for _, _, d in G.edges(data=True)))}


def _viewer(data: dict) -> str:
    tpl = (Path(__file__).parent / "evidence_viewer.html").read_text(encoding="utf-8")
    return tpl.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
