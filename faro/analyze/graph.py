"""Grafo de amplificación: cuentas como nodos; forwards, menciones y duplicados como aristas.
Comunidades (Louvain) y centralidad. Exporta GEXF para Gephi."""
from __future__ import annotations
from pathlib import Path
import networkx as nx
from ..campaign import Campaign
from ..db import connect
from ..paths import campaign_data


def build(camp: Campaign, kinds: tuple[str, ...] = ("forward", "mention", "dup_text", "dup_media")) -> nx.DiGraph:
    G = nx.DiGraph()
    with connect(campaign_data(camp.name)) as con:
        for pseudo, platform, kind, followers, seed in con.execute("SELECT pseudo, platform, kind, followers, seed FROM accounts"):
            G.add_node(pseudo, platform=platform, kind=kind or "", followers=followers or 0, seed=int(seed or 0))
        q = f"SELECT src, dst, kind, weight FROM edges WHERE kind IN ({','.join('?' * len(kinds))})"
        for src, dst, kind, w in con.execute(q, kinds):
            if G.has_edge(src, dst):
                G[src][dst]["weight"] += w
                G[src][dst]["kinds"] = G[src][dst]["kinds"] + "," + kind
            else:
                G.add_edge(src, dst, weight=w, kinds=kind)
        for (acc, n) in con.execute("SELECT account, COUNT(*) FROM posts GROUP BY account"):
            if acc in G:
                G.nodes[acc]["posts"] = n
    G.remove_nodes_from([n for n, d in G.degree() if d == 0])
    return G


def communities(G: nx.DiGraph) -> dict[str, int]:
    try:
        import community as louvain
        return louvain.best_partition(G.to_undirected(), weight="weight", random_state=7)
    except Exception:
        parts = nx.algorithms.community.greedy_modularity_communities(G.to_undirected(), weight="weight")
        return {n: i for i, c in enumerate(parts) for n in c}


def rank(G: nx.DiGraph, top: int = 25) -> list[dict]:
    if G.number_of_nodes() == 0:
        return []
    pr = nx.pagerank(G, weight="weight")
    indeg = dict(G.in_degree(weight="weight"))
    outdeg = dict(G.out_degree(weight="weight"))
    part = communities(G)
    rows = []
    for n in G.nodes:
        rows.append({"account": n, "platform": G.nodes[n].get("platform"), "pagerank": round(pr[n], 5),
                     "amplified_by": indeg.get(n, 0), "amplifies": outdeg.get(n, 0),
                     "posts": G.nodes[n].get("posts", 0), "community": part.get(n, -1), "seed": G.nodes[n].get("seed", 0)})
    return sorted(rows, key=lambda r: -r["pagerank"])[:top]


def export(G: nx.DiGraph, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    part = communities(G)
    for n, c in part.items():
        G.nodes[n]["community"] = c
    nx.write_gexf(G, out)
    return out
