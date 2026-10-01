"""Sitio estático de la campaña: portada con hallazgos y cifras + cronología + grafo + informe + Tor.
Todo autónomo (sin CDN). Pensado para publicarse detrás de contraseña."""
from __future__ import annotations
import html
import json
import shutil
from pathlib import Path
import markdown
import yaml
from jinja2 import Template
from .campaign import Campaign
from .paths import campaign_dir, campaign_data
from .db import connect
from .evidence import utcnow, verify_raw
from . import __version__, report, report_timeline
from .analyze import evidence as evgraph, timeline as tl

CSS = """
:root{--ink:#0F1923;--g1:#CCCCCC;--g2:#999999;--blue:#0563C1;--soft:#f5f6f7}
*{box-sizing:border-box}html{min-height:100%}body{margin:0;min-height:100vh;font-family:Arial,Helvetica,sans-serif;color:var(--ink);background:#fff;line-height:1.45;display:flex;flex-direction:column}
header{border-bottom:1px solid var(--g1);padding:0 22px;display:flex;align-items:center;gap:26px;height:54px;flex-wrap:wrap}
header .brand{font-weight:700;font-size:15px}header .brand small{color:var(--g2);font-weight:400;margin-left:8px}
nav a{color:var(--ink);text-decoration:none;font-size:13px;padding:17px 2px;display:inline-block;border-bottom:2px solid transparent;margin-right:16px}
nav a.on{border-color:var(--ink)}nav a:hover{color:var(--blue)}
main{flex:1;min-height:0}.page{max-width:1100px;margin:0 auto;padding:28px 22px 60px}
iframe.full{width:100%;height:calc(100vh - 54px - 30px);border:0;display:block}
h1{font-size:22px;margin:0 0 6px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--g2);margin:30px 0 12px}
.sub{color:var(--g2);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpi{border:1px solid var(--g1);border-radius:8px;padding:12px 14px}.kpi b{display:block;font-size:24px}.kpi span{font-size:12px;color:var(--g2)}
.finding{border-top:1px solid var(--g1);padding:16px 0;display:grid;grid-template-columns:44px 1fr;gap:12px}
.finding .n{font-size:20px;font-weight:700;color:var(--g2)}.finding h3{margin:0 0 6px;font-size:16px}.finding p{margin:0 0 6px;max-width:78ch}
.finding .ev{font-size:12px;color:var(--g2)}.tag{font-size:11px;border:1px solid var(--g1);border-radius:4px;padding:1px 7px;margin-left:8px;vertical-align:middle}
.tag.alta{border-color:#2e7d32;color:#2e7d32}.tag.media{border-color:#7a5c00;color:#7a5c00}.tag.hipótesis{border-color:#8a1c1c;color:#8a1c1c}
.md{max-width:860px}.md table{border-collapse:collapse;font-size:13px;display:block;overflow-x:auto}.md th,.md td{border:1px solid var(--g1);padding:5px 8px;text-align:left}.md img{max-width:100%}.md h1{font-size:20px}.md h2{font-size:16px;text-transform:none;letter-spacing:0;color:var(--ink);margin-top:28px}.md h3{font-size:14px}.md code{background:var(--soft);padding:1px 4px;border-radius:3px;font-size:12px}
footer{border-top:1px solid var(--g1);padding:8px 22px;font-size:11px;color:var(--g2)}
.next{list-style:none;padding:0;margin:0}.next li{padding:6px 0;border-top:1px dashed var(--g1);font-size:14px;display:grid;grid-template-columns:96px 1fr;gap:10px}.next li .d{font-family:monospace;font-size:12px;color:var(--g2)}
@media(max-width:640px){.finding{grid-template-columns:1fr}}
"""

LAYOUT = Template("""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>{{ title }} · Operación Faro</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex,nofollow"><style>{{ css }}</style></head>
<body><header><div class="brand">Operación Faro<small>{{ camp.name }}</small></div>
<nav>{% for href, label in nav %}<a href="{{ href }}" class="{{ 'on' if href == current else '' }}">{{ label }}</a>{% endfor %}</nav></header>
<main>{{ body }}</main>
<footer>Don Z · faro {{ version }} · generado {{ now }} · Cuentas seudonimizadas; cada afirmación remite a evidencia con hash o a fuente pública. Acceso restringido.</footer>
</body></html>""")

INDEX = Template("""<div class="page">
<h1>{{ camp.title }}</h1>
<div class="sub">{{ camp.description }}</div>
<h2>Estado del corpus</h2>
<div class="grid">
{% for k in kpis %}<div class="kpi"><b>{{ k.value }}</b><span>{{ k.label }}</span></div>{% endfor %}
</div>
<h2>Hallazgos</h2>
{% for f in findings %}<div class="finding"><div class="n">{{ '%02d' % f.id }}</div><div>
<h3>{{ f.title }}<span class="tag {{ f.confidence }}">{{ f.confidence }}</span></h3>
<p>{{ f.text }}</p><div class="ev">Evidencia: {{ f.evidence }} · {{ f.date }}</div></div></div>
{% endfor %}
<h2>Próximos hitos</h2>
<ul class="next">{% for m in upcoming %}<li><span class="d">{{ m.date }}</span><span>{{ m.label }}</span></li>{% endfor %}</ul>
<h2>Vistas</h2>
<ul class="next">
<li><span class="d">cronología</span><span><a href="timeline.html">Línea temporal</a>: volumen diario, hitos con fuente y lectura por tramos de qué se incita.</span></li>
<li><span class="d">grafo</span><span><a href="graph.html">Grafo de evidencia</a>: cuentas, publicaciones, dominios, términos, fechas y entidades documentadas, con la procedencia en cada arista.</span></li>
<li><span class="d">informe</span><span><a href="informe.html">Informe técnico</a> generado desde la base de datos (señales de coordinación, amplificación, infraestructura, integridad).</span></li>
{% if has_onion %}<li><span class="d">tor</span><span><a href="tor.html">Capa Tor</a>: resumen asistido de lo capturado en .onion.</span></li>{% endif %}
<li><span class="d">datos</span><span><a href="graph/evidence.gexf">GEXF</a> · <a href="graph/evidence.graphml">GraphML</a> · <a href="graph/evidence.json">JSON</a> · <a href="timeline.md">cronología.md</a> · <a href="informe.md">informe.md</a></span></li>
</ul>
</div>""")


def build(camp: Campaign, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    now = utcnow()
    nav = [("index.html", "Resumen"), ("timeline.html", "Cronología"), ("graph.html", "Grafo"), ("informe.html", "Informe")]
    # Piezas
    rep_md = report.build(camp, out, lexicon_validated=False)          # informe.md + figures/
    tl_paths = report_timeline.build(camp, out / "_tl")                 # timeline.html/md
    G = evgraph.build(camp)
    evgraph.export(G, out / "graph")                                    # evidence.html/gexf/graphml/json
    shutil.copy(tl_paths["md"], out / "timeline.md")
    (out / "timeline_view.html").write_text(tl_paths["html"].read_text(encoding="utf-8"), encoding="utf-8")
    shutil.rmtree(out / "_tl", ignore_errors=True)
    onion_summary = Path.cwd() / "reports" / camp.name / "onion-summary.md"
    has_onion = onion_summary.exists()
    if has_onion:
        nav.append(("tor.html", "Tor"))
        shutil.copy(onion_summary, out / "onion-summary.md")

    def page(name: str, title: str, body: str) -> None:
        (out / name).write_text(LAYOUT.render(title=title, camp=camp, nav=nav, current=name, body=body, css=CSS, version=__version__, now=now), encoding="utf-8")

    # KPIs
    data = campaign_data(camp.name)
    with connect(data) as con:
        per = {r[0]: r[1] for r in con.execute("SELECT platform, COUNT(*) FROM posts GROUP BY platform")}
        n_acc = con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        n_dom = con.execute("SELECT COUNT(*) FROM domains").fetchone()[0]
        first, last = con.execute("SELECT MIN(posted_at), MAX(posted_at) FROM posts").fetchone()
    vr = verify_raw(data)
    kpis = [{"value": sum(per.values()), "label": "publicaciones (" + ", ".join(f"{k} {v}" for k, v in sorted(per.items())) + ")"},
            {"value": n_acc, "label": "cuentas seudonimizadas"}, {"value": n_dom, "label": "dominios"},
            {"value": f"{sum(1 for _, ok in vr if ok)}/{len(vr)}", "label": "capturas con hash verificado"},
            {"value": G.number_of_nodes(), "label": f"nodos en el grafo · {G.number_of_edges()} aristas"},
            {"value": (first or "")[:10], "label": f"primera publicación · última {(last or '')[:10]}"}]
    fy = campaign_dir(camp.name) / "findings.yml"
    findings = (yaml.safe_load(fy.read_text(encoding="utf-8")) or {}).get("findings", []) if fy.exists() else []
    spec = report_timeline.load(camp)
    today = now[:10]
    upcoming = [m for m in sorted(spec.get("milestones", []), key=lambda m: str(m["date"])) if str(m["date"]) >= today]
    for m in upcoming:
        m["date"] = str(m["date"])
    page("index.html", "Resumen", INDEX.render(camp=camp, kpis=kpis, findings=findings, upcoming=upcoming, has_onion=has_onion))
    page("timeline.html", "Cronología", '<iframe class="full" src="timeline_view.html" title="Cronología"></iframe>')
    page("graph.html", "Grafo", '<iframe class="full" src="graph/evidence.html" title="Grafo de evidencia"></iframe>')
    md_html = markdown.markdown(rep_md.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"])
    page("informe.html", "Informe", f'<div class="page md">{md_html}</div>')
    if has_onion:
        page("tor.html", "Tor", '<div class="page md">' + markdown.markdown(onion_summary.read_text(encoding="utf-8"), extensions=["tables"]) + "</div>")
    return out / "index.html"
