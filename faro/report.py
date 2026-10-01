"""Informe en Markdown con anexo de evidencias. Solo seudónimos; nunca handles reales."""
from __future__ import annotations
from pathlib import Path
from jinja2 import Template
from .campaign import Campaign
from .db import connect
from .paths import campaign_data
from .evidence import utcnow, verify_raw
from .analyze import timeline, bursts, dupes, graph

TEMPLATE = Template("""# {{ camp.title }} — informe técnico (borrador automático)

*Generado por faro {{ version }} el {{ now }}. Campaña `{{ camp.name }}`. Firma: Don Z.*

## 1. Alcance

{{ camp.description or "—" }}

Ventana analizada: {{ first_post or "—" }} → {{ last_post or "—" }}. Fechas clave:
{% for kd in camp.key_dates %}- {{ kd.date }}: {{ kd.label }}
{% endfor %}

## 2. Corpus

| Plataforma | Cuentas | Publicaciones | Con media | Enlaces |
|---|---:|---:|---:|---:|
{% for r in corpus %}| {{ r.platform }} | {{ r.accounts }} | {{ r.posts }} | {{ r.media }} | {{ r.links }} |
{% endfor %}

Integridad de evidencias: {{ ok_raw }}/{{ n_raw }} capturas verifican su SHA-256.

![timeline](figures/timeline.png)

## 3. Señales de coordinación

Criterio: ninguna señal aislada basta; se etiqueta *coordinado* solo con ≥2 señales
independientes sobre el mismo conjunto de cuentas.

### 3.1 Ráfagas de texto idéntico (ventana {{ tw }} min, ≥{{ ta }} cuentas)
{% if text_bursts %}{% for b in text_bursts[:15] %}- {{ b.start }} · {{ b.n_accounts }} cuentas · {{ b.n_posts }} posts · «{{ b.key[:80] }}…»
{% endfor %}{% else %}Sin ráfagas detectadas.{% endif %}

### 3.2 Ráfagas de URL (ventana 30 min, ≥3 cuentas)
{% if url_bursts %}{% for b in url_bursts[:15] %}- {{ b.start }} · {{ b.n_accounts }} cuentas · {{ b.key }}
{% endfor %}{% else %}Sin ráfagas detectadas.{% endif %}

### 3.3 Near-duplicates de texto (Jaccard ≥ 0.7): {{ n_dupes }} pares entre cuentas distintas
### 3.4 Media re-subida (pHash ≤ 8): {{ n_media_dupes }} pares
### 3.5 Cuentas creadas en lote
{% if creation_clusters %}{% for c in creation_clusters %}- {{ c.platform }} · {{ c.bucket }} · {{ c.n }} cuentas
{% endfor %}{% else %}Sin datos de creación suficientes.{% endif %}

## 4. Amplificación

Top cuentas por PageRank en el grafo (forward + mención + duplicado). {{ n_nodes }} nodos, {{ n_edges }} aristas, {{ n_comm }} comunidades.

| Cuenta | Plataforma | PageRank | Amplificada por | Amplifica | Posts | Comunidad | Semilla |
|---|---|---:|---:|---:|---:|---:|:-:|
{% for r in ranking %}| `{{ r.account }}` | {{ r.platform }} | {{ r.pagerank }} | {{ r.amplified_by }} | {{ r.amplifies }} | {{ r.posts }} | {{ r.community }} | {{ "●" if r.seed else "" }} |
{% endfor %}

Grafo exportado: `figures/graph.gexf` (Gephi).

## 5. Infraestructura

| Dominio | Registrador | Creado | País | Menciones |
|---|---|---|---|---:|
{% for d in domains %}| {{ d.domain }} | {{ d.registrar or "?" }} | {{ (d.created or "?")[:10] }} | {{ d.registrant_country or "?" }} | {{ d.n }} |
{% endfor %}

## 6. Limitaciones

- Solo fuentes abiertas; grupos privados y WhatsApp fuera de alcance.
- Léxico validado: {{ "sí" if lexicon_validated else "NO (pendiente hablante nativo)" }}.
- Los handles se muestran seudonimizados (HMAC). El mapa real se conserva fuera del informe.

## Anexo A. Evidencias

Cada publicación citada tiene ID nativo, captura bruta y SHA-256 en la base de datos de la campaña
(`faro evidence list --ids …`). Capturas: {{ n_raw }} ficheros bajo `raw/`.
""")


def build(camp: Campaign, out_dir: Path, lexicon_validated: bool = False) -> Path:
    from . import __version__
    data = campaign_data(camp.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    fig = out_dir / "figures"
    timeline.plot(camp, fig / "timeline.png")
    G = graph.build(camp)
    if G.number_of_nodes():
        graph.export(G, fig / "graph.gexf")
    ranking = graph.rank(G)
    n_comm = len(set(r["community"] for r in ranking)) if ranking else 0
    with connect(data) as con:
        corpus = [dict(r) for r in con.execute("""
            SELECT p.platform, COUNT(DISTINCT p.account) accounts, COUNT(*) posts,
                   (SELECT COUNT(*) FROM media m JOIN posts q ON q.id=m.post_id WHERE q.platform=p.platform) media,
                   (SELECT COUNT(*) FROM links l JOIN posts q ON q.id=l.post_id WHERE q.platform=p.platform) links
            FROM posts p GROUP BY p.platform""")]
        fl = con.execute("SELECT MIN(posted_at), MAX(posted_at) FROM posts").fetchone()
        domains = [dict(r) for r in con.execute("""
            SELECT d.domain, d.registrar, d.created, d.registrant_country, COUNT(l.id) n
            FROM domains d LEFT JOIN links l ON l.domain=d.domain GROUP BY d.domain ORDER BY n DESC LIMIT 30""")]
    vr = verify_raw(data)
    cc = bursts.account_creation_clusters(camp)
    md = TEMPLATE.render(
        camp=camp, version=__version__, now=utcnow(), first_post=fl[0], last_post=fl[1], corpus=corpus,
        ok_raw=sum(1 for _, ok in vr if ok), n_raw=len(vr),
        tw=10, ta=3, text_bursts=bursts.text_bursts(camp), url_bursts=bursts.url_bursts(camp),
        n_dupes=len(dupes.text_near_dupes(camp, write_edges=False)),
        n_media_dupes=len(dupes.media_near_dupes(camp, write_edges=False)),
        creation_clusters=cc.to_dict("records") if not cc.empty else [],
        ranking=ranking, n_nodes=G.number_of_nodes(), n_edges=G.number_of_edges(), n_comm=n_comm,
        domains=domains, lexicon_validated=lexicon_validated,
    )
    p = out_dir / "informe.md"
    p.write_text(md, encoding="utf-8")
    return p
